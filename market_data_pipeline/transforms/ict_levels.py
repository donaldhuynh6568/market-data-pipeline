from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import timedelta

import numpy as np
import pandas as pd

from market_data_pipeline.config import DATA_DIR
from market_data_pipeline.transforms.location import average_true_range
from market_data_pipeline.transforms.swing_structure import find_swing_points

LEVELS_FILENAME = "ict_levels.parquet"
PRICE_5M_FILENAME = "price_data_5m.parquet"
PRICE_1H_FILENAME = "price_data_1h.parquet"

NY = "America/New_York"
BAR_MINUTES = 5

DEFAULT_ATR_WINDOW = 14
# Monthly open needs the whole current month, plus enough days before it for a daily ATR.
DEFAULT_LOOKBACK_5M_DAYS = 45
DEFAULT_LOOKBACK_1H_DAYS = 15
DEFAULT_EQ_ATR_MULT = 0.10
DEFAULT_SWING_WINDOW = 3
DEFAULT_RVOL_WINDOW = 20
# A session needs this share of its 5m bars to count. Equities only trade 04:00-20:00 ET, so overnight sessions
# fall below it and read as "no data" rather than as a range built from the one extended-hours hour that exists.
DEFAULT_MIN_SESSION_COVERAGE = 0.5
# How far past the target time the first bar may sit and still count as "the open at that time".
DEFAULT_OPEN_TOLERANCE_MIN = 5

SWEPT = "SWEPT"
UNTESTED = "UNTESTED"

# Sessions in New York time, ICT convention: Asia 8pm-midnight, London 2am-5am. (start, end) with end 24:00 = midnight.
DEFAULT_SESSIONS = {
    "asia": ("20:00", "24:00"),
    "london": ("02:00", "05:00"),
}


@dataclass(frozen=True)
class IctConfig:
    atr_window: int = DEFAULT_ATR_WINDOW
    lookback_5m_days: int = DEFAULT_LOOKBACK_5M_DAYS
    lookback_1h_days: int = DEFAULT_LOOKBACK_1H_DAYS
    eq_atr_mult: float = DEFAULT_EQ_ATR_MULT
    swing_window: int = DEFAULT_SWING_WINDOW
    rvol_window: int = DEFAULT_RVOL_WINDOW
    min_session_coverage: float = DEFAULT_MIN_SESSION_COVERAGE
    open_tolerance_min: int = DEFAULT_OPEN_TOLERANCE_MIN
    sessions: tuple = tuple((name, start, end) for name, (start, end) in DEFAULT_SESSIONS.items())


def ict_config_from_rules(rules: dict) -> IctConfig:
    section = dict(rules.get("ict", {}))
    sessions = section.pop("sessions", None)
    allowed = {f for f in IctConfig.__dataclass_fields__} - {"sessions"}
    kwargs = {k: v for k, v in section.items() if k in allowed}
    if sessions:
        kwargs["sessions"] = tuple((name, w[0], w[1]) for name, w in sessions.items())
    return IctConfig(**kwargs)


def _to_ny(bars: pd.DataFrame) -> pd.DataFrame:
    ordered = bars.sort_values("timestamp").reset_index(drop=True)
    ts = pd.to_datetime(ordered["timestamp"], utc=True).dt.tz_convert(NY)
    return ordered.assign(ts=ts)


def _clock(text: str) -> timedelta:
    """'HH:MM' -> offset from midnight; '24:00' is allowed and means the end of the day."""
    hours, minutes = text.split(":")
    return timedelta(hours=int(hours), minutes=int(minutes))


def _day_start(day: pd.Timestamp) -> pd.Timestamp:
    """Local midnight of a New York calendar day (DST-safe: built from the wall-clock date, not by adding 24h)."""
    return pd.Timestamp(day.date()).tz_localize(NY)


def _local_at(day: pd.Timestamp, offset: timedelta) -> pd.Timestamp:
    """The New York wall-clock moment `offset` after local midnight of `day`."""
    midnight = pd.Timestamp(day.date())
    return (midnight + offset).tz_localize(NY, ambiguous=True, nonexistent="shift_forward")


def open_price(bars: pd.DataFrame, target: pd.Timestamp, tolerance_min: int) -> float:
    """Open of the first bar at or after `target`, only if that bar is within `tolerance_min` of it.

    A bar that starts later than the target is not the open at that time (equities have no 00:00 bar), so it
    reads NaN rather than quietly substituting the 04:00 pre-market print for a midnight open.
    """
    after = bars[bars["ts"] >= target]
    if after.empty:
        return float("nan")
    first = after.iloc[0]
    if first["ts"] - target > pd.Timedelta(minutes=tolerance_min):
        return float("nan")
    return float(first["open"])


