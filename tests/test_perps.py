from datetime import datetime, timezone

from market_data_pipeline.collectors.perps import (
    base_asset,
    parse_binance,
    parse_bybit,
    parse_hyperliquid,
)

NOW = datetime(2026, 9, 19, tzinfo=timezone.utc)


def test_base_asset():
    assert base_asset("SOL/USD") == "SOL"


def test_parse_binance():
    row = parse_binance(
        "BTC/USD",
        {"lastFundingRate": "0.0001", "markPrice": "60000.0"},
        {"openInterest": "10.5"},
        NOW,
    )

    assert row["venue"] == "binance"
    assert row["funding_rate"] == 0.0001
    assert row["open_interest"] == 10.5
    assert row["open_interest_usd"] == 630000.0


def test_parse_bybit():
    response = {"result": {"list": [{"fundingRate": "-0.0002", "openInterest": "100", "markPrice": "150"}]}}

    row = parse_bybit("SOL/USD", response, NOW)

    assert row["funding_rate"] == -0.0002
    assert row["open_interest_usd"] == 15000.0


def test_parse_hyperliquid_matches_positionally_and_skips_missing_coins():
    response = [
        {"universe": [{"name": "BTC"}, {"name": "ETH"}]},
        [
            {"funding": "0.00001", "openInterest": "5", "markPx": "60000"},
            {"funding": "0.00002", "openInterest": "50", "markPx": "3000"},
        ],
    ]

    rows = parse_hyperliquid(["ETH/USD", "SOL/USD"], response, NOW)

    assert [r["symbol"] for r in rows] == ["ETH/USD"]
    assert rows[0]["mark_price"] == 3000.0
    assert rows[0]["open_interest_usd"] == 150000.0
