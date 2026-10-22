from __future__ import annotations

import argparse
from dataclasses import dataclass

import numpy as np
import pandas as pd

from market_data_pipeline.config import DATA_DIR
from market_data_pipeline.transforms.location import (
    DEFAULT_SWING_WINDOW,
    LocationConfig,
    classify_smooth_jagged,
    dealing_range,
    find_fvgs,
    location_config_from_rules,
)
from market_data_pipeline.transforms.momentum import compute_score_series
from market_data_pipeline.transforms.swing_structure import find_swing_points

TRADES_FILENAME = "backtest_zone_trades.parquet"
SUMMARY_FILENAME = "backtest_summary.parquet"
TARGET_TRADES_FILENAME = "backtest_target_trades.parquet"
TARGET_SUMMARY_FILENAME = "backtest_target_summary.parquet"

# Reward multiples to resolve each trade against. 1R is the headline; the rest show whether a wider
# target rescues a weak entry or simply takes longer to lose.
R_MULTIPLES = (0.5, 1.0, 1.5, 2.0, 3.0)
MOMENTUM_HORIZONS = (5, 10, 20)
RANDOM_ENTRIES_PER_SYMBOL = 15
RANDOM_STOP_ATR = 1.5
MIN_BARS = 40

# The candidates reports/position_levels.select_target offers a live plan, minus GEX_WALL/OI_CLUSTER: those
# come from a same-day options snapshot with no history, so there is nothing to reconstruct at a past bar.
CE, OTE, RANGE_HIGH, SMOOTH_HIGH = "CE", "OTE", "RANGE_HIGH", "SMOOTH_HIGH"
# Matches reports/backtest_read.TARGET_CONTROL_VIEW - duplicated rather than imported for the same reason as
# _nearest_target_above below (transforms/ does not depend on reports/).
TARGET_CONTROL_VIEW = "Control (to target)"


@dataclass(frozen=True)
class BacktestConfig:
    lookback_days: int = 0  # 0 means scan every bar available; a fixed number silently truncates as history grows
    atr_window: int = 14
    swing_window: int = DEFAULT_SWING_WINDOW
    seed: int = 0


def _atr_series(high: np.ndarray, low: np.ndarray, close: np.ndarray, window: int) -> np.ndarray:
    prior_close = np.concatenate([[np.nan], close[:-1]])
    true_range = np.maximum(high - low, np.maximum(np.abs(high - prior_close), np.abs(low - prior_close)))
    return pd.Series(true_range).rolling(window).mean().to_numpy()


def resolve_to_level(
    high: np.ndarray, low: np.ndarray, start: int, stop: float, target: float
) -> tuple[bool | None, int]:
    """Walk forward from `start`: did `target` arrive before `stop`? None if still open at the end.

    The stop is checked first within a bar, so a bar that spans both counts as a loss. Daily bars cannot
    say which came first intraday, and assuming the good outcome is how a backtest flatters itself.
    """
    for j in range(start, len(high)):
        if low[j] <= stop:
            return False, j - start
        if high[j] >= target:
            return True, j - start
    return None, len(high) - start


def resolve_trade(
    high: np.ndarray, low: np.ndarray, start: int, entry: float, stop: float, reward: float
) -> tuple[bool | None, int]:
    """Same walk as `resolve_to_level`, for a synthetic reward multiple instead of a real price level."""
    return resolve_to_level(high, low, start, stop, entry + reward * (entry - stop))


