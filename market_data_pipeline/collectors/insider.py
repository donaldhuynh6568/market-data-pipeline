from __future__ import annotations

import argparse
import time
import xml.etree.ElementTree as ET

import pandas as pd

from market_data_pipeline.collectors.sec import (
    TICKER_MAP_URL,
    build_ticker_cik_map,
    fetch_json,
    fetch_text,
    select_document_by_root_tag,
)
from market_data_pipeline.config import DATA_DIR, require_sec_user_agent
from market_data_pipeline.tickers import load_equity_category

ATOM_NS = "{http://www.w3.org/2005/Atom}"
FILING_ATOM_URL = (
    "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik:010d}&type=4"
    "&dateb=&owner=include&count={count}&output=atom"
)
FILING_INDEX_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession_nodash}/index.json"
FILING_DOC_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession_nodash}/{filename}"
DEFAULT_FILING_COUNT = 10
REQUEST_DELAY_SECONDS = 0.2


def fetch_recent_form4_filings(cik: int, user_agent: str, count: int = DEFAULT_FILING_COUNT) -> list[dict]:
    text = fetch_text(FILING_ATOM_URL.format(cik=cik, count=count), user_agent)
    root = ET.fromstring(text)

    filings = []
    for entry in root.findall(f"{ATOM_NS}entry"):
        content = entry.find(f"{ATOM_NS}content")
        if content is None:
            continue
        accession = content.findtext(f"{ATOM_NS}accession-number")
        filing_date = content.findtext(f"{ATOM_NS}filing-date")
        if accession:
            filings.append({"accession_number": accession, "filing_date": filing_date})
    return filings


def fetch_ownership_document_xml(cik: int, accession_nodash: str, user_agent: str) -> str | None:
    """Locate a Form 4 filing's ownership XML by root tag, not filename (filenames vary)."""
    index_payload = fetch_json(FILING_INDEX_URL.format(cik=cik, accession_nodash=accession_nodash), user_agent)
    names = [
        item["name"]
        for item in index_payload.get("directory", {}).get("item", [])
        if item.get("name", "").endswith(".xml")
    ]
    candidates = {
        name: fetch_text(FILING_DOC_URL.format(cik=cik, accession_nodash=accession_nodash, filename=name), user_agent)
        for name in names
    }
    return select_document_by_root_tag(candidates, "ownershipDocument")


def parse_form4_transactions(xml_text: str, ticker: str) -> pd.DataFrame:
    """Parse non-derivative (plain stock buy/sell) transactions. Derivative transactions
    (option grants, RSU vesting) are out of scope for this pass."""
    root = ET.fromstring(xml_text)

    issuer = root.find("issuer")
    owner = root.find("reportingOwner")
    owner_id = owner.find("reportingOwnerId") if owner is not None else None
    relationship = owner.find("reportingOwnerRelationship") if owner is not None else None

    non_derivative_table = root.find("nonDerivativeTable")
    if non_derivative_table is None:
        return pd.DataFrame()

    rows = []
    for txn in non_derivative_table.findall("nonDerivativeTransaction"):
        rows.append(
            {
                "ticker": ticker,
                "issuer_cik": issuer.findtext("issuerCik") if issuer is not None else None,
                "issuer_trading_symbol": issuer.findtext("issuerTradingSymbol") if issuer is not None else None,
                "reporting_owner": owner_id.findtext("rptOwnerName") if owner_id is not None else None,
                "is_officer": relationship.findtext("isOfficer") if relationship is not None else None,
                "is_director": relationship.findtext("isDirector") if relationship is not None else None,
                "officer_title": relationship.findtext("officerTitle") if relationship is not None else None,
                "transaction_date": txn.findtext("transactionDate/value"),
                "transaction_code": txn.findtext("transactionCoding/transactionCode"),
                "shares": txn.findtext("transactionAmounts/transactionShares/value"),
                "price_per_share": txn.findtext("transactionAmounts/transactionPricePerShare/value"),
                "acquired_disposed_code": txn.findtext(
                    "transactionAmounts/transactionAcquiredDisposedCode/value"
                ),
                "shares_owned_after": txn.findtext(
                    "postTransactionAmounts/sharesOwnedFollowingTransaction/value"
                ),
            }
        )

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    for column in ("shares", "price_per_share", "shares_owned_after"):
        df[column] = pd.to_numeric(df[column], errors="coerce")
    return df


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect SEC Form 4 insider transactions.")
    parser.add_argument(
        "--count", type=int, default=DEFAULT_FILING_COUNT, help="Recent filings to check per ticker."
    )
    args = parser.parse_args()

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
            print(f"  [{ticker}] no CIK found, skipping")
            continue

        print(f"Fetching Form 4 filings for {ticker}...")
        try:
            filings = fetch_recent_form4_filings(cik, user_agent, args.count)
        except Exception as exc:  # pragma: no cover - external API
            print(f"  [{ticker}] failed to list filings, skipping: {exc}")
            continue

        for filing in filings:
            accession_nodash = filing["accession_number"].replace("-", "")
            time.sleep(REQUEST_DELAY_SECONDS)
            try:
                xml_text = fetch_ownership_document_xml(cik, accession_nodash, user_agent)
            except Exception as exc:  # pragma: no cover - external API
                print(f"  [{ticker}] failed to fetch filing {filing['accession_number']}: {exc}")
                continue
            if xml_text is None:
                continue

            df = parse_form4_transactions(xml_text, ticker)
            if not df.empty:
                frames.append(df)
            time.sleep(REQUEST_DELAY_SECONDS)

    if not frames:
        print("No insider transactions collected.")
        return

    combined = pd.concat(frames, ignore_index=True)
    out_path = DATA_DIR / "insider_trades.parquet"
    combined.to_parquet(out_path, index=False)
    print(f"Saved {len(combined)} insider transaction rows to {out_path}")


if __name__ == "__main__":
    main()
