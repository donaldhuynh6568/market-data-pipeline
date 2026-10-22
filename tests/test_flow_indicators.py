import numpy as np
import pandas as pd
import pytest

from market_data_pipeline.transforms import flow_indicators as fi


def _bars(close, high=None, low=None, volume=100.0, trades=10.0, start="2026-01-01", symbol="X"):
    close = np.asarray(close, dtype=float)
    n = len(close)
    return pd.DataFrame({
        "symbol": symbol,
        "timestamp": pd.date_range(start, periods=n, freq="D", tz="UTC"),
        "open": close,
        "high": close + 1 if high is None else np.asarray(high, dtype=float),
        "low": close - 1 if low is None else np.asarray(low, dtype=float),
        "close": close,
        "volume": np.full(n, volume) if np.isscalar(volume) else np.asarray(volume, dtype=float),
        "trade_count": np.full(n, trades) if np.isscalar(trades) else np.asarray(trades, dtype=float),
    })


def test_cmf_is_plus_one_at_the_highs_minus_one_at_the_lows_and_zero_for_flat_bars():
    n = 30
    at_high = _bars(np.full(n, 10.0), high=np.full(n, 10.0), low=np.full(n, 9.0))
    at_low = _bars(np.full(n, 9.0), high=np.full(n, 10.0), low=np.full(n, 9.0))
    flat = _bars(np.full(n, 10.0), high=np.full(n, 10.0), low=np.full(n, 10.0))

    assert fi.chaikin_money_flow(at_high, 20).iloc[-1] == pytest.approx(1.0)
    assert fi.chaikin_money_flow(at_low, 20).iloc[-1] == pytest.approx(-1.0)
    assert fi.chaikin_money_flow(flat, 20).iloc[-1] == pytest.approx(0.0)       # zero-range bars: no division error
    assert fi.chaikin_money_flow(at_high, 20).iloc[:19].isna().all()            # needs a full window


def test_mfi_is_100_for_a_pure_rise_0_for_a_pure_fall_and_nan_without_flow():
    rising = _bars(np.arange(1, 31, dtype=float))
    falling = _bars(np.arange(30, 0, -1, dtype=float))
    assert fi.money_flow_index(rising, 14).iloc[-1] == pytest.approx(100.0)
    assert fi.money_flow_index(falling, 14).iloc[-1] == pytest.approx(0.0)
    assert np.isnan(fi.money_flow_index(_bars(np.full(30, 5.0)), 14).iloc[-1])    # no flow in either direction
    assert fi.money_flow_index(rising, 14).iloc[:14].isna().all()


def test_mfi_matches_a_hand_calculation():
    close = [10, 11, 10, 12, 13, 12, 14]
    bars = _bars(close, high=close, low=close, volume=[1, 2, 3, 4, 5, 6, 7])
    # typical price == close; flows 22 (up), 30 (down), 48 (up), 65 (up), 72 (down), 98 (up) over the last 6 changes.
    positive = 11 * 2 + 12 * 4 + 13 * 5 + 14 * 7
    negative = 10 * 3 + 12 * 6
    assert fi.money_flow_index(bars, 6).iloc[-1] == pytest.approx(100 - 100 / (1 + positive / negative))


def test_obv_divergence_cases():
    # Price grinds to a new high on shrinking, down-weighted volume: OBV peaked earlier.
    close = np.concatenate([np.linspace(10, 20, 20), [19.0, 21.0]])
    volume = np.concatenate([np.full(20, 1000.0), [5000.0, 1.0]])
    bearish = fi.obv_divergence(_bars(close, volume=volume), 20)
    assert bearish.iloc[-1] == fi.BEARISH_DIV

    close = np.concatenate([np.linspace(20, 10, 20), [11.0, 9.0]])
    bullish = fi.obv_divergence(_bars(close, volume=volume), 20)
    assert bullish.iloc[-1] == fi.BULLISH_DIV

    steady = fi.obv_divergence(_bars(np.linspace(10, 20, 30)), 20)
    assert steady.iloc[-1] == fi.CONFIRM_UP
    assert steady.iloc[:19].isna().all()