def zone_trades(bars: pd.DataFrame, config: BacktestConfig = BacktestConfig()) -> pd.DataFrame:
    """Every bullish zone in this symbol's history, with the features known at entry and the outcome.

    Only features derivable from price are recorded: the options-derived layers (GEX regime, walls) are a
    daily snapshot with no history, so they cannot be tested here.
    """
    ordered = bars.sort_values("timestamp").reset_index(drop=True)
    if len(ordered) < MIN_BARS:
        return pd.DataFrame()

    high = ordered["high"].astype(float).to_numpy()
    low = ordered["low"].astype(float).to_numpy()
    close = ordered["close"].astype(float).to_numpy()
    atr = _atr_series(high, low, close, config.atr_window)
    asset_class = ordered["asset_class"].iloc[-1] if "asset_class" in ordered.columns else "equity"
    scores = compute_score_series(pd.Series(close), 365 if asset_class == "crypto" else 252)["score"].to_numpy()
    position = {pd.Timestamp(t): i for i, t in enumerate(ordered["timestamp"])}

    lookback = config.lookback_days or len(ordered)
    zones = find_fvgs(ordered, LocationConfig(lookback_days=lookback, atr_window=config.atr_window))
    rows = []
    for _, zone in zones[zones["kind"] == "bull"].iterrows():
        formed = position.get(pd.Timestamp(zone["formed_at"]))
        entry, stop = float(zone["zone_high"]), float(zone["candle1_stop"])
        risk = entry - stop
        if formed is None or risk <= 0 or not np.isfinite(atr[formed]) or atr[formed] <= 0:
            continue
        # The entry triggers the first time price trades back down into the near edge.
        trigger = next((j for j in range(formed + 2, len(ordered)) if low[j] <= entry), None)
        if trigger is None:
            continue

        # Point-in-time version of location.py's body-vs-wick respect test. No bar strictly before the trigger
        # can qualify: the trigger is defined as the FIRST bar whose low reaches the zone, so every earlier bar
        # closed above the zone entirely. The earliest a body-breach can show up is the trigger bar itself - a
        # wick that reaches the near edge (triggering the fill) while the candle's own close already sits below
        # the zone's midpoint. That is same-day information (the trigger bar's own close), not a future bar, so
        # it stays free of lookahead.
        midpoint = (float(zone["zone_low"]) + entry) / 2
        weak_entry_close = bool(close[trigger] < midpoint)

        row = {
            "symbol": ordered["symbol"].iloc[-1],
            "formed_at": pd.Timestamp(zone["formed_at"]),
            "entered_at": ordered["timestamp"].iloc[trigger],
            "entry": entry,
            "stop": stop,
            "risk_pct": risk / entry,
            "stop_atr": risk / atr[formed],
            "zone_quality": zone["zone_quality"],
            "has_displacement": bool(zone["has_displacement"]),
            "weak_entry_close": weak_entry_close,
            "volume_ratio": zone["volume_ratio"],
            "score_at_entry": scores[trigger] if trigger < len(scores) else np.nan,
            "bars_to_trigger": trigger - formed,
        }
        for reward in R_MULTIPLES:
            won, held = resolve_trade(high, low, trigger, entry, stop, reward)
            row[f"win_{reward:g}r"] = won
            row[f"bars_{reward:g}r"] = held
        # Excursions over the life of the trade only - up to and including the bar that resolved it at 1R.
        # Measuring to the end of the data instead would count moves that happened long after the stop was hit.
        held = row["bars_1r"]
        end = trigger + (held if np.isfinite(held) else 0) + 1
        during_high, during_low = high[trigger:end], low[trigger:end]
        row["mfe_r"] = (during_high.max() - entry) / risk if len(during_high) else np.nan
        row["mae_r"] = (entry - during_low.min()) / risk if len(during_low) else np.nan
        rows.append(row)
    return pd.DataFrame(rows)


def _nearest_target_above(floor: float, candidates: list[tuple[str, float | None]]) -> tuple[str | None, float | None]:
    """Mirrors reports/position_levels.select_target's "nearest candidate above floor" rule.

    Duplicated rather than imported: transforms/ does not depend on reports/, and this is five lines.
    """
    above = [
        (label, value) for label, value in candidates
        if value is not None and np.isfinite(value) and float(value) > floor
    ]
    if not above:
        return None, None
    label, value = min(above, key=lambda item: float(item[1]))
    return label, float(value)


def _target_as_of(
    ordered: pd.DataFrame, swings_with_idx: pd.DataFrame, upto: int, entry: float, swing_window: int
) -> tuple[str | None, float | None]:
    """CE/OTE/range-high/nearest-smooth-high as reports/position_levels.build_plan would have read them at the
    moment of `upto` (inclusive) - computed only from bars on record by then.

    `swings_with_idx` is `find_swing_points` run ONCE over the whole symbol, with each swing's own bar index
    attached. A swing confirmed at index i only ever needs bars i-window..i+window to confirm (see
    swing_structure.find_swing_points): nothing past `upto` can change whether it shows, so the point-in-time
    set is a cheap filter (`i + window <= upto`) rather than re-running the swing scan per trade.
    """
    known = swings_with_idx[swings_with_idx["_idx"] + swing_window <= upto]
    candidates: list[tuple[str, float | None]] = []
    ranges = dealing_range(known)
    if ranges:
        candidates += [(CE, ranges["range_ce"]), (OTE, ranges["range_ote"]), (RANGE_HIGH, ranges["range_high"])]
    history = ordered.iloc[: upto + 1]
    classified = classify_smooth_jagged(known.drop(columns="_idx"), history)
    smooth_highs = classified[
        (classified["kind"] == "swing_high") & (classified["state"] == "SMOOTH") & (classified["price"] > entry)
    ]
    if not smooth_highs.empty:
        candidates.append((SMOOTH_HIGH, float(smooth_highs["price"].min())))
    spot = float(history["close"].iloc[-1])
    return _nearest_target_above(max(entry, spot), candidates)


