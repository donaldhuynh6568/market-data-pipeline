from market_data_pipeline.collectors.congress import parse_senate_transactions


def _sample_payload():
    return [
        {
            "transaction_date": "11/10/2020",
            "owner": "Spouse",
            "ticker": "AAPL",
            "asset_description": "Apple Inc.",
            "asset_type": "Stock",
            "type": "Sale (Full)",
            "amount": "$50,001 - $100,000",
            "comment": "--",
            "senator": "Ron L Wyden",
            "ptr_link": "https://efdsearch.senate.gov/search/view/ptr/example/",
        },
        {
            "transaction_date": "12/01/2020",
            "owner": "Self",
            "ticker": "--",
            "asset_description": "Some Municipal Bond",
            "asset_type": "Municipal Security",
            "type": "Purchase",
            "amount": "$1,001 - $15,000",
            "comment": "--",
            "senator": "Jane Doe",
            "ptr_link": "https://efdsearch.senate.gov/search/view/ptr/example2/",
        },
        {
            "transaction_date": "12/02/2020",
            "owner": "Self",
            "ticker": "MSFT",
            "asset_description": "Microsoft Corp",
            "asset_type": "Stock",
            "type": "Purchase",
            "amount": "$15,001 - $50,000",
            "comment": "--",
            "senator": "John Smith",
            "ptr_link": "https://efdsearch.senate.gov/search/view/ptr/example3/",
        },
    ]


def test_parse_senate_transactions_filters_to_configured_tickers():
    df = parse_senate_transactions(_sample_payload(), ["AAPL", "MSFT"])

    assert sorted(df["ticker"].tolist()) == ["AAPL", "MSFT"]


def test_parse_senate_transactions_skips_unknown_ticker_rows():
    df = parse_senate_transactions(_sample_payload(), ["AAPL", "MSFT"])

    assert "--" not in df["ticker"].tolist()


def test_parse_senate_transactions_is_case_insensitive():
    df = parse_senate_transactions(_sample_payload(), ["aapl"])

    assert df["ticker"].tolist() == ["AAPL"]


def test_parse_senate_transactions_returns_empty_for_no_matches():
    df = parse_senate_transactions(_sample_payload(), ["TSLA"])

    assert df.empty
