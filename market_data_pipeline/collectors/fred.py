from __future__ import annotations

import argparse
import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

from market_data_pipeline.config import require_fred_api_key
from market_data_pipeline.raw import raw_run

FRED_BASE_URL = "https://api.stlouisfed.org/fred/series/observations"

DEFAULT_SERIES = [
    # Federal Reserve
    "FEDFUNDS", "DFF", "SOFR",
    # Treasury yields
    "DGS2", "DGS5", "DGS10", "DGS30",
    # Inflation
    "CPIAUCSL", "CPILFESL", "PCEPI", "PCEPILFE",
    # Employment
    "UNRATE", "PAYEMS", "ICSA",
    # Economic growth
    "GDP", "GDPC1", "INDPRO", "RSAFS",
    # Liquidity
    "WALCL", "M2SL", "RRPONTSYD", "WTREGEN",
    # Dollar
    "DTWEXBGS",
    # Volatility
    "VIXCLS",
]


def fetch_series_observations(series_id: str, api_key: str) -> dict:
    params = urlencode({"series_id": series_id, "api_key": api_key, "file_type": "json"})
    request = Request(
        f"{FRED_BASE_URL}?{params}", headers={"User-Agent": "market-data-pipeline"}
    )
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def parse_fred_observations(payload: dict, series_id: str) -> pd.DataFrame:
    observations = payload.get("observations", [])
    if not observations:
        return pd.DataFrame()

    df = pd.DataFrame(observations)[["date", "value"]].copy()
    df["series_id"] = series_id
    # FRED encodes missing values as "."
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    return df


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect FRED macroeconomic series.")
    parser.add_argument(
        "--series", nargs="*", default=DEFAULT_SERIES, help="FRED series IDs to collect."
    )
    args = parser.parse_args()

    api_key = require_fred_api_key()

    with raw_run("fred", "series", {"series": args.series}, date_col="date") as run:
        collect_series(args.series, api_key, run)


def collect_series(series_ids: list[str], api_key: str, run) -> None:
    frames = []
    for series_id in series_ids:
        print(f"Fetching {series_id}...")
        try:
            payload = fetch_series_observations(series_id, api_key)
        except Exception as exc:  # pragma: no cover - external API
            print(f"  [{series_id}] fetch failed, skipping: {exc}")
            continue

        df = parse_fred_observations(payload, series_id)
        if df.empty:
            print(f"  [{series_id}] no observations returned")
            continue
        frames.append(df)

    if not frames:
        print("No FRED data collected.")
        return

    run.write(pd.concat(frames, ignore_index=True))


if __name__ == "__main__":
    main()
