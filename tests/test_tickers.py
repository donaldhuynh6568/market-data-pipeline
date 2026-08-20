from market_data_pipeline.tickers import load_tickers_from_dict


def test_load_tickers_from_dict_flattens_equities_and_keeps_crypto():
    raw = {
        "equities": {
            "broad_market": ["SPY", "QQQ"],
            "sectors": ["XLE", "XLV"],
        },
        "crypto": ["BTC/USD", "ETH/USD"],
    }

    result = load_tickers_from_dict(raw)

    assert result == {
        "equities": ["SPY", "QQQ", "XLE", "XLV"],
        "crypto": ["BTC/USD", "ETH/USD"],
    }
