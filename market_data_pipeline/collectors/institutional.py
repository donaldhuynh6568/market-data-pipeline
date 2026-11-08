from __future__ import annotations

import argparse
import xml.etree.ElementTree as ET

import pandas as pd

from market_data_pipeline.collectors.sec import fetch_json, fetch_text, select_document_by_root_tag
from market_data_pipeline.config import require_sec_user_agent
from market_data_pipeline.raw import raw_run

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
FILING_INDEX_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession_nodash}/index.json"
FILING_DOC_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession_nodash}/{filename}"
INFO_TABLE_NS = "{http://www.sec.gov/edgar/document/thirteenf/informationtable}"

# CIKs confirmed against live SEC EDGAR filings during development.
DEFAULT_WATCHLIST = {
    "Berkshire Hathaway": "0001067983",
    "Bridgewater Associates": "0001350694",
    "Scion Asset Management": "0001649339",
    "Pershing Square Capital Management": "0001336528",
    "Renaissance Technologies": "0001037389",
}


def _tag(name: str) -> str:
    return f"{INFO_TABLE_NS}{name}"


def find_latest_13f_filing(submissions_payload: dict) -> dict | None:
    recent = submissions_payload.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accession_numbers = recent.get("accessionNumber", [])
    report_dates = recent.get("reportDate", [])
    filing_dates = recent.get("filingDate", [])

    for i, form in enumerate(forms):
        if form == "13F-HR":
            return {
                "accession_number": accession_numbers[i],
                "report_date": report_dates[i] if i < len(report_dates) else None,
                "filing_date": filing_dates[i] if i < len(filing_dates) else None,
            }
    return None


def fetch_information_table_xml(cik: int, accession_nodash: str, user_agent: str) -> str | None:
    """Locate a 13F filing's information table by root tag, not filename — the cover-page
    document (usually primary_doc.xml) and the holdings table aren't consistently named."""
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
    return select_document_by_root_tag(candidates, _tag("informationTable"))


def parse_information_table(xml_text: str, manager_name: str, report_date: str | None) -> pd.DataFrame:
    root = ET.fromstring(xml_text)

    rows = []
    for info_table in root.findall(_tag("infoTable")):
        rows.append(
            {
                "manager": manager_name,
                "report_date": report_date,
                "name_of_issuer": info_table.findtext(_tag("nameOfIssuer")),
                "title_of_class": info_table.findtext(_tag("titleOfClass")),
                "cusip": info_table.findtext(_tag("cusip")),
                "value": info_table.findtext(_tag("value")),
                "shares": info_table.findtext(f"{_tag('shrsOrPrnAmt')}/{_tag('sshPrnamt')}"),
                "share_type": info_table.findtext(f"{_tag('shrsOrPrnAmt')}/{_tag('sshPrnamtType')}"),
                "investment_discretion": info_table.findtext(_tag("investmentDiscretion")),
            }
        )

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df["shares"] = pd.to_numeric(df["shares"], errors="coerce")
    return df


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect latest Form 13F holdings for a curated investor watchlist."
    )
    parser.add_argument(
        "--only", nargs="*", default=None, help="Subset of DEFAULT_WATCHLIST names to collect."
    )
    args = parser.parse_args()

    user_agent = require_sec_user_agent()
    watchlist = DEFAULT_WATCHLIST
    if args.only:
        watchlist = {name: cik for name, cik in DEFAULT_WATCHLIST.items() if name in args.only}
        for name in set(args.only) - set(watchlist):
            print(f"  [{name}] not in DEFAULT_WATCHLIST, skipping")

    with raw_run("sec", "13f", {"watchlist": watchlist}, date_col="report_date") as run:
        collect_holdings(watchlist, user_agent, run)


def collect_holdings(watchlist: dict[str, str], user_agent: str, run) -> None:
    frames = []
    for manager_name, cik_str in watchlist.items():
        cik = int(cik_str)
        print(f"Fetching 13F holdings for {manager_name}...")
        try:
            submissions = fetch_json(SUBMISSIONS_URL.format(cik=cik), user_agent)
        except Exception as exc:  # pragma: no cover - external API
            print(f"  [{manager_name}] failed to fetch submissions, skipping: {exc}")
            continue

        filing = find_latest_13f_filing(submissions)
        if filing is None:
            print(f"  [{manager_name}] no 13F-HR filings found")
            continue

        accession_nodash = filing["accession_number"].replace("-", "")
        try:
            xml_text = fetch_information_table_xml(cik, accession_nodash, user_agent)
        except Exception as exc:  # pragma: no cover - external API
            print(f"  [{manager_name}] failed to fetch information table: {exc}")
            continue

        if xml_text is None:
            print(f"  [{manager_name}] could not locate an information table document")
            continue

        df = parse_information_table(xml_text, manager_name, filing["report_date"])
        if not df.empty:
            frames.append(df)

    if not frames:
        print("No 13F holdings collected.")
        return

    run.write(pd.concat(frames, ignore_index=True))


if __name__ == "__main__":
    main()