def target_trades(bars: pd.DataFrame, config: BacktestConfig = BacktestConfig()) -> pd.DataFrame:
    """Every bullish zone, resolved against the real ICT target a live plan would have offered - CE, OTE, the
    dealing range high or the nearest smooth swing high, whichever sat nearest above price at the moment of
    entry - instead of a synthetic R-multiple. Answers the only question that actually matters for a position
    meant to be held to a key level: does price get there before the stop.

    GEX wall / OI-cluster targets are excluded: those come from a same-day options snapshot with no history,
    so they cannot be reconstructed as of a past bar. A trade with no ICT level above it at all (every
    candidate missing or already passed) is skipped - there is nothing for this test to measure.
    """
    ordered = bars.sort_values("timestamp").reset_index(drop=True)
    if len(ordered) < MIN_BARS:
        return pd.DataFrame()

    high = ordered["high"].astype(float).to_numpy()
    low = ordered["low"].astype(float).to_numpy()
    close = ordered["close"].astype(float).to_numpy()
    atr = _atr_series(high, low, close, config.atr_window)
    asset_class = ordered["asset_class"].iloc[-1] if "asset_class" in ordered.columns else "equity"
    scores = compute_score_series(pd.Series(close), 365 if asset_class == "crypto" else 252)["score"].to_numpy()
    position = {pd.Timestamp(t): i for i, t in enumerate(ordered["timestamp"])}

    swings = find_swing_points(ordered, window=config.swing_window)
    swings_with_idx = swings.assign(_idx=swings["timestamp"].map(position))

    lookback = config.lookback_days or len(ordered)
    zones = find_fvgs(ordered, LocationConfig(lookback_days=lookback, atr_window=config.atr_window))
    rows = []
    for _, zone in zones[zones["kind"] == "bull"].iterrows():
        formed = position.get(pd.Timestamp(zone["formed_at"]))
        entry, stop = float(zone["zone_high"]), float(zone["candle1_stop"])
        risk = entry - stop
        if formed is None or risk <= 0 or not np.isfinite(atr[formed]) or atr[formed] <= 0:
            continue
        trigger = next((j for j in range(formed + 2, len(ordered)) if low[j] <= entry), None)
        if trigger is None:
            continue

        target_label, target = _target_as_of(ordered, swings_with_idx, trigger, entry, config.swing_window)
        if target is None:
            continue

        # Same body-vs-wick respect test as zone_trades' weak_entry_close, computed the same point-in-time way.
        midpoint = (float(zone["zone_low"]) + entry) / 2
        weak_entry_close = bool(close[trigger] < midpoint)

        won, held = resolve_to_level(high, low, trigger, stop, target)
        rows.append(
            {
                "symbol": ordered["symbol"].iloc[-1],
                "formed_at": pd.Timestamp(zone["formed_at"]),
                "entered_at": ordered["timestamp"].iloc[trigger],
                "entry": entry,
                "stop": stop,
                "target": target,
                "target_label": target_label,
                "target_rr": (target - entry) / risk,
                "stop_atr": risk / atr[formed],
                "zone_quality": zone["zone_quality"],
                "weak_entry_close": weak_entry_close,
                "score_at_entry": scores[trigger] if trigger < len(scores) else np.nan,
                "won": won,
                "bars_held": held,
            }
        )
    return pd.DataFrame(rows)


def score_quintiles(scores: pd.Series) -> pd.Series:
    """Momentum quintiles labelled Q1 low..Q5 high.

    Falls back to however many clean bins pd.qcut's `duplicates="drop"` can actually produce when the sample
    is too small or too uniform for five - fixed labels on fewer bins than asked for raises ValueError rather
    than quietly misassigning them, so this has to adapt instead of passing `labels=` straight through.
    """
    labels = ["Q1 low", "Q2", "Q3", "Q4", "Q5 high"]
    bucketed = pd.qcut(scores, 5, duplicates="drop")
    if len(bucketed.cat.categories) == len(labels):
        return bucketed.cat.rename_categories(labels)
    return bucketed.cat.rename_categories([str(c) for c in bucketed.cat.categories])


