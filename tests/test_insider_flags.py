from datetime import date

import pandas as pd

from market_data_pipeline.reports.insider_flags import InsiderConfig, insider_sell_flags


def _trades(rows: list[tuple]) -> pd.DataFrame:
    return pd.DataFrame(
        rows,
        columns=["ticker", "reporting_owner", "transaction_date", "transaction_code", "acquired_disposed_code"],
    )


def test_flags_only_names_meeting_both_sell_and_owner_thresholds():
    rows = (
        [("AVGO", f"insider{i % 4}", "2026-08-10", "S", "D") for i in range(24)]  # 24 sells, 4 insiders -> flagged
        + [("MA", "onlyone", "2026-08-10", "S", "D") for _ in range(30)]  # many sells, one insider -> not flagged
        + [("META", f"i{i % 3}", "2026-08-10", "S", "D") for i in range(5)]  # 3 insiders, too few sells
    )

    flags = insider_sell_flags(_trades(rows), date(2026, 9, 19), InsiderConfig(window_days=90, min_sells=20, min_owners=3))

    assert flags == {"AVGO": {"sells": 24, "owners": 4, "window_days": 90}}


def test_ignores_non_sales_and_acquisitions():
    rows = (
        [("AVGO", f"i{i % 4}", "2026-08-10", "M", "A") for i in range(25)]  # option exercises, not sales
        + [("AVGO", f"i{i % 4}", "2026-08-10", "S", "A") for i in range(25)]  # acquired direction
    )

    assert insider_sell_flags(_trades(rows), date(2026, 9, 19)) == {}


def test_only_counts_the_window_ending_at_as_of():
    rows = (
        [("AVGO", f"i{i % 4}", "2026-01-10", "S", "D") for i in range(30)]  # too old
        + [("AVGO", f"i{i % 4}", "2026-10-10", "S", "D") for i in range(30)]  # after as_of
    )

    assert insider_sell_flags(_trades(rows), date(2026, 9, 19)) == {}


def test_empty_or_missing_data_gives_no_flags():
    assert insider_sell_flags(None, date(2026, 9, 19)) == {}
    assert insider_sell_flags(_trades([]), date(2026, 9, 19)) == {}
