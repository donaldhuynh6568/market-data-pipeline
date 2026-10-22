from __future__ import annotations

import argparse
from dataclasses import dataclass

import numpy as np
import pandas as pd

from market_data_pipeline.config import DATA_DIR
from market_data_pipeline.transforms.swing_structure import find_swing_points

LEVELS_FILENAME = "location_levels.parquet"
ZONES_FILENAME = "location_zones.parquet"

DEFAULT_LOOKBACK_DAYS = 25
# NOTE: reference/Location Confluence Playbook.md's "formal 3-candle swing" (sources 7/8) is a 1-bar-each-side
# rule (a candle whose high/low beats only its immediate neighbor) - stricter than this project's window, which
# requires 3 bars each side. The doc's definition fires on far more, less significant pivots. Deliberately not
# switched to match it: that changes the dealing range, smooth/jagged reads and every backtested zone at once,
# and nothing has measured whether the looser definition helps or hurts. Treat as a documented difference, not
# a bug, until it's been through the same measure-before-trust process as the rest of this file.
DEFAULT_SWING_WINDOW = 3
DEFAULT_ATR_WINDOW = 14
DEFAULT_DISPLACEMENT_MULT = 0.8
DEFAULT_MAX_PULLBACK_PCT = 0.15
# Bars on each side a displacement candle must break through ("a fast, directional run through a prior high/low").
DISPLACEMENT_BREAK_BARS = 5

DEFAULT_VOLUME_BASELINE_WINDOW = 20
DEFAULT_VOLUME_CONFIRM_MULT = 1.5

# Zone quality from the two independent qualifiers, best first.
STRONG = "STRONG"
CONFIRMED = "CONFIRMED"
PLAIN = "PLAIN"
QUALITY_RANK = {STRONG: 0, CONFIRMED: 1, PLAIN: 2}

ZONE_COLUMNS = [
    "symbol", "kind", "formed_at", "zone_low", "zone_high", "candle1_stop",
    "has_displacement", "volume_ratio", "has_volume_confirmation", "zone_quality",
    "filled", "zone_status", "spot", "distance_pct",
]

# Body-vs-wick respect test (reference/Location Confluence Playbook.md, source 6): a wick is allowed to pierce
# the zone's far half once price has traded back into it, but once a candle's BODY closes past the midpoint,
# the zone is read as failed - an earlier, softer signal than `filled`, which needs price to fully clear the
# far edge. UNTESTED = price has not traded back into the zone at all since it formed.
ZONE_UNTESTED = "UNTESTED"
ZONE_RESPECTED = "RESPECTED"
ZONE_FAILED = "FAILED"
ZONE_FILLED = "FILLED"

INSIDE_DAY_CLASSIC = "CLASSIC"
INSIDE_DAY_BODY_ONLY = "BODY_ONLY"


@dataclass(frozen=True)
class LocationConfig:
    lookback_days: int = DEFAULT_LOOKBACK_DAYS
    swing_window: int = DEFAULT_SWING_WINDOW
    atr_window: int = DEFAULT_ATR_WINDOW
    displacement_atr_mult: float = DEFAULT_DISPLACEMENT_MULT
    max_pullback_pct: float = DEFAULT_MAX_PULLBACK_PCT
    volume_baseline_window: int = DEFAULT_VOLUME_BASELINE_WINDOW
    volume_confirm_mult: float = DEFAULT_VOLUME_CONFIRM_MULT


def location_config_from_rules(rules: dict) -> LocationConfig:
    section = rules.get("location", {})
    allowed = {f for f in LocationConfig.__dataclass_fields__}
    return LocationConfig(**{k: v for k, v in section.items() if k in allowed})


def average_true_range(high: np.ndarray, low: np.ndarray, close: np.ndarray, window: int) -> float:
    """Wilder's true range averaged over `window` bars; NaN when there is not enough history."""
    if len(high) < 2:
        return float("nan")
    prior_close = close[:-1]
    true_range = np.maximum(
        high[1:] - low[1:],
        np.maximum(np.abs(high[1:] - prior_close), np.abs(low[1:] - prior_close)),
    )
    if len(true_range) < window:
        return float("nan")
    return float(true_range[-window:].mean())


