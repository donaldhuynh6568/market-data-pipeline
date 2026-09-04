import numpy as np
import pandas as pd
import pytest

from market_data_pipeline.transforms.volume_floor import (
    add_volume_baseline,
    add_volume_ratio_flag,
    compute_volume_floor,
)


def _bars(symbol: str, volumes: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": symbol,
            "timestamp": pd.date_range("2026-01-01 09:30", periods=len(volumes), freq="5min"),
            "volume": volumes,
        }
    )


def test_add_volume_baseline_is_nan_until_window_bars_of_history_exist():
    # window=5: the first 5 bars (indices 0-4) can't have a baseline yet.
    df = _bars("AAA", [100, 100, 100, 100, 100, 300, 100, 50])

    result = add_volume_baseline(df, window=5)

    assert result["baseline_volume"].iloc[:5].isna().all()


def test_add_volume_baseline_excludes_the_current_bar_and_matches_hand_calc():
    df = _bars("AAA", [100, 100, 100, 100, 100, 300, 100, 50])

    result = add_volume_baseline(df, window=5)

    # bar index 5 (volume=300): baseline = mean of prior 5 bars [100,100,100,100,100] = 100
    assert result["baseline_volume"].iloc[5] == pytest.approx(100)
    # bar index 6 (volume=100): baseline = mean of prior 5 bars [100,100,100,100,300] = 140
    assert result["baseline_volume"].iloc[6] == pytest.approx(140)
    # bar index 7 (volume=50): baseline = mean of prior 5 bars [100,100,100,300,100] = 140
    assert result["baseline_volume"].iloc[7] == pytest.approx(140)


def test_add_volume_ratio_flag_true_at_or_above_multiplier():
    df = pd.DataFrame({"volume": [300, 100, 50], "baseline_volume": [100, 140, 140]})

    result = add_volume_ratio_flag(df, multiplier=2.0)

    assert result["volume_ratio"].tolist() == pytest.approx([3.0, 100 / 140, 50 / 140])
    assert result["volume_floor_met"].tolist() == [True, False, False]


def test_add_volume_ratio_flag_true_exactly_at_threshold():
    df = pd.DataFrame({"volume": [200], "baseline_volume": [100]})

    result = add_volume_ratio_flag(df, multiplier=2.0)

    assert result["volume_floor_met"].iloc[0]


def test_add_volume_ratio_flag_nan_baseline_gives_false_not_a_crash():
    df = pd.DataFrame({"volume": [500], "baseline_volume": [np.nan]})

    result = add_volume_ratio_flag(df, multiplier=2.0)

    assert not result["volume_floor_met"].iloc[0]


def test_compute_volume_floor_calibrates_independently_per_symbol():
    # AAA: small-scale volumes, spikes to 300 (3x its own ~100 baseline).
    # BBB: large-scale volumes, spikes to 30,000 (3x its own ~10,000 baseline).
    # Same relative spike, wildly different absolute scale — proves the
    # baseline is per-symbol, not one shared threshold in disguise.
    aaa = _bars("AAA", [100, 100, 100, 100, 100, 300])
    bbb = _bars("BBB", [10000, 10000, 10000, 10000, 10000, 30000])
    df = pd.concat([aaa, bbb], ignore_index=True)

    result = compute_volume_floor(df, window=5, multiplier=2.0)

    aaa_last = result[result["symbol"] == "AAA"].iloc[-1]
    bbb_last = result[result["symbol"] == "BBB"].iloc[-1]

    assert aaa_last["baseline_volume"] == pytest.approx(100)
    assert bbb_last["baseline_volume"] == pytest.approx(10000)
    assert aaa_last["volume_floor_met"]
    assert bbb_last["volume_floor_met"]
