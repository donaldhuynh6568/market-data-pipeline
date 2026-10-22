from __future__ import annotations

import argparse
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from market_data_pipeline.config import DATA_DIR
from market_data_pipeline.transforms.ict_levels import NY, _day_start
from market_data_pipeline.transforms.swing_structure import find_swing_points

OUTPUT_FILENAME = "flow_indicators.parquet"
DAILY_FILENAME = "price_data.parquet"
HOURLY_FILENAME = "price_data_1h.parquet"
INTRADAY_FILENAME = "price_data_5m.parquet"

BULLISH_DIV = "BULLISH_DIV"
BEARISH_DIV = "BEARISH_DIV"
CONFIRM_UP = "CONFIRM_UP"
CONFIRM_DOWN = "CONFIRM_DOWN"
NO_SIGNAL = "NONE"

DEFAULT_EQUITY_BENCHMARK = "SPY"
DEFAULT_CRYPTO_BENCHMARK = "BTC/USD"


@dataclass(frozen=True)
class FlowConfig:
    cmf_window: int = 20
    mfi_window: int = 14
    obv_lookback: int = 20
    rs_short: int = 20
    rs_long: int = 60
    size_short: int = 5
    size_long: int = 60
    swing_window: int = 3
    profile_sessions: int = 20
    profile_bins: int = 100
    value_area: float = 0.70
    lookback_1h_days: int = 60
    lookback_5m_days: int = 45
    benchmarks: dict = field(default_factory=dict)  # symbol -> benchmark symbol; unlisted fall back to SPY / BTC


def flow_config_from_rules(rules: dict) -> FlowConfig:
    section = rules.get("flow", {})
    allowed = set(FlowConfig.__dataclass_fields__)
    return FlowConfig(**{k: v for k, v in section.items() if k in allowed})


def benchmark_for(symbol: str, benchmarks: dict) -> str | None:
    """The index a name is measured against: an override, else SPY for equities and BTC for other crypto.

    The benchmark itself has no relative strength, so it gets None.
    """
    choice = benchmarks.get(symbol) or (DEFAULT_CRYPTO_BENCHMARK if "/" in symbol else DEFAULT_EQUITY_BENCHMARK)
    return None if choice == symbol else choice


# --- daily series (pure, so a backtest can read them point-in-time) --------------------------------------------------


def _floats(bars: pd.DataFrame, column: str) -> pd.Series:
    return bars[column].astype(float).reset_index(drop=True)


def chaikin_money_flow(bars: pd.DataFrame, window: int = 20) -> pd.Series:
    """Volume-weighted close location: +1 when every bar closes at its high, -1 at its low. NaN until `window` bars.

    A bar with no range contributes zero flow rather than a division by zero.
    """
    high, low, close, volume = (_floats(bars, c) for c in ("high", "low", "close", "volume"))
    span = high - low
    multiplier = ((close - low) - (high - close)).div(span.where(span > 0)).fillna(0.0)
    flow = (multiplier * volume).rolling(window).sum()
    total = volume.rolling(window).sum()
    return flow.div(total.where(total > 0))


def money_flow_index(bars: pd.DataFrame, window: int = 14) -> pd.Series:
    """Volume-weighted RSI on typical price, 0-100. NaN until `window` flows exist."""
    high, low, close, volume = (_floats(bars, c) for c in ("high", "low", "close", "volume"))
    typical = (high + low + close) / 3
    raw = typical * volume
    change = typical.diff()
    positive = raw.where(change > 0, 0.0).rolling(window).sum()
    negative = raw.where(change < 0, 0.0).rolling(window).sum()
    ratio = positive.div(negative.where(negative > 0))
    mfi = 100 - 100 / (1 + ratio)
    mfi = mfi.where(~((negative == 0) & (positive > 0)), 100.0)     # all inflow
    mfi = mfi.where(~((negative == 0) & (positive == 0)), np.nan)   # no flow at all
    # The first row has no previous typical price, so a window that includes it is one flow short.
    mfi.iloc[:window] = np.nan
    return mfi


def on_balance_volume(bars: pd.DataFrame) -> pd.Series:
    close, volume = _floats(bars, "close"), _floats(bars, "volume")
    return (np.sign(close.diff()).fillna(0.0) * volume).cumsum()


def obv_divergence(bars: pd.DataFrame, lookback: int = 20) -> pd.Series:
    """Price against OBV over a trailing window, per bar (no lookahead).

    Price at its window high while OBV is below its own window high is BEARISH_DIV (distribution into strength);
    price at its window low while OBV is above its window low is BULLISH_DIV (accumulation into weakness).
    Otherwise the two are compared by direction: CONFIRM_UP / CONFIRM_DOWN when both moved the same way over the
    window, NONE when they disagree without a new extreme.
    """
    close, obv = _floats(bars, "close"), on_balance_volume(bars)
    price_max, price_min = close.rolling(lookback).max(), close.rolling(lookback).min()
    obv_max, obv_min = obv.rolling(lookback).max(), obv.rolling(lookback).min()
    price_move, obv_move = close.diff(lookback - 1), obv.diff(lookback - 1)

    out = pd.Series(NO_SIGNAL, index=close.index, dtype=object)
    out[(price_move > 0) & (obv_move > 0)] = CONFIRM_UP
    out[(price_move < 0) & (obv_move < 0)] = CONFIRM_DOWN
    out[(close >= price_max) & (obv < obv_max)] = BEARISH_DIV
    out[(close <= price_min) & (obv > obv_min)] = BULLISH_DIV
    out[price_max.isna()] = None
    return out