def first_open_since(bars: pd.DataFrame, start: pd.Timestamp) -> float:
    """Open of the first bar at or after `start`, however late it begins (weekly/monthly opens)."""
    after = bars[bars["ts"] >= start]
    return float(after.iloc[0]["open"]) if not after.empty else float("nan")


def session_extremes(
    bars: pd.DataFrame, start_text: str, end_text: str, min_coverage: float
) -> dict | None:
    """High/low of the most recent *completed* session, with whether either side has since been traded through.

    Walks back from the latest bar's New York date, so the same call serves a Monday-midday run (last Asia =
    Sunday evening) and an after-close run. None when no completed session has enough bars to trust.
    """
    last_ts = bars["ts"].iloc[-1]
    expected = max(1, int((_clock(end_text) - _clock(start_text)).total_seconds() // 60 // BAR_MINUTES))
    for back in range(0, 5):
        day = last_ts - pd.Timedelta(days=back)
        start = _local_at(day, _clock(start_text))
        end = _local_at(day, _clock(end_text))
        # Completed means the latest bar has reached the session's final bar.
        if last_ts < end - pd.Timedelta(minutes=BAR_MINUTES):
            continue
        window = bars[(bars["ts"] >= start) & (bars["ts"] < end)]
        if window.empty or len(window) / expected < min_coverage:
            continue
        high, low = float(window["high"].max()), float(window["low"].min())
        later = bars[bars["ts"] >= end]
        return {
            "high": high,
            "low": low,
            "high_status": SWEPT if (later["high"] > high).any() else UNTESTED,
            "low_status": SWEPT if (later["low"] < low).any() else UNTESTED,
        }
    return None


def find_equal_levels(swings: pd.DataFrame, bars: pd.DataFrame, kind: str, tolerance: float, spot: float) -> dict:
    """Nearest cluster of relative equal highs (or lows) on the liquidity side of spot, not yet traded through.

    Two or more swing points within `tolerance` of each other mark resting stops just beyond them. Only
    clusters still untouched count: a later bar through the level has already taken that liquidity.
    EQH must sit above spot and EQL below, otherwise it is behind price, not a draw.
    """
    empty = {"price": np.nan, "count": 0}
    points = swings[swings["kind"] == kind]
    if points.empty or not np.isfinite(tolerance) or tolerance <= 0:
        return empty

    is_high = kind == "swing_high"
    clusters: list[list[tuple[pd.Timestamp, float]]] = []
    for ts, price in sorted(zip(points["timestamp"], points["price"].astype(float)), key=lambda p: p[1]):
        if clusters and price - clusters[-1][-1][1] <= tolerance:
            clusters[-1].append((ts, price))
        else:
            clusters.append([(ts, price)])

    best = None
    for cluster in clusters:
        if len(cluster) < 2:
            continue
        prices = [p for _, p in cluster]
        level = max(prices) if is_high else min(prices)
        if (is_high and level <= spot) or (not is_high and level >= spot):
            continue
        latest = max(ts for ts, _ in cluster)
        later = bars[bars["timestamp"] > latest]
        if is_high and (later["high"] > level).any():
            continue
        if not is_high and (later["low"] < level).any():
            continue
        if best is None or abs(level - spot) < abs(best["price"] - spot):
            best = {"price": level, "count": len(cluster)}
    return best or empty


def last_day_volume_read(daily: pd.DataFrame, window: int) -> tuple[float, float]:
    """(relative volume, return) of the last *completed* day, i.e. the one before the latest (still forming) date.

    Volume is the day's total against the mean of the `window` days before it; the baseline excludes the day
    itself (same convention as location.volume_ratio_at). NaN until there is a full baseline.
    """
    if len(daily) < window + 2:
        return float("nan"), float("nan")
    day = daily.iloc[-2]
    baseline = daily["volume"].iloc[-2 - window : -2].mean()
    rvol = float(day["volume"] / baseline) if np.isfinite(baseline) and baseline > 0 else float("nan")
    ret = float(day["close"] / day["open"] - 1) if day["open"] else float("nan")
    return rvol, ret


def build_symbol_levels(bars_5m: pd.DataFrame, bars_1h: pd.DataFrame | None, config: IctConfig) -> dict:
    """One row of ICT reference levels for one symbol, from its 5m bars (and 1H bars for equal highs/lows)."""
    bars = _to_ny(bars_5m)
    symbol = bars["symbol"].iloc[-1]
    last = bars.iloc[-1]
    spot = float(last["close"])
    today = last["ts"]

    daily = bars.assign(day=bars["ts"].dt.date).groupby("day").agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"),
        volume=("volume", "sum"),
    )
    atr = average_true_range(
        daily["high"].to_numpy(float), daily["low"].to_numpy(float), daily["close"].to_numpy(float), config.atr_window
    )
    rvol_1d, ret_1d = last_day_volume_read(daily, config.rvol_window)

    tol = config.open_tolerance_min
    week_start = _day_start(today - pd.Timedelta(days=today.weekday()))
    month_start = _day_start(today.replace(day=1))
    row: dict = {
        "symbol": symbol,
        "as_of": last["timestamp"],
        "spot": spot,
        "atr": atr,
        "rvol_1d": rvol_1d,
        "ret_1d": ret_1d,
        "midnight_open": open_price(bars, _local_at(today, _clock("00:00")), tol),
        "open_0830": open_price(bars, _local_at(today, _clock("08:30")), tol),
        "open_0930": open_price(bars, _local_at(today, _clock("09:30")), tol),
        "week_open": first_open_since(bars, week_start),
        "month_open": first_open_since(bars, month_start),
    }

    for name, start_text, end_text in config.sessions:
        extremes = session_extremes(bars, start_text, end_text, config.min_session_coverage)
        row[f"{name}_high"] = extremes["high"] if extremes else np.nan
        row[f"{name}_low"] = extremes["low"] if extremes else np.nan
        row[f"{name}_high_status"] = extremes["high_status"] if extremes else None
        row[f"{name}_low_status"] = extremes["low_status"] if extremes else None

    row.update({"eqh": np.nan, "eqh_count": 0, "eql": np.nan, "eql_count": 0})
    if bars_1h is not None and not bars_1h.empty:
        hourly = bars_1h.sort_values("timestamp").reset_index(drop=True)
        hourly = hourly.assign(timestamp=pd.to_datetime(hourly["timestamp"], utc=True))
        h_atr = average_true_range(
            hourly["high"].to_numpy(float), hourly["low"].to_numpy(float), hourly["close"].to_numpy(float),
            config.atr_window,
        )
        swings = find_swing_points(hourly, window=config.swing_window)
        tolerance = config.eq_atr_mult * h_atr
        eqh = find_equal_levels(swings, hourly, "swing_high", tolerance, spot)
        eql = find_equal_levels(swings, hourly, "swing_low", tolerance, spot)
        row.update({"eqh": eqh["price"], "eqh_count": eqh["count"], "eql": eql["price"], "eql_count": eql["count"]})
    return row


def build_levels(price_5m: pd.DataFrame, price_1h: pd.DataFrame | None, config: IctConfig = IctConfig()) -> pd.DataFrame:
    """ICT levels for every symbol in the 5m data."""
    if price_5m.empty:
        return pd.DataFrame()
    five = price_5m.drop_duplicates(["symbol", "timestamp"], keep="last")
    hourly_by_symbol = {}
    if price_1h is not None and not price_1h.empty:
        hourly_by_symbol = {s: g for s, g in price_1h.groupby("symbol")}
    rows = [
        build_symbol_levels(group, hourly_by_symbol.get(symbol), config)
        for symbol, group in five.groupby("symbol", sort=True)
    ]
    return pd.DataFrame(rows)


def _read_recent(path, days: int) -> pd.DataFrame:
    """Only the trailing `days` of a large bar file; the parquet filter keeps the 5m file out of memory."""
    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days)
    return pd.read_parquet(path, filters=[("timestamp", ">=", cutoff)])


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Derive opens, session highs/lows and equal highs/lows from 5m/1H bars."
    )
    parser.parse_args()

    from market_data_pipeline.picks import RULES_PATH, load_rules

    config = ict_config_from_rules(load_rules(RULES_PATH))
    five_path = DATA_DIR / PRICE_5M_FILENAME
    if not five_path.exists():
        raise SystemExit(f"Missing {five_path}. Run the price collector first (5m timeframe).")
    hourly_path = DATA_DIR / PRICE_1H_FILENAME

    price_5m = _read_recent(five_path, config.lookback_5m_days)
    price_1h = _read_recent(hourly_path, config.lookback_1h_days) if hourly_path.exists() else None
    levels = build_levels(price_5m, price_1h, config)
    levels.to_parquet(DATA_DIR / LEVELS_FILENAME, index=False)
    print(f"Saved {len(levels)} ICT level rows to {DATA_DIR / LEVELS_FILENAME}")


if __name__ == "__main__":
    main()
