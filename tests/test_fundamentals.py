import pandas as pd

from market_data_pipeline.collectors.fundamentals import (
    _normalize_cik,
    _select_shares_outstanding,
    parse_companyfacts,
    select_fact_value,
)


def _companyfacts_payload():
    """Hand-built SEC XBRL companyfacts payload shaped like the real API response:
    facts.<taxonomy>.<tag>.units.<unit> = [{end, val, fy, fp, form, filed}, ...].

    Covers two fiscal periods (a 10-K and a 10-Q), a metric only available under its
    fallback tag (revenue for the 10-Q uses the legacy `Revenues` tag instead of the
    primary `RevenueFromContractWithCustomerExcludingAssessedTax` tag), and a duplicate
    entry for the same period/form to exercise "prefer most recently filed."
    """
    return {
        "cik": 320193,
        "facts": {
            "us-gaap": {
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "units": {
                        "USD": [
                            {"end": "2026-06-30", "val": 500, "form": "10-K", "filed": "2026-08-01"},
                        ]
                    }
                },
                "Revenues": {
                    "units": {
                        "USD": [
                            {"end": "2026-03-31", "val": 400, "form": "10-Q", "filed": "2026-05-01"},
                        ]
                    }
                },
                "NetIncomeLoss": {
                    "units": {
                        "USD": [
                            {"end": "2026-06-30", "val": 90, "form": "10-K", "filed": "2026-08-01"},
                            {"end": "2026-03-31", "val": 70, "form": "10-Q", "filed": "2026-05-01"},
                        ]
                    }
                },
                "OperatingIncomeLoss": {
                    "units": {
                        "USD": [
                            {"end": "2026-06-30", "val": 120, "form": "10-K", "filed": "2026-08-01"},
                            {"end": "2026-03-31", "val": 95, "form": "10-Q", "filed": "2026-05-01"},
                        ]
                    }
                },
                "NetCashProvidedByUsedInOperatingActivities": {
                    "units": {
                        "USD": [
                            # Duplicate entries for the same period/form (e.g. a restated
                            # figure filed later) - the more recently filed one should win.
                            {"end": "2026-06-30", "val": 150, "form": "10-K", "filed": "2026-08-01"},
                            {"end": "2026-06-30", "val": 145, "form": "10-K", "filed": "2026-07-15"},
                        ]
                    }
                },
                "PaymentsToAcquirePropertyPlantAndEquipment": {
                    "units": {
                        "USD": [
                            {"end": "2026-06-30", "val": 40, "form": "10-K", "filed": "2026-08-01"},
                        ]
                    }
                },
            },
            "dei": {
                "EntityCommonStockSharesOutstanding": {
                    "units": {
                        "shares": [
                            {"end": "2026-06-30", "val": 1000, "form": "10-K", "filed": "2026-08-01"},
                        ]
                    }
                }
            },
        },
    }


def test_select_fact_value_uses_primary_tag_when_present():
    payload = _companyfacts_payload()
    us_gaap = payload["facts"]["us-gaap"]

    value, tag_used = select_fact_value(us_gaap, ["NetIncomeLoss", "ProfitLoss"], "2026-06-30", "10-K")

    assert value == 90
    assert tag_used == "NetIncomeLoss"


def test_select_fact_value_falls_back_to_alternate_tag():
    payload = _companyfacts_payload()
    us_gaap = payload["facts"]["us-gaap"]

    # revenue for the 10-Q period only exists under the legacy "Revenues" tag
    value, tag_used = select_fact_value(
        us_gaap,
        ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"],
        "2026-03-31",
        "10-Q",
    )

    assert value == 400
    assert tag_used == "Revenues"


def test_select_fact_value_returns_none_when_no_tag_present():
    payload = _companyfacts_payload()
    us_gaap = payload["facts"]["us-gaap"]

    value, tag_used = select_fact_value(us_gaap, ["InterestExpense", "InterestExpenseDebt"], "2026-06-30", "10-K")

    assert value is None
    assert tag_used is None


def test_select_fact_value_prefers_most_recently_filed_duplicate():
    payload = _companyfacts_payload()
    us_gaap = payload["facts"]["us-gaap"]

    value, tag_used = select_fact_value(
        us_gaap, ["NetCashProvidedByUsedInOperatingActivities"], "2026-06-30", "10-K"
    )

    assert value == 150
    assert tag_used == "NetCashProvidedByUsedInOperatingActivities"


def test_select_shares_outstanding_reads_from_dei_shares_unit():
    payload = _companyfacts_payload()
    dei = payload["facts"]["dei"]

    value, tag_used = _select_shares_outstanding(dei, "2026-06-30", "10-K")

    assert value == 1000
    assert tag_used == "EntityCommonStockSharesOutstanding"


def test_parse_companyfacts_extracts_one_row_per_fiscal_period():
    df = parse_companyfacts(_companyfacts_payload(), "AAPL")

    assert set(df["fiscal_period_end"]) == {"2026-06-30", "2026-03-31"}

    fy_row = df[df["fiscal_period_end"] == "2026-06-30"].iloc[0]
    assert fy_row["ticker"] == "AAPL"
    assert fy_row["cik"] == 320193
    assert fy_row["revenue"] == 500
    assert fy_row["revenue_tag_used"] == "RevenueFromContractWithCustomerExcludingAssessedTax"
    assert fy_row["ocf"] == 150
    assert fy_row["capex"] == 40
    assert fy_row["shares_outstanding"] == 1000

    q_row = df[df["fiscal_period_end"] == "2026-03-31"].iloc[0]
    assert q_row["revenue"] == 400
    assert q_row["revenue_tag_used"] == "Revenues"


def test_parse_companyfacts_leaves_missing_metrics_null():
    df = parse_companyfacts(_companyfacts_payload(), "AAPL")

    fy_row = df[df["fiscal_period_end"] == "2026-06-30"].iloc[0]
    assert fy_row["interest_expense"] is None
    assert fy_row["interest_expense_tag_used"] is None


def test_normalize_cik_coerces_zero_padded_string_to_int():
    # Some filers' companyfacts payload returns cik as a zero-padded string
    # instead of an int - both must normalize to the same int so concatenated
    # per-ticker frames don't end up with a mixed-type column.
    assert _normalize_cik("0002023554") == 2023554
    assert _normalize_cik(320193) == 320193
    assert _normalize_cik(None) is None


def test_parse_companyfacts_normalizes_string_cik():
    payload = _companyfacts_payload()
    payload["cik"] = "0000320193"

    df = parse_companyfacts(payload, "AAPL")

    assert df["cik"].iloc[0] == 320193
    assert pd.api.types.is_integer_dtype(df["cik"])


def test_parse_companyfacts_returns_empty_when_no_relevant_forms():
    payload = {
        "cik": 1,
        "facts": {
            "us-gaap": {
                "NetIncomeLoss": {
                    "units": {"USD": [{"end": "2026-06-30", "val": 90, "form": "4", "filed": "2026-07-01"}]}
                }
            },
            "dei": {},
        },
    }

    df = parse_companyfacts(payload, "AAPL")

    assert df.empty