def relative_strength(bars: pd.DataFrame, benchmark: pd.DataFrame | None, window: int) -> pd.Series:
    """Change over `window` bars in close / benchmark close, matched by calendar date. NaN where the benchmark
    has no bar that day, and entirely NaN when there is no benchmark."""
    empty = pd.Series(np.nan, index=range(len(bars)), dtype=float)
    if benchmark is None or benchmark.empty:
        return empty
    dates = pd.to_datetime(bars["timestamp"], utc=True).dt.date.reset_index(drop=True)
    bench = benchmark.assign(_d=pd.to_datetime(benchmark["timestamp"], utc=True).dt.date)
    bench_close = bench.drop_duplicates("_d", keep="last").set_index("_d")["close"].astype(float)
    ratio = _floats(bars, "close") / dates.map(bench_close).astype(float)
    return ratio / ratio.shift(window) - 1


def avg_trade_size_ratio(bars: pd.DataFrame, short: int = 5, long: int = 60) -> pd.Series:
    """Recent average trade size (volume / trades) against its longer baseline; above 1 means bigger prints."""
    trades = _floats(bars, "trade_count")
    size = _floats(bars, "volume").div(trades.where(trades > 0))
    return size.rolling(short).mean().div(size.rolling(long).mean())


# --- intraday levels --------------------------------------------------------------------------------------------------


def anchored_vwap(bars: pd.DataFrame, anchor: pd.Timestamp | None) -> float:
    """Volume-weighted average typical price from `anchor` (inclusive) to the last bar; NaN with no volume."""
    if anchor is None or pd.isna(anchor):
        return float("nan")
    since = bars[pd.to_datetime(bars["timestamp"], utc=True) >= anchor]
    volume = since["volume"].astype(float)
    if since.empty or volume.sum() <= 0:
        return float("nan")
    typical = (since["high"].astype(float) + since["low"].astype(float) + since["close"].astype(float)) / 3
    return float((typical * volume).sum() / volume.sum())


def volume_profile(bars: pd.DataFrame, bins: int = 100, value_area: float = 0.70) -> dict:
    """Point of control and value area from where volume traded.

    Each bar's volume is spread evenly over the price bins its high-low range touches. The value area grows out
    from the POC bin, always taking the larger neighbouring bin, until it holds `value_area` of all volume.
    """
    nan = {"poc": np.nan, "vah": np.nan, "val": np.nan}
    if bars.empty:
        return nan
    high, low = bars["high"].astype(float).to_numpy(), bars["low"].astype(float).to_numpy()
    volume = bars["volume"].astype(float).to_numpy()
    lo, hi = low.min(), high.max()
    if not np.isfinite(lo) or hi <= lo or volume.sum() <= 0:
        return nan
    width = (hi - lo) / bins
    first = np.clip(((low - lo) / width).astype(int), 0, bins - 1)
    last = np.clip(((high - lo) / width).astype(int), 0, bins - 1)
    profile = np.zeros(bins)
    for a, b, v in zip(first, last, volume):
        profile[a : b + 1] += v / (b - a + 1)

    poc = int(profile.argmax())
    lower = upper = poc
    covered, target = profile[poc], value_area * profile.sum()
    while covered < target and (lower > 0 or upper < bins - 1):
        below = profile[lower - 1] if lower > 0 else -1.0
        above = profile[upper + 1] if upper < bins - 1 else -1.0
        if above >= below:
            upper += 1
            covered += profile[upper]
        else:
            lower -= 1
            covered += profile[lower]
    return {"poc": lo + (poc + 0.5) * width, "vah": lo + (upper + 1) * width, "val": lo + lower * width}


# --- per-symbol row ---------------------------------------------------------------------------------------------------


def _last(series: pd.Series) -> float:
    return float(series.iloc[-1]) if len(series) and pd.notna(series.iloc[-1]) else float("nan")


