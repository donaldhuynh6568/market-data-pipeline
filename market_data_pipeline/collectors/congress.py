from __future__ import annotations

import argparse
import json
from urllib.request import Request, urlopen

import pandas as pd

from market_data_pipeline.config import DATA_DIR
from market_data_pipeline.tickers import load_tickers

SENATE_TRANSACTIONS_URL = (
    "https://raw.githubusercontent.com/timothycarambat/senate-stock-watcher-data"
    "/master/aggregate/all_transactions.json"
)

# Confirmed during development: this source's most recent transaction is
# 2020-12-02. It is not a live feed of current congressional trading.
DATA_COVERAGE_NOTE = "Senate Stock Watcher source covers 2012-06-14 through 2020-12-02 only (not current)."


def fetch_senate_transactions() -> list[dict]:
    request = Request(SENATE_TRANSACTIONS_URL, headers={"User-Agent": "market-data-pipeline"})
    with urlopen(request, timeout=60) as response:
        return json.loads(response.read())


def parse_senate_transactions(payload: list[dict], tickers: list[str]) -> pd.DataFrame:
    df = pd.DataFrame(payload)
    if df.empty:
        return df

    ticker_set = {t.upper() for t in tickers}
    df = df[df["ticker"] != "--"].copy()
    df = df[df["ticker"].str.upper().isin(ticker_set)]
    return df.reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect historical Senate trading disclosures (2012-2020 only)."
    )
    parser.add_argument("--tickers", nargs="*", default=None, help="Ticker symbols to keep.")
    args = parser.parse_args()

    tickers = args.tickers or load_tickers()["equities"]

    print(f"NOTE: {DATA_COVERAGE_NOTE}")
    print("Fetching Senate Stock Watcher data...")
    payload = fetch_senate_transactions()

    filtered = parse_senate_transactions(payload, tickers)
    if filtered.empty:
        print("No matching congressional trades found for the configured tickers.")
        return

    out_path = DATA_DIR / "congress_trades.parquet"
    filtered.to_parquet(out_path, index=False)
    print(f"Saved {len(filtered)} congressional trade rows to {out_path}")


if __name__ == "__main__":
    main()