def _has_displacement(
    index: int,
    kind: str,
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    atr: float,
    mult: float,
) -> bool:
    """The playbook's common-gap filter: a big directional body that breaks the recent extreme.

    Without it any slow drift leaves three-candle gaps behind; with it the gap is evidence that price
    actually ran through prior liquidity.
    """
    if not np.isfinite(atr) or atr <= 0:
        return False
    body = abs(close[index] - open_[index])
    if body <= mult * atr:
        return False
    start = max(0, index - DISPLACEMENT_BREAK_BARS)
    if start == index:
        return False
    if kind == "bull":
        return high[index] > high[start:index].max()
    return low[index] < low[start:index].min()


def volume_ratio_at(volume: np.ndarray | None, index: int, window: int) -> float:
    """The imbalance candle's volume against its own trailing baseline.

    The baseline excludes the candle itself (same convention as transforms/volume_floor.py), otherwise a
    spike partly absorbs itself into the average it is being measured against. NaN until there is enough
    history, or when the bars carry no volume at all.
    """
    if volume is None or index < window:
        return float("nan")
    baseline = volume[index - window : index].mean()
    if not np.isfinite(baseline) or baseline <= 0:
        return float("nan")
    return float(volume[index] / baseline)


def zone_status_from_history(
    kind: str, zone_low: float, zone_high: float, filled: bool, after_high: np.ndarray, after_low: np.ndarray,
    after_close: np.ndarray,
) -> str:
    """Current standing of one zone, from every bar on record after it formed (a "now" read, for the live report;
    see `weak_entry_close` in transforms/backtest.py for the point-in-time version a backtest needs).

    `after_*` are the same post-formation slices `filled` is computed from, so this stays consistent with it.
    """
    if filled:
        return ZONE_FILLED
    midpoint = (zone_low + zone_high) / 2
    entered_mask = after_low <= zone_high if kind == "bull" else after_high >= zone_low
    if not entered_mask.any():
        return ZONE_UNTESTED
    since_entry = after_close[int(np.argmax(entered_mask)) :]
    breached = (since_entry < midpoint).any() if kind == "bull" else (since_entry > midpoint).any()
    return ZONE_FAILED if breached else ZONE_RESPECTED


def inside_day_type(bars: pd.DataFrame) -> str | None:
    """Is the most recent bar fully (classic) or just its body (body_only) inside the prior bar's range?

    Read as compression ahead of an expansion move (reference doc, sources 7/8); informational only - which
    direction the expansion takes is read from the zone/momentum picture elsewhere, not guessed here.
    """
    ordered = bars.sort_values("timestamp").reset_index(drop=True)
    if len(ordered) < 2:
        return None
    prev, last = ordered.iloc[-2], ordered.iloc[-1]
    prev_low, prev_high = float(prev["low"]), float(prev["high"])
    if float(last["high"]) <= prev_high and float(last["low"]) >= prev_low:
        return INSIDE_DAY_CLASSIC
    body_low, body_high = sorted((float(last["open"]), float(last["close"])))
    if body_high <= prev_high and body_low >= prev_low:
        return INSIDE_DAY_BODY_ONLY
    return None


def grade_zone(has_displacement: bool, has_volume_confirmation: bool) -> str:
    """Price displacement and a volume surge are independent evidence; either qualifies a zone, both is best."""
    if has_displacement and has_volume_confirmation:
        return STRONG
    if has_displacement or has_volume_confirmation:
        return CONFIRMED
    return PLAIN


