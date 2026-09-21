from datetime import date, datetime, timezone

import pytest

from market_data_pipeline.collectors.deribit_options import (
    bs_gamma,
    build_chain,
    parse_instrument,
)
from market_data_pipeline.transforms.gex import compute_gex

NOW = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)


def test_parse_instrument():
    assert parse_instrument("BTC-27MAR26-80000-C") == (date(2026, 3, 27), 80000.0, "call")
    assert parse_instrument("ETH-3OCT26-2500-P") == (date(2026, 10, 3), 2500.0, "put")
    assert parse_instrument("BTC-PERPETUAL") is None
    assert parse_instrument("BTC-27MAR26") is None


def test_bs_gamma_matches_known_value_and_rejects_bad_inputs():
    # ATM, 1y, 50% vol, zero rate: d1 = 0.25, pdf(0.25)/(S*sigma) = 0.38667/50
    assert bs_gamma(100.0, 100.0, 1.0, 0.5) == pytest.approx(0.386668 / 50, rel=1e-4)
    assert bs_gamma(100.0, 100.0, 0.0, 0.5) is None
    assert bs_gamma(100.0, 100.0, 1.0, 0.0) is None


def _summaries():
    return [
        {"instrument_name": "BTC-25SEP26-60000-C", "open_interest": 100, "mark_iv": 50, "volume": 5},
        {"instrument_name": "BTC-25SEP26-60000-P", "open_interest": 200, "mark_iv": 50, "volume": 0},
        {"instrument_name": "BTC-25SEP26-200000-C", "open_interest": 999, "mark_iv": 80},  # out of range
        {"instrument_name": "BTC-25DEC27-60000-C", "open_interest": 999, "mark_iv": 50},  # too far out
        {"instrument_name": "BTC-PERPETUAL", "open_interest": 1},
    ]


def test_build_chain_filters_and_shapes_columns_for_gex():
    chain = build_chain("BTC", _summaries(), spot=60000.0, now=NOW, moneyness=0.2, max_expiry_days=60)

    assert len(chain) == 2
    assert set(chain["type"]) == {"call", "put"}
    assert (chain["underlying_symbol"] == "BTC/USD").all()
    assert (chain["contract_multiplier"] == 1).all()
    assert (chain["implied_volatility"] == 0.5).all()

    gex = compute_gex(chain)
    assert list(gex["symbol"]) == ["BTC/USD"]
    assert gex.iloc[0]["gamma_regime"] in {"POSITIVE_GAMMA", "NEGATIVE_GAMMA", "NEAR_FLIP"}


def test_contract_multiplier_scales_gex_relative_to_equity_default():
    chain = build_chain("BTC", _summaries(), spot=60000.0, now=NOW, moneyness=0.2, max_expiry_days=60)
    default_multiplier = chain.drop(columns="contract_multiplier")

    ratio = compute_gex(default_multiplier).iloc[0]["total_gamma"] / compute_gex(chain).iloc[0]["total_gamma"]

    assert ratio == pytest.approx(100.0)
