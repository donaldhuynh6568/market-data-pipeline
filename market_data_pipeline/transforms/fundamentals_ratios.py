from __future__ import annotations

import pandas as pd

from market_data_pipeline.config import DATA_DIR

CAPEX_TREND_PERIODS = 4  # trailing quarters used to compute the CapEx % revenue trend


def compute_fcf(df: pd.DataFrame) -> pd.Series:
    """Free cash flow = operating cash flow - capital expenditures."""
    return df["ocf"] - df["capex"]


def compute_fcf_margin(df: pd.DataFrame) -> pd.Series:
    """FCF as a share of revenue."""
    fcf = compute_fcf(df)
    return fcf / df["revenue"]


def compute_cash_conversion_ratio(df: pd.DataFrame) -> pd.Series:
    """OCF / net income - a quality-of-earnings check. A falling ratio while net income
    rises suggests earnings are increasingly built on accruals rather than cash."""
    return df["ocf"] / df["net_income"]


def compute_capex_pct_revenue(df: pd.DataFrame) -> pd.Series:
    """CapEx as a share of revenue for a single period."""
    return df["capex"] / df["revenue"]


def compute_capex_pct_revenue_trend(df: pd.DataFrame, periods: int = CAPEX_TREND_PERIODS) -> pd.DataFrame:
    """Trailing change in CapEx % revenue per ticker, sorted by fiscal_period_end.

    Returns the input columns plus `capex_pct_revenue` and `capex_pct_revenue_trend`
    (the change versus `periods` fiscal periods ago, per ticker).
    """
    result = df.copy()
    result["capex_pct_revenue"] = compute_capex_pct_revenue(result)
    result = result.sort_values(["ticker", "fiscal_period_end"])
    result["capex_pct_revenue_trend"] = result.groupby("ticker")["capex_pct_revenue"].diff(periods)
    return result


def compute_net_debt(df: pd.DataFrame) -> pd.Series:
    """Net debt = total debt - cash on hand."""
    return df["total_debt"] - df["cash"]


def compute_ebitda(df: pd.DataFrame) -> pd.Series:
    """EBITDA is never a standard XBRL tag - always derived from operating income + D&A."""
    return df["operating_income"] + df["depreciation_amortization"]


def compute_net_debt_to_ebitda(df: pd.DataFrame) -> pd.Series:
    """Standard leverage ratio - the number that flags credit risk quantitatively
    rather than waiting for a CDS-spread headline."""
    net_debt = compute_net_debt(df)
    ebitda = compute_ebitda(df)
    return net_debt / ebitda


def compute_interest_coverage(df: pd.DataFrame) -> pd.Series:
    """EBIT / interest expense. A company with thin coverage is the one that actually
    suffers when rates rise; one with high coverage barely notices."""
    return df["operating_income"] / df["interest_expense"]


def compute_fundamentals_ratios(df: pd.DataFrame) -> pd.DataFrame:
    result = compute_capex_pct_revenue_trend(df)
    result["fcf"] = compute_fcf(result)
    result["fcf_margin"] = compute_fcf_margin(result)
    result["cash_conversion_ratio"] = compute_cash_conversion_ratio(result)
    result["net_debt"] = compute_net_debt(result)
    result["net_debt_to_ebitda"] = compute_net_debt_to_ebitda(result)
    result["interest_coverage"] = compute_interest_coverage(result)

    return result[
        [
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
    ]


def main() -> None:
    fundamentals_path = DATA_DIR / "fundamentals_data.parquet"
    if not fundamentals_path.exists():
        raise SystemExit(
            f"Missing {fundamentals_path}. Run the fundamentals collector first: "
            "uv run python -m market_data_pipeline.collectors.fundamentals"
        )

    df = pd.read_parquet(fundamentals_path)
    result = compute_fundamentals_ratios(df)

    out_path = DATA_DIR / "fundamentals_ratios.parquet"
    result.to_parquet(out_path, index=False)
    print(f"Saved {len(result)} fundamentals ratio rows to {out_path}")
    for _, row in result.sort_values("fiscal_period_end").iterrows():
        print(
            f"  [{row['ticker']}] {row['fiscal_period_end']} "
            f"fcf={row['fcf']:.2e} net_debt_to_ebitda={row['net_debt_to_ebitda']:.2f}"
        )


if __name__ == "__main__":
    main()
