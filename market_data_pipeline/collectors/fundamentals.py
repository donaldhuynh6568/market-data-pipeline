from __future__ import annotations

import argparse
import time

import pandas as pd

from market_data_pipeline.collectors.sec import (
    TICKER_MAP_URL,
    build_ticker_cik_map,
    fetch_json,
)
from market_data_pipeline.config import require_sec_user_agent
from market_data_pipeline.storage import save_parquet, upsert_to_supabase
from market_data_pipeline.tickers import load_equity_category

COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
REQUEST_DELAY_SECONDS = 0.2
RELEVANT_FORMS = {"10-K", "10-Q"}

# Per-metric XBRL tag fallback lists, tried in order. Different filers tag economically
# identical concepts differently (a known, permanent XBRL characteristic), so each metric
# has a primary tag plus alternates observed in the wild. EBIT/EBITDA are never standard
# tags anywhere and are always derived, not looked up here.
TAG_FALLBACKS: dict[str, list[str]] = {
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
    ],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "operating_income": ["OperatingIncomeLoss"],
    "interest_expense": ["InterestExpense", "InterestExpenseDebt", "InterestAndDebtExpense"],
    "depreciation_amortization": [
        "DepreciationDepletionAndAmortization",
        "DepreciationAmortizationAndAccretionNet",
    ],
    "ocf": [
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ],
    "capex": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsForCapitalImprovements",
        "PaymentsToAcquireProductiveAssets",
    ],
    "cash": [
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        "Cash",
    ],
    "long_term_debt": ["LongTermDebtNoncurrent", "LongTermDebt"],
    "short_term_debt": ["LongTermDebtCurrent", "ShortTermBorrowings", "DebtCurrent"],
}
SHARES_OUTSTANDING_TAGS = ["EntityCommonStockSharesOutstanding"]


def select_fact_value(
    us_gaap_facts: dict, tag_candidates: list[str], fiscal_period_end: str, form: str
) -> tuple[float | None, str | None]:
    """Pick a fact's value for a given period/form out of several candidate tags.

    Returns (value, tag_used). Walks tag_candidates in order and returns the first
    one that has an entry matching fiscal_period_end and form; None/None if nothing
    matches any candidate tag.
    """
    for tag in tag_candidates:
        entries = us_gaap_facts.get(tag, {}).get("units", {}).get("USD", [])
        matches = [e for e in entries if e.get("end") == fiscal_period_end and e.get("form") == form]
        if not matches:
            continue
        # Prefer the most recently filed entry when a period has duplicate/overlapping
        # reports (e.g. restatements, or a 10-Q reporting both quarterly and YTD figures
        # under the same tag).
        best = max(matches, key=lambda e: e.get("filed", ""))
        return best.get("val"), tag
    return None, None


def _select_shares_outstanding(
    dei_facts: dict, fiscal_period_end: str, form: str
) -> tuple[float | None, str | None]:
    """Shares outstanding lives under the dei taxonomy with unit "shares", not "USD"."""
    for tag in SHARES_OUTSTANDING_TAGS:
        entries = dei_facts.get(tag, {}).get("units", {}).get("shares", [])
        matches = [e for e in entries if e.get("end") == fiscal_period_end and e.get("form") == form]
        if not matches:
            continue
        best = max(matches, key=lambda e: e.get("filed", ""))
        return best.get("val"), tag
    return None, None


def _normalize_cik(raw_cik: object) -> int | None:
    """companyfacts returns `cik` as either an int or a zero-padded string
    (e.g. "0002023554") depending on the filer - always coerce to int."""
    if raw_cik is None:
        return None
    return int(raw_cik)


def _fiscal_periods(us_gaap_facts: dict) -> list[tuple[str, str]]:
    """Collect the distinct (fiscal_period_end, form) pairs present across all tags,
    restricted to 10-K/10-Q filings."""
    periods: set[tuple[str, str]] = set()
    for fact in us_gaap_facts.values():
        for entry in fact.get("units", {}).get("USD", []):
            form = entry.get("form")
            end = entry.get("end")
            if form in RELEVANT_FORMS and end:
                periods.add((end, form))
    return sorted(periods)


def parse_companyfacts(payload: dict, ticker: str) -> pd.DataFrame:
    """Extract one row per fiscal period (10-K/10-Q) from an SEC XBRL companyfacts payload."""
    facts = payload.get("facts", {})
    us_gaap = facts.get("us-gaap", {})
    dei = facts.get("dei", {})

    periods = _fiscal_periods(us_gaap)
    if not periods:
        return pd.DataFrame()

    rows = []
    for fiscal_period_end, form in periods:
        row: dict = {
            "ticker": ticker,
            # companyfacts returns `cik` as either an int or a zero-padded string
            # depending on the filer - normalize so concatenated frames across
            # tickers don't end up with a mixed-type column (breaks parquet writes).
            "cik": _normalize_cik(payload.get("cik")),
            "fiscal_period_end": fiscal_period_end,
            "form": form,
        }
        for metric, tags in TAG_FALLBACKS.items():
            value, tag_used = select_fact_value(us_gaap, tags, fiscal_period_end, form)
            row[metric] = value
            row[f"{metric}_tag_used"] = tag_used

        shares_value, shares_tag = _select_shares_outstanding(dei, fiscal_period_end, form)
        row["shares_outstanding"] = shares_value
        row["shares_outstanding_tag_used"] = shares_tag

        long_term = row.get("long_term_debt")
        short_term = row.get("short_term_debt")
        if long_term is not None or short_term is not None:
            row["total_debt"] = (long_term or 0) + (short_term or 0)
        else:
            row["total_debt"] = None

        rows.append(row)

    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect cash-flow / balance-sheet / income-statement facts from SEC XBRL companyfacts."
    )
    parser.parse_args()

    user_agent = require_sec_user_agent()
    tickers = load_equity_category("large_cap")
    if not tickers:
        print("No large_cap tickers configured in tickers.yaml, nothing to do.")
        return

    cik_by_ticker = build_ticker_cik_map(fetch_json(TICKER_MAP_URL, user_agent))

    frames = []
    for ticker in tickers:
        cik = cik_by_ticker.get(ticker.upper())
        if cik is None:
            print(f"  [{ticker}] no CIK found in SEC ticker map, skipping")
            continue

        print(f"Fetching XBRL companyfacts for {ticker}...")
        try:
            payload = fetch_json(COMPANYFACTS_URL.format(cik=cik), user_agent)
        except Exception as exc:  # pragma: no cover - external API
            print(f"  [{ticker}] fetch failed, skipping: {exc}")
            continue

        df = parse_companyfacts(payload, ticker)
        if not df.empty:
            frames.append(df)
        else:
            print(f"  [{ticker}] no matching fundamentals facts")
        time.sleep(REQUEST_DELAY_SECONDS)

    if not frames:
        print("No fundamentals data collected.")
        return

    combined = pd.concat(frames, ignore_index=True)
    out_path = save_parquet(combined, "fundamentals_data.parquet")
    print(f"Saved {len(combined)} fundamentals rows to {out_path}")

    upsert_to_supabase(
        "fundamentals_data",
        combined.to_dict(orient="records"),
        on_conflict="ticker,fiscal_period_end,form",
    )


if __name__ == "__main__":
    main()