def find_fvgs(bars: pd.DataFrame, config: LocationConfig = LocationConfig()) -> pd.DataFrame:
    """Three-candle fair value gaps in the last `lookback_days` bars of one symbol's daily history.

    A bullish gap is candle 1's high below candle 3's low (candle 2's range does not overlap either);
    the stop is candle 1's low, not the gap's own edge. Bearish is the mirror. A zone is `filled` once a
    later bar trades fully through it; a bar that only dips into it leaves the zone live, since the
    playbook treats unfilled gaps as standing reference levels.
    """
    ordered = bars.sort_values("timestamp").reset_index(drop=True)
    open_ = ordered["open"].astype(float).to_numpy()
    high = ordered["high"].astype(float).to_numpy()
    low = ordered["low"].astype(float).to_numpy()
    close = ordered["close"].astype(float).to_numpy()
    timestamps = ordered["timestamp"].to_numpy()
    n = len(ordered)
    if n < 3:
        return pd.DataFrame(columns=ZONE_COLUMNS)

    atr = average_true_range(high, low, close, config.atr_window)
    volume = ordered["volume"].astype(float).to_numpy() if "volume" in ordered.columns else None
    spot = close[-1]
    rows = []
    for i in range(max(1, n - config.lookback_days), n - 1):
        after_high, after_low, after_close = high[i + 2 :], low[i + 2 :], close[i + 2 :]
        for kind in ("bull", "bear"):
            if kind == "bull" and high[i - 1] < low[i + 1]:
                zone_low, zone_high, stop = high[i - 1], low[i + 1], low[i - 1]
                filled = bool((after_low < zone_low).any())
            elif kind == "bear" and low[i - 1] > high[i + 1]:
                zone_low, zone_high, stop = high[i + 1], low[i - 1], high[i - 1]
                filled = bool((after_high > zone_high).any())
            else:
                continue
            has_displacement = _has_displacement(
                i, kind, open_, high, low, close, atr, config.displacement_atr_mult
            )
            ratio = volume_ratio_at(volume, i, config.volume_baseline_window)
            has_volume = bool(np.isfinite(ratio) and ratio >= config.volume_confirm_mult)
            rows.append(
                {
                    "symbol": ordered["symbol"].iloc[-1],
                    "kind": kind,
                    "formed_at": pd.Timestamp(timestamps[i]),
                    "zone_low": float(zone_low),
                    "zone_high": float(zone_high),
                    "candle1_stop": float(stop),
                    "has_displacement": has_displacement,
                    "volume_ratio": ratio,
                    "has_volume_confirmation": has_volume,
                    "zone_quality": grade_zone(has_displacement, has_volume),
                    "filled": filled,
                    "zone_status": zone_status_from_history(
                        kind, zone_low, zone_high, filled, after_high, after_low, after_close
                    ),
                    "spot": float(spot),
                    "distance_pct": float(zone_high / spot - 1) if kind == "bull" else float(zone_low / spot - 1),
                }
            )
    return pd.DataFrame(rows, columns=ZONE_COLUMNS)


def quadrants(range_low: float, range_high: float) -> dict:
    """Quadrant grading of a range: CE is the 50% midpoint, OTE the 3/4 completion reference."""
    height = range_high - range_low
    return {
        "range_low": range_low,
        "range_q1": range_low + 0.25 * height,
        "range_ce": range_low + 0.50 * height,
        "range_ote": range_low + 0.75 * height,
        "range_high": range_high,
        "range_height": height,
    }


def dealing_range(swings: pd.DataFrame) -> dict | None:
    """Last confirmed swing low and swing high, graded into quadrants. None when either is missing."""
    highs = swings[swings["kind"] == "swing_high"].sort_values("timestamp")
    lows = swings[swings["kind"] == "swing_low"].sort_values("timestamp")
    if highs.empty or lows.empty:
        return None
    low = float(lows["price"].iloc[-1])
    high = float(highs["price"].iloc[-1])
    if high <= low:
        return None
    return quadrants(low, high)


def classify_smooth_jagged(swings: pd.DataFrame, bars: pd.DataFrame) -> pd.DataFrame:
    """A swing level stays SMOOTH (untested liquidity, still a draw) until a later bar trades through it."""
    if swings.empty:
        return swings.assign(state=pd.Series(dtype=str))

    ordered = bars.sort_values("timestamp").reset_index(drop=True)
    states = []
    for _, point in swings.iterrows():
        later = ordered[ordered["timestamp"] > point["timestamp"]]
        if point["kind"] == "swing_high":
            swept = bool((later["high"].astype(float) > point["price"]).any())
        else:
            swept = bool((later["low"].astype(float) < point["price"]).any())
        states.append("JAGGED" if swept else "SMOOTH")
    return swings.assign(state=states)


