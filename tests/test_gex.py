import pandas as pd

from market_data_pipeline.transforms.gex import (
    classify_gamma_regime,
    compute_contract_gex,
    find_gamma_strikes,
    summarize_gex_by_symbol,
)


def _sample_chain() -> pd.DataFrame:
    return pd.DataFrame({
        "underlying_symbol": ["SPY", "SPY", "SPY", "SPY"],
        "snapshot_date": ["2026-08-14"] * 4,
        "strike_price": [95.0, 100.0, 100.0, 105.0],
        "type": ["put", "call", "put", "call"],
        "gamma": [0.05, 0.08, 0.03, 0.02],
        "open_interest": [1000, 2000, 500, 800],
        "spot_price": [100.0, 100.0, 100.0, 100.0],
    })


def test_compute_contract_gex_sign_convention():
    result = compute_contract_gex(_sample_chain())

    call_rows = result[result["type"] == "call"]
    put_rows = result[result["type"] == "put"]
    assert (call_rows["gamma_exposure"] > 0).all()
    assert (put_rows["gamma_exposure"] < 0).all()


def test_summarize_gex_by_symbol_aggregates_calls_and_puts():
    summary = summarize_gex_by_symbol(_sample_chain())

    row = summary.iloc[0]
    assert row["underlying_symbol"] == "SPY"
    assert row["call_gamma"] > 0
    assert row["put_gamma"] < 0
    assert row["total_gamma"] == row["call_gamma"] + row["put_gamma"]


def test_find_gamma_strikes_reports_extremes_and_flip():
    strikes = find_gamma_strikes(_sample_chain())

    row = strikes.iloc[0]
    assert row["max_positive_gamma_strike"] == 100.0
    assert row["max_negative_gamma_strike"] == 95.0
    assert row["gamma_flip"] is not None


def test_classify_gamma_regime_positive_and_negative():
    assert classify_gamma_regime(total_gamma=5.0, spot=100.0, gamma_flip=50.0) == "POSITIVE_GAMMA"
    assert classify_gamma_regime(total_gamma=-5.0, spot=100.0, gamma_flip=50.0) == "NEGATIVE_GAMMA"


def test_classify_gamma_regime_near_flip():
    assert classify_gamma_regime(total_gamma=5.0, spot=100.0, gamma_flip=100.5) == "NEAR_FLIP"
