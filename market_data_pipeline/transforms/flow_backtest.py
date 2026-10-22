from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from market_data_pipeline.config import DATA_DIR
from market_data_pipeline.reports import positioning as pos
from market_data_pipeline.transforms import flow_indicators as fi
from market_data_pipeline.transforms.momentum import compute_score_series

OUTPUT_FILENAME = "flow_backtest.parquet"
HORIZONS = (5, 10, 20)
WARMUP = 70                 # bars before the first observation: the slowest indicator needs 60 for its baseline
MIN_BARS = WARMUP + max(HORIZONS) + 1

# The daily indicators that can be read point-in-time. VWAP and the volume profile are built from the latest
# intraday bars only, so they are not backtested here.
LAYERS = ["CMF", "MFI", "OBV", "Rel. strength", "Trade size"]
VOTE_NAMES = {pos.AGREE: "agree", pos.NEUTRAL: "neutral", pos.DISAGREE: "against"}
COLUMNS = ["layer", "direction", "horizon", "vote", "observations", "mean_return", "win_rate"]


def symbol_votes(
    bars: pd.DataFrame, benchmark: pd.DataFrame | None, flow_config: fi.FlowConfig, pos_config: pos.PositioningConfig
) -> pd.DataFrame:
    """Every flow vote for every date of one symbol, using only data up to that date.

    The trade direction at each date is the sign of the momentum score (positive -> LONG candidate), standing in for
    the live screen, whose 4H structure is not available historically.
    """
    ordered = bars.sort_values("timestamp").reset_index(drop=True)
    close = ordered["close"].astype(float)
    ann = 365 if str(ordered["asset_class"].iloc[-1]) == "crypto" else 252
    score = compute_score_series(close, ann)["score"]

    cmf = fi.chaikin_money_flow(ordered, flow_config.cmf_window)
    mfi = fi.money_flow_index(ordered, flow_config.mfi_window)
    obv = fi.obv_divergence(ordered, flow_config.obv_lookback)
    rs = fi.relative_strength(ordered, benchmark, flow_config.rs_short)
    size = fi.avg_trade_size_ratio(ordered, flow_config.size_short, flow_config.size_long)
    ret5 = close.pct_change(flow_config.size_short)

    rows = []
    for i in range(WARMUP, len(ordered) - max(HORIZONS)):
        if not np.isfinite(score.iloc[i]) or score.iloc[i] == 0:
            continue
        direction = pos.LONG if score.iloc[i] > 0 else pos.SHORT
        votes = [
            pos.cmf_vote(direction, cmf.iloc[i], pos_config),
            pos.mfi_vote(direction, mfi.iloc[i], pos_config),
            pos.obv_vote(direction, obv.iloc[i]),
            pos.rs_vote(direction, rs.iloc[i], None, pos_config),
            pos.trade_size_vote(direction, size.iloc[i], ret5.iloc[i], pos_config),
        ]
        sign = 1.0 if direction == pos.LONG else -1.0
        row = {"symbol": ordered["symbol"].iloc[-1], "index": i, "direction": direction}
        for horizon in HORIZONS:
            row[f"ret_{horizon}"] = sign * (close.iloc[i + horizon] / close.iloc[i] - 1)
        for layer, vote in zip(LAYERS, votes):
            row[layer] = VOTE_NAMES.get(vote.vote)
        rows.append(row)
    return pd.DataFrame(rows)


def summarize(observations: pd.DataFrame) -> pd.DataFrame:
    """Direction-adjusted forward return by layer, direction and vote, on non-overlapping windows.

    Sampling every `horizon`-th date per symbol keeps the observations independent; overlapping windows would
    repeat the same move many times and manufacture significance. The ALL row is the baseline each vote has to beat.
    """
    if observations.empty:
        return pd.DataFrame(columns=COLUMNS)
    rows = []
    for horizon in HORIZONS:
        sampled = observations[observations["index"] % horizon == 0]
        for direction, group in sampled.groupby("direction"):
            returns = group[f"ret_{horizon}"]
            rows.append(("ALL", direction, horizon, "all", len(group), returns.mean(), (returns > 0).mean()))
            for layer in LAYERS:
                for vote, part in group.dropna(subset=[layer]).groupby(layer):
                    ret = part[f"ret_{horizon}"]
                    rows.append((layer, direction, horizon, vote, len(part), ret.mean(), (ret > 0).mean()))
    return pd.DataFrame(rows, columns=COLUMNS)


def build_backtest(
    daily: pd.DataFrame, flow_config: fi.FlowConfig = fi.FlowConfig(),
    pos_config: pos.PositioningConfig = pos.PositioningConfig(),
) -> pd.DataFrame:
    daily = daily.drop_duplicates(["symbol", "timestamp"], keep="last")
    by_symbol = {s: g for s, g in daily.groupby("symbol")}
    frames = []
    for symbol, bars in by_symbol.items():
        if len(bars) < MIN_BARS:
            continue
        bench_symbol = fi.benchmark_for(symbol, flow_config.benchmarks)
        frames.append(symbol_votes(bars, by_symbol.get(bench_symbol) if bench_symbol else None, flow_config, pos_config))
    frames = [f for f in frames if not f.empty]
    return summarize(pd.concat(frames, ignore_index=True)) if frames else pd.DataFrame(columns=COLUMNS)


def headline(table: pd.DataFrame, horizon: int = 10, min_obs: int = 30) -> list[str]:
    """One line per layer: mean direction-adjusted return when it agreed vs when it was against, and the baseline."""
    lines = []
    view = table[(table["horizon"] == horizon) & (table["observations"] >= 1)]
    for layer in LAYERS:
        parts = []
        for direction in (pos.LONG, pos.SHORT):
            part = view[(view["layer"] == layer) & (view["direction"] == direction)].set_index("vote")
            base = view[(view["layer"] == "ALL") & (view["direction"] == direction)]
            if not {"agree", "against"} <= set(part.index) or base.empty:
                continue
            a, c = part.loc["agree"], part.loc["against"]
            if min(a["observations"], c["observations"]) < min_obs:
                continue
            parts.append(
                f"{direction.lower()}: agree {a['mean_return'] * 100:+.2f}% (n={int(a['observations'])}) vs against "
                f"{c['mean_return'] * 100:+.2f}% (n={int(c['observations'])}), baseline {base['mean_return'].iloc[0] * 100:+.2f}%"
            )
        lines.append(f"{layer}: " + ("; ".join(parts) if parts else "too few observations"))
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest the daily flow votes against forward returns.")
    parser.parse_args()

    from market_data_pipeline.picks import RULES_PATH, load_rules
    from market_data_pipeline.reports.positioning import positioning_config_from_rules

    rules = load_rules(RULES_PATH)
    daily_path = DATA_DIR / fi.DAILY_FILENAME
    if not daily_path.exists():
        raise SystemExit(f"Missing {daily_path}. Run the price collector first.")
    table = build_backtest(
        pd.read_parquet(daily_path), fi.flow_config_from_rules(rules), positioning_config_from_rules(rules)
    )
    table.to_parquet(DATA_DIR / OUTPUT_FILENAME, index=False)
    print(f"Saved {len(table)} rows to {DATA_DIR / OUTPUT_FILENAME}")
    print("\n10-day forward return, direction-adjusted (non-overlapping windows):")
    print("\n".join(headline(table)))


if __name__ == "__main__":
    main()