def expectancy(wins: pd.Series, reward: float) -> float:
    """R per trade at a fixed reward multiple: win_rate * reward - loss_rate."""
    resolved = wins.dropna()
    if resolved.empty:
        return float("nan")
    win_rate = resolved.mean()
    return win_rate * reward - (1 - win_rate)


def summarize(trades: pd.DataFrame, by: str | list[str] | None = None, reward: float = 1.0) -> pd.DataFrame:
    """Win rate and expectancy at one reward multiple, overall or split by one feature or a cross-tab of several.

    A list of keys collapses into a single `bucket` label ("Q4 x PLAIN") so every view shares one output schema
    and can be stacked into the same summary table.
    """
    column = f"win_{reward:g}r"
    if trades.empty or column not in trades:
        return pd.DataFrame()

    def stats(group: pd.DataFrame) -> pd.Series:
        resolved = group[column].dropna()
        return pd.Series(
            {
                "trades": len(resolved),
                "win_rate": resolved.mean() if len(resolved) else np.nan,
                "expectancy_r": expectancy(group[column], reward),
                "median_mfe_r": group["mfe_r"].median(),
                "median_mae_r": group["mae_r"].median(),
            }
        )

    columns = ["bucket", "trades", "win_rate", "expectancy_r", "median_mfe_r", "median_mae_r"]
    if by is None:
        return stats(trades).to_frame().T.assign(bucket="ALL")[columns]

    keys = [by] if isinstance(by, str) else list(by)
    table = trades.groupby(keys, observed=True).apply(stats, include_groups=False).reset_index()
    table["bucket"] = table[keys].astype(str).agg(" x ".join, axis=1)
    return table[columns]


def summarize_target(trades: pd.DataFrame, by: str | list[str] | None = None) -> pd.DataFrame:
    """Win rate and expectancy for trades resolved against their own real ICT target.

    The reward varies trade to trade (whichever level was actually on offer), so unlike `summarize` this
    cannot use win_rate * one fixed reward - expectancy is the mean of each trade's own realized R: the
    target's reward multiple when it won, -1R when it was stopped out first.
    """
    if trades.empty or "won" not in trades:
        return pd.DataFrame()

    def stats(group: pd.DataFrame) -> pd.Series:
        resolved = group.dropna(subset=["won"])
        if resolved.empty:
            return pd.Series({"trades": 0, "win_rate": np.nan, "expectancy_r": np.nan, "median_target_rr": np.nan})
        won = resolved["won"].astype(bool)
        pnl = np.where(won, resolved["target_rr"].astype(float), -1.0)
        return pd.Series(
            {
                "trades": len(resolved),
                "win_rate": float(won.mean()),
                "expectancy_r": float(pnl.mean()),
                "median_target_rr": float(resolved["target_rr"].median()),
            }
        )

    columns = ["bucket", "trades", "win_rate", "expectancy_r", "median_target_rr"]
    if by is None:
        return stats(trades).to_frame().T.assign(bucket="ALL")[columns]

    keys = [by] if isinstance(by, str) else list(by)
    table = trades.groupby(keys, observed=True).apply(stats, include_groups=False).reset_index()
    table["bucket"] = table[keys].astype(str).agg(" x ".join, axis=1)
    return table[columns]


