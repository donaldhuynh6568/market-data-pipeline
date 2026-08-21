from market_data_pipeline.collectors.sec import build_ticker_cik_map, parse_submissions


def test_build_ticker_cik_map_uppercases_tickers():
    payload = {
        "0": {"cik_str": 320193, "ticker": "aapl", "title": "Apple Inc."},
        "1": {"cik_str": 1067983, "ticker": "BRK-A", "title": "Berkshire Hathaway Inc"},
    }

    result = build_ticker_cik_map(payload)

    assert result == {"AAPL": 320193, "BRK-A": 1067983}


def _submissions_payload():
    return {
        "cik": 320193,
        "filings": {
            "recent": {
                "form": ["10-K", "4", "8-K", "10-Q"],
                "filingDate": ["2026-08-01", "2026-07-15", "2026-06-01", "2026-05-01"],
                "reportDate": ["2026-06-30", "", "2026-06-01", "2026-03-31"],
                "accessionNumber": ["0001-a", "0001-b", "0001-c", "0001-d"],
                "primaryDocument": ["10k.htm", "form4.xml", "8k.htm", "10q.htm"],
            }
        },
    }


def test_parse_submissions_filters_to_requested_forms():
    df = parse_submissions(_submissions_payload(), "AAPL", ["10-K", "10-Q", "8-K"])

    assert set(df["form"]) == {"10-K", "8-K", "10-Q"}
    assert "4" not in df["form"].tolist()


def test_parse_submissions_renames_date_columns_for_lookahead_bias_discipline():
    df = parse_submissions(_submissions_payload(), "AAPL", ["10-K"])

    row = df.iloc[0]
    assert row["observation_date"] == "2026-06-30"
    assert row["release_date"] == "2026-08-01"
    assert row["ticker"] == "AAPL"
    assert row["cik"] == 320193


def test_parse_submissions_returns_empty_when_no_forms_match():
    df = parse_submissions(_submissions_payload(), "AAPL", ["S-1"])

    assert df.empty