def build_symbol_row(
    daily: pd.DataFrame, hourly: pd.DataFrame | None, intraday: pd.DataFrame | None,
    benchmark: pd.DataFrame | None, benchmark_symbol: str | None, config: FlowConfig,
) -> dict:
    ordered = daily.sort_values("timestamp").reset_index(drop=True)
    obv_signal = obv_divergence(ordered, config.obv_lookback)
    ret5 = ordered["close"].astype(float).pct_change(config.size_short)
    row: dict = {
        "symbol": ordered["symbol"].iloc[-1],
        "as_of": ordered["timestamp"].iloc[-1],
        "cmf": _last(chaikin_money_flow(ordered, config.cmf_window)),
        "mfi": _last(money_flow_index(ordered, config.mfi_window)),
        "obv_signal": obv_signal.iloc[-1] if len(obv_signal) else None,
        "benchmark": benchmark_symbol,
        "rs_short": _last(relative_strength(ordered, benchmark, config.rs_short)),
        "rs_long": _last(relative_strength(ordered, benchmark, config.rs_long)),
        "trade_size_ratio": _last(avg_trade_size_ratio(ordered, config.size_short, config.size_long))
        if "trade_count" in ordered.columns else float("nan"),
        "trade_size_ret": _last(ret5),
    }

    anchors = {"avwap_week": None, "avwap_month": None, "avwap_swing_low": None, "avwap_swing_high": None}
    if hourly is not None and not hourly.empty:
        bars = hourly.sort_values("timestamp").reset_index(drop=True)
        ny = bars.assign(ts=pd.to_datetime(bars["timestamp"], utc=True).dt.tz_convert(NY))
        today = ny["ts"].iloc[-1]
        for key, start in (
            ("avwap_week", _day_start(today - pd.Timedelta(days=today.weekday()))),
            ("avwap_month", _day_start(today.replace(day=1))),
        ):
            from_start = ny.loc[ny["ts"] >= start, "timestamp"]
            anchors[key] = pd.Timestamp(from_start.iloc[0]) if len(from_start) else None
        swings = find_swing_points(bars.assign(timestamp=pd.to_datetime(bars["timestamp"], utc=True)), window=config.swing_window)
        for kind, key in (("swing_low", "avwap_swing_low"), ("swing_high", "avwap_swing_high")):
            picked = swings[swings["kind"] == kind]
            anchors[key] = pd.Timestamp(picked["timestamp"].iloc[-1]) if not picked.empty else None
        for key, anchor in anchors.items():
            row[key] = anchored_vwap(bars, anchor)
    else:
        row.update(dict.fromkeys(anchors, float("nan")))

    if intraday is not None and not intraday.empty:
        stamped = intraday.assign(session=pd.to_datetime(intraday["timestamp"], utc=True).dt.tz_convert(NY).dt.date)
        keep = sorted(stamped["session"].unique())[-config.profile_sessions :]
        row.update(volume_profile(stamped[stamped["session"].isin(keep)], config.profile_bins, config.value_area))
    else:
        row.update({"poc": np.nan, "vah": np.nan, "val": np.nan})
    return row


def build_flow(
    daily: pd.DataFrame, hourly: pd.DataFrame | None, intraday: pd.DataFrame | None, config: FlowConfig = FlowConfig()
) -> pd.DataFrame:
    """Flow indicators for every symbol in the daily data."""
    if daily.empty:
        return pd.DataFrame()
    daily = daily.drop_duplicates(["symbol", "timestamp"], keep="last")
    by_symbol = {s: g for s, g in daily.groupby("symbol")}
    hourly_by = {s: g for s, g in hourly.groupby("symbol")} if hourly is not None and not hourly.empty else {}
    intraday_by = {s: g for s, g in intraday.groupby("symbol")} if intraday is not None and not intraday.empty else {}
    rows = []
    for symbol in sorted(by_symbol):
        bench_symbol = benchmark_for(symbol, config.benchmarks)
        rows.append(
            build_symbol_row(
                by_symbol[symbol], hourly_by.get(symbol), intraday_by.get(symbol),
                by_symbol.get(bench_symbol) if bench_symbol else None, bench_symbol, config,
            )
        )
    return pd.DataFrame(rows)


def _read_recent(path, days: int) -> pd.DataFrame:
    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days)
    return pd.read_parquet(path, filters=[("timestamp", ">=", cutoff)])


def main() -> None:
    argparse.ArgumentParser(
        description="Money flow (CMF, MFI, OBV), relative strength, trade size, anchored VWAP and volume profile."
    ).parse_args()

    from market_data_pipeline.picks import RULES_PATH, load_rules

    config = flow_config_from_rules(load_rules(RULES_PATH))
    daily_path = DATA_DIR / DAILY_FILENAME
    if not daily_path.exists():
        raise SystemExit(f"Missing {daily_path}. Run the price collector first.")
    hourly_path, intraday_path = DATA_DIR / HOURLY_FILENAME, DATA_DIR / INTRADAY_FILENAME

    flow = build_flow(
        pd.read_parquet(daily_path),
        _read_recent(hourly_path, config.lookback_1h_days) if hourly_path.exists() else None,
        _read_recent(intraday_path, config.lookback_5m_days) if intraday_path.exists() else None,
        config,
    )
    flow.to_parquet(DATA_DIR / OUTPUT_FILENAME, index=False)
    print(f"Saved {len(flow)} flow rows to {DATA_DIR / OUTPUT_FILENAME}")


if __name__ == "__main__":
    main()
