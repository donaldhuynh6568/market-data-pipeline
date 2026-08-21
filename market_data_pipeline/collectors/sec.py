from __future__ import annotations

import argparse
import json
import time
import xml.etree.ElementTree as ET
from urllib.request import Request, urlopen

import pandas as pd

from market_data_pipeline.config import require_sec_user_agent
from market_data_pipeline.storage import save_parquet
from market_data_pipeline.tickers import load_tickers

TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
DEFAULT_FORMS = ["10-K", "10-Q", "8-K"]
REQUEST_DELAY_SECONDS = 0.2


def fetch_text(url: str, user_agent: str) -> str:
    request = Request(url, headers={"User-Agent": user_agent})
    with urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


def fetch_json(url: str, user_agent: str) -> dict:
    request = Request(url, headers={"User-Agent": user_agent})
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def build_ticker_cik_map(payload: dict) -> dict[str, int]:
    return {row["ticker"].upper(): row["cik_str"] for row in payload.values()}


def select_document_by_root_tag(candidates: dict[str, str], target_tag: str) -> str | None:
    """Pick the XML document whose root tag matches, out of several candidate documents.

    EDGAR filings don't name their documents consistently (a 13F's information table might
    be `46994.xml` while the cover page is `primary_doc.xml`), so the target document has to
    be identified by content, not filename.
    """
    for xml_text in candidates.values():
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            continue
        if root.tag == target_tag:
            return xml_text
    return None


def parse_submissions(payload: dict, ticker: str, forms: list[str]) -> pd.DataFrame:
    recent = payload.get("filings", {}).get("recent", {})
    df = pd.DataFrame(recent)
    if df.empty:
        return df

    df = df[df["form"].isin(forms)].copy()
    if df.empty:
        return df

    df["ticker"] = ticker
    df["cik"] = payload.get("cik")
    df = df.rename(columns={"reportDate": "observation_date", "filingDate": "release_date"})

    keep = [
        "ticker",
        "cik",
        "form",
        "observation_date",
        "release_date",
        "accessionNumber",
        "primaryDocument",
    ]
    return df[[c for c in keep if c in df.columns]]


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect SEC EDGAR filing metadata.")
    parser.add_argument(
        "--forms", nargs="*", default=DEFAULT_FORMS, help="Form types to keep."
    )
    args = parser.parse_args()

    user_agent = require_sec_user_agent()
    tickers = load_tickers()["equities"]

    cik_by_ticker = build_ticker_cik_map(fetch_json(TICKER_MAP_URL, user_agent))

    frames = []
    for ticker in tickers:
        cik = cik_by_ticker.get(ticker.upper())
        if cik is None:
            print(f"  [{ticker}] no CIK found in SEC ticker map, skipping")
            continue

        print(f"Fetching SEC filings for {ticker}...")
        try:
            payload = fetch_json(SUBMISSIONS_URL.format(cik=cik), user_agent)
        except Exception as exc:  # pragma: no cover - external API
            print(f"  [{ticker}] fetch failed, skipping: {exc}")
            continue

        df = parse_submissions(payload, ticker, args.forms)
        if not df.empty:
            frames.append(df)
        else:
            print(f"  [{ticker}] no matching filings")
        time.sleep(REQUEST_DELAY_SECONDS)

    if not frames:
        print("No SEC filings collected.")
        return

    combined = pd.concat(frames, ignore_index=True)
    out_path = save_parquet(combined, "sec_filings.parquet")
    print(f"Saved {len(combined)} filing rows to {out_path}")


if __name__ == "__main__":
    main()
