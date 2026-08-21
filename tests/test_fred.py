from market_data_pipeline.collectors.fred import parse_fred_observations


def test_parse_fred_observations_converts_values_to_numeric():
    payload = {
        "observations": [
            {"realtime_start": "2026-01-01", "realtime_end": "2026-01-01", "date": "2026-06-01", "value": "5.33"},
            {"realtime_start": "2026-01-01", "realtime_end": "2026-01-01", "date": "2026-07-01", "value": "5.41"},
        ]
    }

    df = parse_fred_observations(payload, "FEDFUNDS")

    assert df["series_id"].tolist() == ["FEDFUNDS", "FEDFUNDS"]
    assert df["value"].tolist() == [5.33, 5.41]
    assert list(df.columns) == ["date", "value", "series_id"]


def test_parse_fred_observations_treats_dot_as_missing():
    payload = {"observations": [{"date": "2026-06-01", "value": "."}]}

    df = parse_fred_observations(payload, "DGS10")

    assert df["value"].isna().all()


def test_parse_fred_observations_handles_empty_series():
    df = parse_fred_observations({"observations": []}, "VIXCLS")

    assert df.empty
