import numpy as np
import pandas as pd
import pytest

from market_data_pipeline.reports import positioning as pos
from market_data_pipeline.transforms import flow_backtest as fb
from market_data_pipeline.transforms import flow_indicators as fi


def _daily(symbol, close, volume=1000.0, trades=100.0):
    close = np.asarray(close, dtype=float)
    n = len(close)
    return pd.DataFrame({
        "symbol": symbol, "asset_class": "equity",
        "timestamp": pd.date_range("2025-01-01", periods=n, freq="D", tz="UTC"),
        "open": close, "high": close + 1, "low": close - 1, "close": close,
        "volume": volume, "trade_count": trades,
    })


def _trending(n=140, drift=0.002, seed=1):
    rng = np.random.default_rng(seed)
    return 100 * np.exp(np.cumsum(drift + 0.01 * rng.standard_normal(n)))


def test_observations_use_only_data_up_to_their_own_date():
    close = _trending()
    bars = _daily("X", close)
    cfg_f, cfg_p = fi.FlowConfig(), pos.PositioningConfig()
    full = fb.symbol_votes(bars, None, cfg_f, cfg_p)

    # Rewriting the future (everything after bar 100) must not change any vote cast on or before bar 100.
    changed = close.copy()
    changed[101:] = changed[101:] * 3
    altered = fb.symbol_votes(_daily("X", changed), None, cfg_f, cfg_p)

    layers = fb.LAYERS
    early_full = full[full["index"] <= 100].set_index("index")[layers]
    early_altered = altered[altered["index"] <= 100].set_index("index")[layers]
    pd.testing.assert_frame_equal(early_full, early_altered)


def test_returns_are_direction_adjusted_by_the_momentum_sign():
    up = fb.symbol_votes(_daily("U", np.linspace(100, 200, 140)), None, fi.FlowConfig(), pos.PositioningConfig())
    down = fb.symbol_votes(_daily("D", np.linspace(200, 100, 140)), None, fi.FlowConfig(), pos.PositioningConfig())
    assert set(up["direction"]) == {pos.LONG} and set(down["direction"]) == {pos.SHORT}
    assert (up["ret_10"] > 0).all()                 # long on a rising series made money
    assert (down["ret_10"] > 0).all()               # short on a falling series made money too


def test_summary_samples_non_overlapping_windows_and_includes_a_baseline():
    observations = pd.DataFrame({
        "symbol": "X", "index": range(70, 110), "direction": pos.LONG, "ret_5": 0.01, "ret_10": 0.02, "ret_20": 0.03,
        "CMF": "agree", "MFI": "against", "OBV": None, "Rel. strength": "neutral", "Trade size": None,
    })

    table = fb.summarize(observations)

    ten = table[(table["horizon"] == 10) & (table["layer"] == "ALL")]
    assert int(ten["observations"].iloc[0]) == 4                       # indexes 70, 80, 90, 100
    cmf = table[(table["layer"] == "CMF") & (table["horizon"] == 10)]
    assert cmf["vote"].tolist() == ["agree"] and cmf["mean_return"].iloc[0] == pytest.approx(0.02)
    assert table[table["layer"] == "OBV"].empty                         # a layer with no votes yields no rows


def test_build_backtest_skips_short_histories_and_uses_the_benchmark():
    short = _daily("S", _trending(50))
    spy = _daily("SPY", _trending(160, seed=2))
    name = _daily("N", _trending(160, seed=3))

    table = fb.build_backtest(pd.concat([short, spy, name], ignore_index=True))

    assert not table.empty
    assert set(table.columns) == set(fb.COLUMNS)
    rs = table[(table["layer"] == "Rel. strength")]
    assert not rs.empty                                                  # N is measured against SPY


def test_headline_needs_enough_observations_on_both_sides():
    rows = [
        ("ALL", "LONG", 10, "all", 500, 0.01, 0.55),
        ("CMF", "LONG", 10, "agree", 300, 0.02, 0.6),
        ("CMF", "LONG", 10, "against", 100, -0.01, 0.4),
        ("MFI", "LONG", 10, "agree", 300, 0.02, 0.6),
        ("MFI", "LONG", 10, "against", 5, -0.01, 0.4),
    ]
    lines = fb.headline(pd.DataFrame(rows, columns=fb.COLUMNS))
    assert "agree +2.00% (n=300) vs against -1.00% (n=100), baseline +1.00%" in lines[0]
    assert lines[1] == "MFI: too few observations"