def momentum_forward_returns(bars: pd.DataFrame, horizons=MOMENTUM_HORIZONS) -> pd.DataFrame:
    """Forward returns by momentum-score tercile, on NON-overlapping windows only.

    Overlapping windows inflate the sample and manufacture signal that is not there; with roughly 60-90
    bars per name only a few hundred independent observations exist, and the table should say so.
    """
    rows = []
    for symbol, group in bars.groupby("symbol"):
        group = group.sort_values("timestamp").reset_index(drop=True)
        close = group["close"].astype(float)
        asset_class = group["asset_class"].iloc[-1] if "asset_class" in group.columns else "equity"
        scores = compute_score_series(close, 365 if asset_class == "crypto" else 252)["score"]
        for horizon in horizons:
            for i in range(0, len(close) - horizon, horizon):
                if np.isfinite(scores.iloc[i]):
                    rows.append(
                        {
                            "symbol": symbol,
                            "horizon": horizon,
                            "score": scores.iloc[i],
                            "forward_return": close.iloc[i + horizon] / close.iloc[i] - 1,
                        }
                    )
    observations = pd.DataFrame(rows)
    if observations.empty:
        return observations

    out = []
    for horizon, group in observations.groupby("horizon"):
        group = group.copy()
        group["bucket"] = pd.qcut(group["score"], 3, labels=["low", "mid", "high"], duplicates="drop")
        table = group.groupby("bucket", observed=True)["forward_return"].agg(
            observations="size", mean_return="mean", win_rate=lambda x: (x > 0).mean()
        )
        table["horizon"] = horizon
        table["correlation"] = group["score"].corr(group["forward_return"])
        out.append(table.reset_index())
    return pd.concat(out, ignore_index=True)


def random_control(
    bars: pd.DataFrame,
    stop_multiples: np.ndarray | None = None,
    config: BacktestConfig = BacktestConfig(),
    reward: float = 1.0,
    reward_multiples: np.ndarray | None = None,
) -> dict:
    """Random entry days with the same stop geometry - the bar any real signal has to clear.

    `stop_multiples` should be the stop widths the signal being tested actually used, sampled per trade. A fixed
    stop width is not a like-for-like comparison: tighter stops get hit more often whatever the entry, so a
    control that stops wider than the signal flatters the control and overstates the signal's failure.

    `reward_multiples`, when given, samples the reward the same way per trade instead of using the fixed
    `reward` for all of them - for comparing against a backtest whose reward varies trade to trade (the ICT
    target-exit test), where one fixed reward would be exactly the mismatch this already avoids for stops.
    Omitting it keeps the original fixed-reward behaviour (and its exact expectancy formula) unchanged.
    """
    rng = np.random.default_rng(config.seed)
    multiples = (
        np.asarray(stop_multiples, dtype=float) if stop_multiples is not None and len(stop_multiples)
        else np.array([RANDOM_STOP_ATR])
    )
    multiples = multiples[np.isfinite(multiples) & (multiples > 0)]
    if not len(multiples):
        multiples = np.array([RANDOM_STOP_ATR])

    rewards = (
        np.asarray(reward_multiples, dtype=float) if reward_multiples is not None and len(reward_multiples) else None
    )
    if rewards is not None:
        rewards = rewards[np.isfinite(rewards) & (rewards > 0)]
        rewards = rewards if len(rewards) else None

    pnl: list[float] = []
    for _, group in bars.groupby("symbol"):
        group = group.sort_values("timestamp").reset_index(drop=True)
        if len(group) < MIN_BARS:
            continue
        high = group["high"].astype(float).to_numpy()
        low = group["low"].astype(float).to_numpy()
        close = group["close"].astype(float).to_numpy()
        atr = _atr_series(high, low, close, config.atr_window)
        for _ in range(RANDOM_ENTRIES_PER_SYMBOL):
            i = int(rng.integers(config.atr_window + 5, len(close) - 5))
            if not np.isfinite(atr[i]) or atr[i] <= 0:
                continue
            entry = close[i]
            stop = entry - float(rng.choice(multiples)) * atr[i]
            trade_reward = float(rng.choice(rewards)) if rewards is not None else reward
            won, _ = resolve_trade(high, low, i + 1, entry, stop, trade_reward)
            if won is not None:
                pnl.append(trade_reward if won else -1.0)

    total = len(pnl)
    win_rate = float(np.mean([p > 0 for p in pnl])) if total else float("nan")
    result = {
        "trades": total,
        "win_rate": win_rate,
        "expectancy_r": float(np.mean(pnl)) if total else float("nan"),
        "median_stop_atr": float(np.median(multiples)),
    }
    if rewards is not None:
        result["median_reward"] = float(np.median(rewards))
    return result


