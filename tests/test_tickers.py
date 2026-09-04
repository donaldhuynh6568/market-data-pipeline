import yaml

from market_data_pipeline.tickers import (
    load_equity_category,
    load_futures_tickers,
    load_tickers_from_dict,
)


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


def test_load_equity_category_returns_only_requested_category(tmp_path):
    raw = {
        "equities": {
            "broad_market": ["SPY", "QQQ"],
            "large_cap": ["AAPL", "MSFT"],
        }
    }
    path = tmp_path / "tickers.yaml"
    path.write_text(yaml.dump(raw), encoding="utf-8")

    assert load_equity_category("large_cap", path) == ["AAPL", "MSFT"]
    assert load_equity_category("missing_category", path) == []


def test_load_futures_tickers_returns_flat_list(tmp_path):
    raw = {"futures": ["MNQ=F", "MES=F", "SIL=F"]}
    path = tmp_path / "tickers.yaml"
    path.write_text(yaml.dump(raw), encoding="utf-8")

    assert load_futures_tickers(path) == ["MNQ=F", "MES=F", "SIL=F"]


def test_load_futures_tickers_returns_empty_when_missing(tmp_path):
    path = tmp_path / "tickers.yaml"
    path.write_text(yaml.dump({"equities": {}}), encoding="utf-8")

    assert load_futures_tickers(path) == []