def select_entry_zone(zones: pd.DataFrame, spot: float, config: LocationConfig) -> pd.Series | None:
    """The bullish zone a long would be entered from: unfilled, nearest below spot, best quality preferred.

    Quality (STRONG > CONFIRMED > PLAIN) is weighed only among zones within `max_pullback_pct` of spot, so a
    textbook zone 30% down never beats a workable one 2% down. When every zone is out of that band the
    nearest is still returned — the caller labels it EXTENDED rather than pretending there is no structure.
    """
    if zones.empty:
        return None
    candidates = zones[(zones["kind"] == "bull") & (~zones["filled"]) & (zones["zone_low"] < spot)]
    if candidates.empty:
        return None
    # distance_pct is <= 0 for zones below spot; a zone containing spot sits at distance > 0 and is always in band.
    in_band = candidates[
        (candidates["zone_high"] >= spot) | (candidates["distance_pct"] >= -config.max_pullback_pct)
    ]
    if in_band.empty:
        # Nothing reachable: fall back to the nearest zone on distance alone, quality is moot at that range.
        return candidates.sort_values(["zone_high", "formed_at"], ascending=[False, False]).iloc[0]

    ranked = in_band.assign(_rank=in_band["zone_quality"].map(QUALITY_RANK))
    best = ranked.sort_values(["_rank", "zone_high", "formed_at"], ascending=[True, False, False]).iloc[0]
    return best.drop(labels="_rank")


def nearest_resistance(zones: pd.DataFrame, spot: float) -> pd.Series | None:
    """Nearest unfilled bearish zone above spot — overhead supply, and the only level a SELL name gets."""
    if zones.empty:
        return None
    above = zones[(zones["kind"] == "bear") & (~zones["filled"]) & (zones["zone_low"] > spot)]
    if above.empty:
        return None
    return above.sort_values("zone_low").iloc[0]


def build_symbol_levels(bars: pd.DataFrame, config: LocationConfig = LocationConfig()) -> tuple[dict, pd.DataFrame]:
    """Per-symbol levels row plus that symbol's zone table."""
    ordered = bars.sort_values("timestamp").reset_index(drop=True)
    symbol = ordered["symbol"].iloc[-1]
    spot = float(ordered["close"].iloc[-1])
    high = ordered["high"].astype(float).to_numpy()
    low = ordered["low"].astype(float).to_numpy()
    close = ordered["close"].astype(float).to_numpy()
    atr = average_true_range(high, low, close, config.atr_window)

    zones = find_fvgs(ordered, config)
    swings = classify_smooth_jagged(find_swing_points(ordered, window=config.swing_window), ordered)
    row: dict = {
        "symbol": symbol,
        "as_of": ordered["timestamp"].iloc[-1],
        "spot": spot,
        "bars_available": len(ordered),
        "atr": atr,
        "atr_pct": atr / spot if spot else np.nan,
        "inside_day": inside_day_type(ordered),
    }

    ranges = dealing_range(swings)
    row.update(ranges or dict.fromkeys(["range_low", "range_q1", "range_ce", "range_ote", "range_high", "range_height"]))
    if ranges and ranges["range_height"]:
        row["spot_pos_in_range"] = (spot - ranges["range_low"]) / ranges["range_height"]
    else:
        row["spot_pos_in_range"] = np.nan

    entry_zone = select_entry_zone(zones, spot, config)
    if entry_zone is not None:
        entry = float(entry_zone["zone_high"])  # the near edge: enter at the edge, not a deeper fill at CE
        stop = float(entry_zone["candle1_stop"])
        row.update(
            {
                "entry": entry,
                "entry_zone_low": float(entry_zone["zone_low"]),
                "entry_zone_high": entry,
                "stop": stop,
                "risk_pct": (entry - stop) / entry if entry else np.nan,
                "entry_formed_at": entry_zone["formed_at"],
                "entry_has_displacement": bool(entry_zone["has_displacement"]),
                "entry_volume_ratio": float(entry_zone["volume_ratio"]),
                "entry_zone_quality": str(entry_zone["zone_quality"]),
                "entry_zone_status": str(entry_zone["zone_status"]),
            }
        )
    else:
        row.update(
            dict.fromkeys(
                ["entry", "entry_zone_low", "entry_zone_high", "stop", "risk_pct", "entry_formed_at",
                 "entry_volume_ratio", "entry_zone_quality", "entry_zone_status"]
            )
            | {"entry_has_displacement": False}
        )

    resistance = nearest_resistance(zones, spot)
    row["resistance_low"] = float(resistance["zone_low"]) if resistance is not None else np.nan
    row["resistance_high"] = float(resistance["zone_high"]) if resistance is not None else np.nan

    live = zones[~zones["filled"]] if not zones.empty else zones
    row["n_support_zones"] = int(((live["kind"] == "bull") & (live["zone_low"] < spot)).sum()) if not live.empty else 0
    row["n_resistance_zones"] = int(((live["kind"] == "bear") & (live["zone_low"] > spot)).sum()) if not live.empty else 0

    smooth_highs = swings[(swings["kind"] == "swing_high") & (swings["state"] == "SMOOTH") & (swings["price"] > spot)]
    row["nearest_smooth_high"] = float(smooth_highs["price"].min()) if not smooth_highs.empty else np.nan
    row["n_smooth_highs_above"] = int(len(smooth_highs))
    return row, zones


