import numpy as np
import pandas as pd
import pytest

from market_data_pipeline.transforms.momentum import compute_momentum


def _bars(symbol: str, closes: list[float], asset_class: str = "equity") -> pd.DataFrame:
    closes_arr = np.array(closes, dtype=float)
    return pd.DataFrame(
        {
            "symbol": symbol,
            "timestamp": pd.date_range("2026-01-01", periods=len(closes), freq="D", tz="UTC"),
            "open": closes_arr,
            "high": closes_arr * 1.01,
            "low": closes_arr * 0.99,
            "close": closes_arr,
            "asset_class": asset_class,
        }
    )


def test_simple_return_windows():
    closes = [100.0 + i for i in range(100)]
    result = compute_momentum(_bars("AAA", closes)).iloc[0]

    assert result["ret_5d"] == closes[-1] / closes[-6] - 1
    assert result["ret_20d"] == closes[-1] / closes[-21] - 1
    assert result["ret_60d"] == closes[-1] / closes[-61] - 1


def test_insufficient_history_yields_nan_not_error():
    result = compute_momentum(_bars("AAA", [100.0 + i for i in range(30)])).iloc[0]

    assert not np.isnan(result["ret_20d"])
    assert np.isnan(result["ret_60d"])
    assert np.isnan(result["vol_adj_score"])
    assert result["above_200d_ma"] is None


def test_vol_adjustment_ranks_smooth_trend_above_noisy_trend():
    rng = np.random.default_rng(0)
    smooth = 100 * np.cumprod(1 + np.full(100, 0.004))
    noisy = 100 * np.cumprod(1 + 0.004 + rng.normal(0, 0.05, 100))
    df = pd.concat([_bars("SMOOTH", smooth), _bars("NOISY", noisy)])

    result = compute_momentum(df)

    assert result.iloc[0]["symbol"] == "SMOOTH"
    assert result.iloc[0]["rank"] == 1


def test_acceleration_positive_when_trend_steepens():
    flat_then_up = [100.0] * 60 + [100.0 * 1.01**i for i in range(1, 41)]
    result = compute_momentum(_bars("AAA", flat_then_up)).iloc[0]

    assert result["acceleration_20d"] > 0


def test_above_200d_ma_flag_and_crypto_annualization():
    up = [100.0 + i for i in range(220)]
    equity = compute_momentum(_bars("AAA", up, "equity")).iloc[0]
    crypto = compute_momentum(_bars("BBB", up, "crypto")).iloc[0]

    assert bool(equity["above_200d_ma"]) is True
    assert crypto["ann_vol"] / equity["ann_vol"] == pytest.approx(np.sqrt(365 / 252))


def test_empty_input_returns_empty_frame():
    assert compute_momentum(pd.DataFrame()).empty


def test_score_formula_matches_hand_calculation():
    from market_data_pipeline.transforms.momentum import compute_score_series

    closes = pd.Series([100.0, 101, 99, 102, 103, 101, 104, 105, 103, 106, 108])
    scores = compute_score_series(closes, 252)

    momentum_pct = (108 / 100 - 1) * 100
    daily = closes.pct_change().iloc[-10:]
    vol_pct = daily.std(ddof=0) * np.sqrt(252) * 100
    assert scores["momentum_pct"].iloc[-1] == pytest.approx(momentum_pct)
    assert scores["vol_pct"].iloc[-1] == pytest.approx(vol_pct)
    assert scores["score"].iloc[-1] == pytest.approx(momentum_pct / vol_pct)


def test_crypto_score_uses_365_day_annualization():
    from market_data_pipeline.transforms.momentum import compute_score_series

    rng = np.random.default_rng(1)
    closes = pd.Series(100 * np.cumprod(1 + rng.normal(0.002, 0.02, 40)))

    ratio = compute_score_series(closes, 252)["score"].iloc[-1] / compute_score_series(closes, 365)["score"].iloc[-1]

    assert ratio == pytest.approx(np.sqrt(365 / 252))


def _score_path_series(daily_returns: list[float]) -> list[float]:
    return list(100 * np.cumprod(1 + np.array(daily_returns)))


def test_delta_signal_accelerating_and_decelerating():
    rng = np.random.default_rng(2)
    noise = rng.normal(0, 0.005, 60)
    # Flat-ish then steadily stronger gains: score keeps rising; mirror image keeps falling.
    rising = _score_path_series(list(noise[:30]) + list(0.001 + 0.0004 * np.arange(30) + noise[30:]))
    falling = _score_path_series(list(0.02 + noise[:30]) + list(0.02 - 0.0008 * np.arange(30) + noise[30:]))
    df = pd.concat([_bars("UP", rising), _bars("DOWN", falling)])

    result = compute_momentum(df).set_index("symbol")

    assert result.loc["UP", "delta_signal"] == "ACCELERATING"
    assert (result.loc["UP", ["delta_5d", "delta_10d", "delta_20d"]] > 0).all()
    assert result.loc["DOWN", "delta_signal"] == "DECELERATING"


def test_delta_signal_mixed_when_windows_disagree():
    from market_data_pipeline.transforms.momentum import classify_delta_signal

    assert classify_delta_signal([0.1, 0.2, -0.05]) == "MIXED"
    assert classify_delta_signal([0.1, float("nan"), 0.2]) == "UNKNOWN"


def test_short_history_gives_nan_deltas_and_unknown_signal():
    result = compute_momentum(_bars("AAA", [100.0 + i for i in range(25)])).iloc[0]

    assert np.isnan(result["delta_20d"])
    assert result["delta_signal"] == "UNKNOWN"


def test_vol_floor_bounds_score_for_near_constant_prices():
    closes = [100.0 * (1 + 0.0002) ** i for i in range(60)]  # tiny, perfectly steady climb
    result = compute_momentum(_bars("SHV", closes)).iloc[0]

    # 10-bar momentum ~0.2%; with the 5% vol floor the score is bounded near 0.04 rather than exploding.
    assert result["score"] < 0.1


def test_cash_like_symbols_are_unranked_and_do_not_displace_others():
    steady = [100.0 * (1 + 0.0002) ** i for i in range(60)]
    rng = np.random.default_rng(3)
    normal = list(100 * np.cumprod(1 + rng.normal(0.003, 0.02, 60)))
    df = pd.concat([_bars("SHV", steady), _bars("AAA", normal)])

    result = compute_momentum(df, cash_like=["SHV"]).set_index("symbol")

    assert bool(result.loc["SHV", "cash_like"]) is True
    assert np.isnan(result.loc["SHV", "rank"])
    assert result.loc["AAA", "rank"] == 1
