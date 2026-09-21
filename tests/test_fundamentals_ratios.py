import pandas as pd

from market_data_pipeline.transforms.fundamentals_ratios import (
    compute_capex_pct_revenue_trend,
    compute_cash_conversion_ratio,
    compute_ebitda,
    compute_fcf,
    compute_fcf_margin,
    compute_fundamentals_ratios,
    compute_interest_coverage,
    compute_net_debt,
    compute_net_debt_to_ebitda,
)


def _single_period_row() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ticker": ["AAPL"],
            "fiscal_period_end": ["2026-06-30"],
            "revenue": [500.0],
            "net_income": [90.0],
            "operating_income": [120.0],
            "interest_expense": [10.0],
            "depreciation_amortization": [30.0],
            "ocf": [100.0],
            "capex": [30.0],
            "cash": [50.0],
            "total_debt": [200.0],
        }
    )


def test_compute_fcf():
    df = _single_period_row()
    assert compute_fcf(df).iloc[0] == 70.0


def test_compute_fcf_margin():
    df = _single_period_row()
    assert compute_fcf_margin(df).iloc[0] == 70.0 / 500.0


def test_compute_cash_conversion_ratio():
    df = _single_period_row()
    assert compute_cash_conversion_ratio(df).iloc[0] == 100.0 / 90.0


def test_compute_net_debt():
    df = _single_period_row()
    assert compute_net_debt(df).iloc[0] == 150.0


def test_compute_ebitda():
    df = _single_period_row()
    assert compute_ebitda(df).iloc[0] == 150.0


def test_compute_net_debt_to_ebitda():
    df = _single_period_row()
    assert compute_net_debt_to_ebitda(df).iloc[0] == 150.0 / 150.0


def test_compute_interest_coverage():
    df = _single_period_row()
    assert compute_interest_coverage(df).iloc[0] == 12.0


def test_compute_capex_pct_revenue_trend_across_periods():
    df = pd.DataFrame(
        {
            "ticker": ["AAPL", "AAPL", "AAPL", "AAPL", "AAPL"],
            "fiscal_period_end": [
                "2025-06-30",
                "2025-09-30",
                "2025-12-31",
                "2026-03-31",
                "2026-06-30",
            ],
            "revenue": [500.0, 500.0, 500.0, 500.0, 500.0],
            "capex": [30.0, 30.0, 30.0, 30.0, 60.0],
        }
    )

    result = compute_capex_pct_revenue_trend(df, periods=4)

    assert result[result["fiscal_period_end"] == "2025-06-30"]["capex_pct_revenue_trend"].isna().all()
    latest = result[result["fiscal_period_end"] == "2026-06-30"].iloc[0]
    # capex % revenue went from 30/500=0.06 four periods ago to 60/500=0.12 now
    assert round(latest["capex_pct_revenue_trend"], 4) == round(0.12 - 0.06, 4)


def test_compute_fundamentals_ratios_returns_expected_columns():
    df = _single_period_row()

    result = compute_fundamentals_ratios(df)

    assert list(result.columns) == [
        "ticker",
        "fiscal_period_end",
        "fcf",
        "fcf_margin",
        "cash_conversion_ratio",
        "capex_pct_revenue",
        "capex_pct_revenue_trend",
        "net_debt",
        "net_debt_to_ebitda",
        "interest_coverage",
    ]
    row = result.iloc[0]
    assert row["fcf"] == 70.0
    assert row["net_debt"] == 150.0