def test_obv_divergence_never_looks_ahead():
    close = np.concatenate([np.linspace(10, 20, 25), np.linspace(20, 5, 10)])
    full = fi.obv_divergence(_bars(close), 20)
    truncated = fi.obv_divergence(_bars(close[:28]), 20)
    assert full.iloc[:28].tolist() == truncated.tolist()


def test_relative_strength_is_the_ratio_change_and_nan_without_a_benchmark():
    bench = _bars(np.full(30, 100.0))
    outperformer = _bars(np.linspace(100, 130, 30))
    rs = fi.relative_strength(outperformer, bench, 20)
    assert rs.iloc[-1] == pytest.approx((130 / 100) / (outperformer["close"].iloc[-21] / 100) - 1)
    assert rs.iloc[:20].isna().all()
    assert fi.relative_strength(outperformer, None, 20).isna().all()
    assert fi.benchmark_for("SPY", {}) is None and fi.benchmark_for("AAPL", {}) == "SPY"
    assert fi.benchmark_for("ETH/USD", {}) == "BTC/USD" and fi.benchmark_for("BTC/USD", {}) is None
    assert fi.benchmark_for("AAPL", {"AAPL": "QQQ"}) == "QQQ"


def test_avg_trade_size_ratio_rises_when_prints_get_bigger():
    volume = np.concatenate([np.full(60, 1000.0), np.full(5, 3000.0)])
    ratio = fi.avg_trade_size_ratio(_bars(np.full(65, 10.0), volume=volume, trades=100.0), 5, 60)
    assert ratio.iloc[-1] > 1.3
    assert fi.avg_trade_size_ratio(_bars(np.full(65, 10.0)), 5, 60).iloc[-1] == pytest.approx(1.0)


def test_anchored_vwap_matches_a_manual_calculation_and_ignores_bars_before_the_anchor():
    bars = _bars([10, 20, 30, 40], high=[10, 20, 30, 40], low=[10, 20, 30, 40], volume=[1000, 1, 2, 3])
    anchor = bars["timestamp"].iloc[1]
    assert fi.anchored_vwap(bars, anchor) == pytest.approx((20 * 1 + 30 * 2 + 40 * 3) / 6)
    assert np.isnan(fi.anchored_vwap(bars, None))
    assert np.isnan(fi.anchored_vwap(bars, bars["timestamp"].iloc[-1] + pd.Timedelta(days=1)))


def test_volume_profile_puts_the_poc_where_volume_traded_and_the_value_area_around_it():
    # Heavy volume pinned at 100, light volume spread above and below.
    rows = [(100.0, 100.5, 100.0, 5000.0)] * 10 + [(h, h + 0.5, h, 100.0) for h in np.arange(90, 110, 1.0)]
    bars = pd.DataFrame(rows, columns=["close", "high", "low", "volume"])
    profile = fi.volume_profile(bars, bins=40, value_area=0.7)
    assert profile["poc"] == pytest.approx(100.0, abs=0.5)
    assert profile["val"] <= profile["poc"] <= profile["vah"]
    assert profile["vah"] - profile["val"] < 5                                  # tight: the bulk is at 100
    assert all(np.isnan(v) for v in fi.volume_profile(bars.iloc[0:0]).values())
    assert all(np.isnan(v) for v in fi.volume_profile(bars.assign(volume=0.0)).values())


def test_build_flow_end_to_end_with_a_benchmark_and_no_intraday_data():
    spy = _bars(np.linspace(100, 110, 80), symbol="SPY")
    aapl = _bars(np.linspace(100, 125, 80), symbol="AAPL")
    flow = fi.build_flow(pd.concat([spy, aapl], ignore_index=True), None, None).set_index("symbol")

    assert flow.loc["AAPL", "benchmark"] == "SPY"
    assert flow.loc["AAPL", "rs_short"] > 0
    assert pd.isna(flow.loc["SPY", "rs_short"])
    assert flow.loc["AAPL", "obv_signal"] == fi.CONFIRM_UP
    assert pd.isna(flow.loc["AAPL", "poc"]) and pd.isna(flow.loc["AAPL", "avwap_week"])


def test_flow_config_from_rules_ignores_unknown_keys():
    config = fi.flow_config_from_rules({"flow": {"cmf_window": 10, "bogus": 1, "benchmarks": {"AAPL": "QQQ"}}})
    assert config.cmf_window == 10 and config.benchmarks == {"AAPL": "QQQ"}
