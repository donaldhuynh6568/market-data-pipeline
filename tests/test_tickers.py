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


def test_load_cash_like_tickers(tmp_path):
    from market_data_pipeline.tickers import load_cash_like_tickers

    path = tmp_path / "tickers.yaml"
    path.write_text(yaml.safe_dump({"equities": {}, "cash_like": ["SHV"]}), encoding="utf-8")

    assert load_cash_like_tickers(path) == ["SHV"]
    path.write_text(yaml.safe_dump({"equities": {}}), encoding="utf-8")
    assert load_cash_like_tickers(path) == []


def test_a_symbol_in_two_categories_is_collected_once():
    """XLU sits in both broad_market and sectors; collecting it twice would duplicate every downstream row."""
    raw = {
        "equities": {"broad_market": ["SPY", "XLU"], "sectors": ["XLU", "XLE"]},
        "crypto": ["BTC/USD", "BTC/USD"],
    }

    result = load_tickers_from_dict(raw)

    assert result["equities"] == ["SPY", "XLU", "XLE"]  # first-seen order preserved
    assert result["crypto"] == ["BTC/USD"]


def test_universe_frame_flags_cash_like_as_not_rankable_and_dedupes(tmp_path):
    from market_data_pipeline.tickers import universe_frame

    path = tmp_path / "tickers.yaml"
    path.write_text(yaml.safe_dump({
        "equities": {"broad_market": ["SPY", "XLU"], "sectors": ["XLU"], "defensive": ["SHV"]},
        "crypto": ["BTC/USD"],
        "cash_like": ["SHV"],
    }), encoding="utf-8")

    frame = universe_frame(path).set_index("ticker")

    assert list(frame.index) == ["SPY", "XLU", "SHV", "BTC/USD"]
    assert frame.loc["XLU", "category"] == "broad_market"            # first category wins
    assert frame.loc["BTC/USD", "asset_class"] == "crypto"
    assert not frame.loc["SHV", "is_rankable"] and frame.loc["SPY", "is_rankable"]
