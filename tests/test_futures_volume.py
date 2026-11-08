from market_data_pipeline.collectors.futures_volume import parse_chart_response

# Trimmed real fixture: MNQ=F 5-minute bars, captured live from Yahoo Finance's
# chart endpoint during development. The last bar (volume=0) is the currently
# forming, incomplete candle.
MNQ_CHART_RESPONSE = {
    "chart": {
        "result": [
            {
                "meta": {"symbol": "MNQ=F"},
                "timestamp": [
                    1787796000,
                    1787796300,
                    1787796600,
                    1787796900,
                    1787797200,
                    1787797301,
                ],
                "indicators": {
                    "quote": [
                        {
                            "open": [29531.5, 29539.0, 29540.25, 29540.75, 29535.25, 29533.75],
                            "high": [29541.0, 29540.5, 29544.25, 29543.0, 29538.0, 29533.75],
                            "low": [29525.75, 29516.25, 29529.5, 29527.5, 29528.0, 29533.75],
                            "close": [29539.0, 29540.0, 29540.5, 29534.75, 29532.0, 29533.75],
                            "volume": [5240, 6004, 3773, 2455, 432, 0],
                        }
                    ]
                },
            }
        ],
        "error": None,
    }
}


def test_parse_chart_response_drops_the_incomplete_trailing_bar():
    df = parse_chart_response(MNQ_CHART_RESPONSE, "MNQ=F")

    assert len(df) == 5
    assert df["volume"].tolist() == [5240, 6004, 3773, 2455, 432]


def test_parse_chart_response_converts_timestamps_and_tags_symbol():
    df = parse_chart_response(MNQ_CHART_RESPONSE, "MNQ=F")

    assert (df["symbol"] == "MNQ=F").all()
    assert str(df["timestamp"].iloc[0]) == "2026-08-27 02:00:00+00:00"


def test_parse_chart_response_returns_empty_for_no_result():
    payload = {"chart": {"result": None, "error": {"code": "Not Found"}}}

    assert parse_chart_response(payload, "BOGUS=F").empty

