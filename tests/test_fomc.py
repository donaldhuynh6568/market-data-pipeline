import pandas as pd

from market_data_pipeline.collectors.fomc import build_fomc_decisions, classify_decision


def _series(dates_values: list[tuple[str, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        [{"date": date, "value": value, "series_id": "DFEDTARU"} for date, value in dates_values]
    )


def test_classify_decision_hike():
    decision, change_bps = classify_decision(5.25, 5.50)
    assert decision == "hike"
    assert change_bps == 25


def test_classify_decision_cut():
    decision, change_bps = classify_decision(5.50, 5.25)
    assert decision == "cut"
    assert change_bps == -25


def test_classify_decision_hold():
    decision, change_bps = classify_decision(5.50, 5.50)
    assert decision == "hold"
    assert change_bps == 0


def test_classify_decision_unknown_when_missing_data():
    decision, change_bps = classify_decision(None, 5.50)
    assert decision == "unknown"
    assert change_bps is None


def test_build_fomc_decisions_detects_hike_after_meeting():
    series = _series(
        [
            ("2025-01-01", 5.25),
            ("2025-01-30", 5.50),  # rate change effective the day after the meeting
        ]
    )
    meeting_dates = [pd.Timestamp("2025-01-29")]

    result = build_fomc_decisions(meeting_dates, series)

    assert len(result) == 1
    row = result.iloc[0]
    assert row["date"] == "2025-01-29"
    assert row["target_upper_before"] == 5.25
    assert row["target_upper_after"] == 5.50
    assert row["decision"] == "hike"
    assert row["change_bps"] == 25


def test_build_fomc_decisions_holds_when_rate_unchanged():
    series = _series(
        [
            ("2025-01-01", 5.25),
            ("2025-03-20", 5.25),
        ]
    )
    meeting_dates = [pd.Timestamp("2025-03-19")]

    result = build_fomc_decisions(meeting_dates, series)

    assert result.iloc[0]["decision"] == "hold"
    assert result.iloc[0]["change_bps"] == 0