def run_backtest(bars: pd.DataFrame, config: BacktestConfig = BacktestConfig()) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(per-trade table, stacked summary of every view) for the whole universe."""
    frames = [zone_trades(group, config) for _, group in bars.groupby("symbol")]
    trades = pd.concat([f for f in frames if not f.empty], ignore_index=True) if frames else pd.DataFrame()

    sections = []
    if not trades.empty:
        for reward in R_MULTIPLES:
            table = summarize(trades, reward=reward)
            table["view"] = f"All zones @ {reward:g}R"
            sections.append(table)
        for feature, label in (
            ("zone_quality", "Zone quality"),
            ("has_displacement", "Displacement"),
            ("weak_entry_close", "Weak close on entry bar"),
        ):
            table = summarize(trades, by=feature)
            table["view"] = f"{label} @ 1R"
            sections.append(table)
        # The coarse bands stay for this diagnostic sweep even though reports/daily_read.py no longer ranks off
        # it (ranking now comes from run_target_backtest below, which resolves against a real ICT level
        # instead of a fixed R-multiple) - quintile edges also shift with the data, so a live row could never
        # be assigned to one regardless of which scorecard it came from.
        banded = trades.assign(
            stop_band=pd.cut(trades["stop_atr"], [0, 1.0, 1.5, 2.5, np.inf], labels=["<1.0", "1.0-1.5", "1.5-2.5", ">2.5"]),
            score_band=pd.cut(trades["score_at_entry"], [-np.inf, 0, 0.2, np.inf], labels=["negative", "0-0.2", ">0.2"]),
            score_quintile=score_quintiles(trades["score_at_entry"]),
        )
        for feature, label in (
            ("stop_band", "Stop width (ATRs)"),
            ("score_band", "Momentum at entry"),
            ("score_quintile", "Momentum quintile"),
        ):
            table = summarize(banded, by=feature)
            table["view"] = f"{label} @ 1R"
            sections.append(table)
        # The view that decides whether zone quality earns anything once momentum is held fixed.
        crossed = summarize(banded, by=["score_quintile", "zone_quality"])
        crossed["view"] = "Momentum quintile x Zone quality @ 1R"
        sections.append(crossed)

    # Match the control's stop widths to the ones the zones actually used, so the comparison is like for like.
    stop_multiples = trades["stop_atr"].to_numpy() if not trades.empty else None
    control = random_control(bars, stop_multiples, config)
    median_stop = control.pop("median_stop_atr")
    sections.append(
        pd.DataFrame([{
            **control,
            "bucket": f"random entry, stops matched (median {median_stop:.2f} ATR)",
            "view": "Control @ 1R",
        }])
    )
    # Buckets come from strings, booleans and interval categories; parquet needs one consistent type.
    for table in sections:
        if "bucket" in table:
            table["bucket"] = table["bucket"].astype(str)
    summary = pd.concat(sections, ignore_index=True) if sections else pd.DataFrame()
    columns = ["view", "bucket", "trades", "win_rate", "expectancy_r", "median_mfe_r", "median_mae_r"]
    for column in columns:
        if column not in summary:
            summary[column] = np.nan
    return trades, summary[columns]


def run_target_backtest(bars: pd.DataFrame, config: BacktestConfig = BacktestConfig()) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(per-trade table, stacked summary) for the ICT target-exit backtest.

    Same zones as `run_backtest`, but resolved against the real level a live plan would actually have been
    holding for - CE, OTE, dealing-range high or the nearest smooth swing high - instead of a fixed R-multiple
    sweep. This is what reports/daily_read.py ranks on: momentum and the ICT read, nothing from risk sizing.
    """
    frames = [target_trades(group, config) for _, group in bars.groupby("symbol")]
    trades = pd.concat([f for f in frames if not f.empty], ignore_index=True) if frames else pd.DataFrame()

    sections = []
    if not trades.empty:
        overall = summarize_target(trades)
        overall["view"] = "All zones (to target)"
        sections.append(overall)

        banded = trades.assign(
            score_band=pd.cut(trades["score_at_entry"], [-np.inf, 0, 0.2, np.inf], labels=["negative", "0-0.2", ">0.2"]),
            score_quintile=score_quintiles(trades["score_at_entry"]),
        )
        table = summarize_target(banded, by="score_band")
        table["view"] = "Momentum at entry (to target)"
        sections.append(table)
        for feature, label in (
            ("zone_quality", "Zone quality (to target)"),
            ("weak_entry_close", "Weak close on entry (to target)"),
            ("target_label", "Target type (to target)"),
        ):
            table = summarize_target(trades, by=feature)
            table["view"] = label
            sections.append(table)
        # Same question run_backtest asks of its own R-multiple trades: does zone quality earn anything once
        # momentum is held fixed, now against the real exit rather than a synthetic one.
        crossed = summarize_target(banded, by=["score_quintile", "zone_quality"])
        crossed["view"] = "Momentum quintile x Zone quality (to target)"
        sections.append(crossed)

    # Matched on both stop AND reward this time: a fixed reward would reintroduce exactly the mismatch the
    # stop-matching already exists to avoid, since the real targets vary trade to trade.
    stop_multiples = trades["stop_atr"].to_numpy() if not trades.empty else None
    reward_multiples = trades["target_rr"].to_numpy() if not trades.empty else None
    control = random_control(bars, stop_multiples, config, reward_multiples=reward_multiples)
    median_stop = control.pop("median_stop_atr")
    median_reward = control.pop("median_reward", None)
    bucket = f"random entry, stops matched (median {median_stop:.2f} ATR)"
    if median_reward is not None:
        bucket += f", targets matched (median {median_reward:.2f}R)"
    sections.append(pd.DataFrame([{**control, "bucket": bucket, "view": TARGET_CONTROL_VIEW}]))

    for table in sections:
        if "bucket" in table:
            table["bucket"] = table["bucket"].astype(str)
    summary = pd.concat(sections, ignore_index=True) if sections else pd.DataFrame()
    columns = ["view", "bucket", "trades", "win_rate", "expectancy_r", "median_target_rr"]
    for column in columns:
        if column not in summary:
            summary[column] = np.nan
    return trades, summary[columns]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Score the ICT zone rule and the momentum ranking against the daily bars collected so far."
    )
    parser.add_argument("--lookback", type=int, default=None, help="Bars of history to scan for zones")
    args = parser.parse_args()

    from market_data_pipeline.picks import RULES_PATH, load_rules

    rules = load_rules(RULES_PATH)
    location = location_config_from_rules(rules)
    config = BacktestConfig(atr_window=location.atr_window)
    if args.lookback is not None:
        config = BacktestConfig(**{**config.__dict__, "lookback_days": args.lookback})

    price_path = DATA_DIR / "price_data.parquet"
    if not price_path.exists():
        raise SystemExit(f"Missing {price_path}. Run: uv run collect_price_data.py --days 90")

    bars = pd.read_parquet(price_path)
    trades, summary = run_backtest(bars, config)
    target_trades_df, target_summary = run_target_backtest(bars, config)
    momentum = momentum_forward_returns(bars)

    trades.to_parquet(DATA_DIR / TRADES_FILENAME, index=False)
    summary.assign(section="zones").to_parquet(DATA_DIR / SUMMARY_FILENAME, index=False)
    target_trades_df.to_parquet(DATA_DIR / TARGET_TRADES_FILENAME, index=False)
    target_summary.to_parquet(DATA_DIR / TARGET_SUMMARY_FILENAME, index=False)
    momentum.to_parquet(DATA_DIR / "backtest_momentum.parquet", index=False)

    span = f"{pd.to_datetime(bars['timestamp']).min().date()} to {pd.to_datetime(bars['timestamp']).max().date()}"
    print(f"Scored {len(trades)} zone entries across {bars['symbol'].nunique()} names ({span})")
    for view, group in summary.groupby("view", sort=False):
        print(f"\n  {view}")
        for _, row in group.iterrows():
            if pd.isna(row["win_rate"]):
                continue
            print(
                f"    {str(row['bucket']):<28} n={int(row['trades']):>4}  win {row['win_rate'] * 100:5.1f}%  "
                f"expectancy {row['expectancy_r']:+.3f}R"
            )
    print(f"\nResolved {len(target_trades_df)} of those against their real ICT target instead of a fixed R-multiple")
    for view, group in target_summary.groupby("view", sort=False):
        print(f"\n  {view}")
        for _, row in group.iterrows():
            if pd.isna(row["win_rate"]):
                continue
            print(
                f"    {str(row['bucket']):<28} n={int(row['trades']):>4}  win {row['win_rate'] * 100:5.1f}%  "
                f"expectancy {row['expectancy_r']:+.3f}R"
            )
    print("\n  Momentum, non-overlapping windows")
    for _, row in momentum.iterrows():
        print(
            f"    {row['horizon']:>2}d {str(row['bucket']):<6} n={int(row['observations']):>4}  "
            f"mean {row['mean_return'] * 100:+.2f}%  win {row['win_rate'] * 100:5.1f}%"
        )
    print(
        "\n  Read this as a sample, not a verdict: a few months of daily bars in one regime, with trades "
        "clustered by name and date, and no transaction costs."
    )


if __name__ == "__main__":
    main()