def build_levels(price_df: pd.DataFrame, config: LocationConfig = LocationConfig()) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(levels, zones) for every symbol in the daily price data."""
    if price_df.empty:
        return pd.DataFrame(), pd.DataFrame(columns=ZONE_COLUMNS)

    ordered = price_df.sort_values(["symbol", "timestamp"]).drop_duplicates(["symbol", "timestamp"], keep="last")
    rows, zone_frames = [], []
    for _, group in ordered.groupby("symbol", sort=True):
        row, zones = build_symbol_levels(group, config)
        rows.append(row)
        if not zones.empty:
            zone_frames.append(zones)

    levels = pd.DataFrame(rows)
    all_zones = pd.concat(zone_frames, ignore_index=True) if zone_frames else pd.DataFrame(columns=ZONE_COLUMNS)
    return levels, all_zones


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Derive dealing ranges, FVG zones and entry/stop levels from daily bars."
    )
    parser.add_argument("--lookback", type=int, default=None, help="Bars to scan for zones (default from rules file)")
    parser.add_argument("--window", type=int, default=None, help="Bars each side to confirm a swing point")
    args = parser.parse_args()

    from market_data_pipeline.picks import RULES_PATH, load_rules

    config = location_config_from_rules(load_rules(RULES_PATH))
    if args.lookback is not None:
        config = LocationConfig(**{**config.__dict__, "lookback_days": args.lookback})
    if args.window is not None:
        config = LocationConfig(**{**config.__dict__, "swing_window": args.window})

    price_path = DATA_DIR / "price_data.parquet"
    if not price_path.exists():
        raise SystemExit(
            f"Missing {price_path}. Run the price collector first: uv run collect_price_data.py --days 90"
        )

    levels, zones = build_levels(pd.read_parquet(price_path), config)
    levels.to_parquet(DATA_DIR / LEVELS_FILENAME, index=False)
    zones.to_parquet(DATA_DIR / ZONES_FILENAME, index=False)
    print(f"Saved {len(levels)} level rows and {len(zones)} zones to {DATA_DIR}")
    for _, row in levels.iterrows():
        if pd.isna(row["entry"]):
            print(f"  [{row['symbol']}] no entry zone (spot {row['spot']:.2f}, {row['n_resistance_zones']} above)")
        else:
            print(
                f"  [{row['symbol']}] entry {row['entry']:.2f} stop {row['stop']:.2f} "
                f"risk {row['risk_pct'] * 100:.1f}% (spot {row['spot']:.2f})"
            )


if __name__ == "__main__":
    main()
