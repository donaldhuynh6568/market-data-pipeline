import numpy as np
import pandas as pd
import pytest

from market_data_pipeline.transforms import ict_levels as il

NY = "America/New_York"


def _bars(start: str, periods: int, prices=None, symbol="X", freq="5min") -> pd.DataFrame:
    """Bars from a New York wall-clock start; open = prior close so opens are easy to assert."""
    index = pd.date_range(start, periods=periods, freq=freq, tz=NY)
    close = np.asarray(prices if prices is not None else np.linspace(100, 110, periods), dtype=float)
    open_ = np.concatenate([[close[0]], close[:-1]])
    return pd.DataFrame({
        "symbol": symbol, "timestamp": index.tz_convert("UTC"), "open": open_, "high": close + 0.5, "low": close - 0.5,
        "close": close, "volume": 1000.0,
    })


def _hourly(n: int, high=100.0, low=90.0) -> pd.DataFrame:
    return pd.DataFrame({"timestamp": pd.date_range("2026-10-01", periods=n, freq="1h", tz="UTC"), "high": high, "low": low})


def _swings(rows):
    return pd.DataFrame(rows, columns=["timestamp", "price", "kind"])


def test_open_price_uses_first_bar_only_within_tolerance():
    bars = il._to_ny(_bars("2026-10-05 04:00", 24))     # equity-like: first bar at 04:00
    midnight = pd.Timestamp("2026-10-05 00:00", tz=NY)
    four = pd.Timestamp("2026-10-05 04:00", tz=NY)
    assert np.isnan(il.open_price(bars, midnight, 5))          # no bar near midnight -> blank, not the 04:00 print
    assert il.open_price(bars, four, 5) == bars["open"].iloc[0]
    assert il.first_open_since(bars, midnight) == bars["open"].iloc[0]   # weekly/monthly accept a late first bar


def test_session_extremes_uses_latest_completed_session_and_flags_sweeps():
    # Sun 20:00 -> Mon 03:00 NY. The Sunday Asia session (20:00-24:00) is complete; Monday's has not started.
    prices = np.concatenate([np.full(48, 100.0), np.full(12, 100.0), [104.0], np.full(23, 101.0)])
    bars = il._to_ny(_bars("2026-10-04 20:00", len(prices), prices))
    result = il.session_extremes(bars, "20:00", "24:00", 0.5)
    assert result["high"] == pytest.approx(100.5)
    assert result["low"] == pytest.approx(99.5)
    assert result["high_status"] == il.SWEPT       # 104 after midnight traded above the Asia high
    assert result["low_status"] == il.UNTESTED


def test_session_extremes_none_when_coverage_too_thin():
    # Equity-like: only 04:00-05:00 of the 02:00-05:00 London window exists -> 12 of 36 bars.
    bars = il._to_ny(_bars("2026-10-05 04:00", 60))
    assert il.session_extremes(bars, "02:00", "05:00", 0.5) is None


def test_session_extremes_skips_session_still_forming():
    bars = il._to_ny(_bars("2026-10-05 02:00", 12))     # only the first hour of London so far
    assert il.session_extremes(bars, "02:00", "05:00", 0.0) is None


def test_session_window_survives_dst_change():
    # 2026-11-01 is the US fall-back day; adding raw hours would put the window an hour off.
    day = pd.Timestamp("2026-11-01")
    start = il._local_at(day, il._clock("20:00"))
    assert start.hour == 20
    end = il._local_at(day, il._clock("24:00"))
    assert (end.hour, end.day) == (0, 2)


def test_find_equal_levels_clusters_untouched_highs_above_spot():
    ts = pd.date_range("2026-10-01", periods=4, freq="1h", tz="UTC")
    swings = _swings([(ts[0], 105.0, "swing_high"), (ts[1], 105.05, "swing_high"), (ts[2], 110.0, "swing_high")])
    result = il.find_equal_levels(swings, _hourly(10), "swing_high", tolerance=0.1, spot=100.0)
    assert result == {"price": pytest.approx(105.05), "count": 2}


def test_find_equal_levels_drops_swept_or_wrong_side_clusters():
    ts = pd.date_range("2026-10-01", periods=2, freq="1h", tz="UTC")
    swings = _swings([(ts[0], 105.0, "swing_high"), (ts[1], 105.0, "swing_high")])
    swept = _hourly(10).assign(high=[100.0] * 5 + [106.0] * 5)
    assert il.find_equal_levels(swings, swept, "swing_high", 0.1, 100.0)["count"] == 0
    calm = _hourly(10)
    assert il.find_equal_levels(swings, calm, "swing_high", 0.1, 110.0)["count"] == 0   # equal highs below spot
    assert il.find_equal_levels(swings, calm, "swing_high", float("nan"), 100.0)["count"] == 0


def test_find_equal_levels_lows_pick_the_nearest_cluster_below_spot():
    ts = pd.date_range("2026-10-01", periods=4, freq="1h", tz="UTC")
    swings = _swings([
        (ts[0], 95.0, "swing_low"), (ts[1], 95.02, "swing_low"), (ts[2], 80.0, "swing_low"), (ts[3], 80.01, "swing_low"),
    ])
    result = il.find_equal_levels(swings, _hourly(10, low=96.0), "swing_low", 0.1, 100.0)
    assert result == {"price": pytest.approx(95.0), "count": 2}


def test_last_day_volume_read_excludes_forming_day_and_baseline_itself():
    days = pd.date_range("2026-09-01", periods=24).date
    volume = [100.0] * 22 + [300.0, 50.0]            # day -2 is heavy, the still-forming last day is tiny
    daily = pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0, "close": 102.0, "volume": volume}, index=days)
    rvol, ret = il.last_day_volume_read(daily, 20)
    assert rvol == pytest.approx(3.0)
    assert ret == pytest.approx(0.02)
    assert all(np.isnan(v) for v in il.last_day_volume_read(daily.iloc[:5], 20))


def test_build_levels_end_to_end_on_crypto_like_bars():
    # Fri 2026-10-02 00:00 NY through Mon 2026-10-05 10:00 NY, one symbol, 24h.
    n = int((pd.Timestamp("2026-10-05 10:00", tz=NY) - pd.Timestamp("2026-10-02 00:00", tz=NY)).total_seconds() // 300)
    bars = _bars("2026-10-02 00:00", n, symbol="BTC/USD")
    row = il.build_levels(bars, None, il.IctConfig()).iloc[0]
    monday_midnight = bars[bars["timestamp"] == pd.Timestamp("2026-10-05 00:00", tz=NY).tz_convert("UTC")]
    assert row["symbol"] == "BTC/USD"
    assert row["spot"] == pytest.approx(bars["close"].iloc[-1])
    assert row["midnight_open"] == pytest.approx(monday_midnight["open"].iloc[0])
    assert row["week_open"] == pytest.approx(row["midnight_open"])        # Monday midnight is the week's first bar
    assert row["month_open"] == pytest.approx(bars["open"].iloc[0])       # Oct 2 is the first bar of October here
    assert row["asia_high"] > row["asia_low"]
    assert not np.isnan(row["london_high"])
    assert row["eqh_count"] == 0                                           # no 1H bars supplied


def test_ict_config_from_rules_reads_sessions_and_ignores_unknown_keys():
    config = il.ict_config_from_rules({"ict": {"eq_atr_mult": 0.2, "bogus": 1, "sessions": {"asia": ["19:00", "24:00"]}}})
    assert config.eq_atr_mult == 0.2
    assert config.sessions == (("asia", "19:00", "24:00"),)
