from __future__ import annotations

import argparse
import json
from urllib.request import Request, urlopen

import pandas as pd

from market_data_pipeline.config import DATA_DIR
from market_data_pipeline.tickers import load_futures_tickers

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval={interval}&range={range_}"

# Matches min_volume_per_5min_candle_contracts in
# reference/orderflow_strategy_rules.yaml exactly.
VOLUME_FLOOR_CONTRACTS = 20000

DEFAULT_DAYS = 5


def fetch_yahoo_chart(symbol: str, interval: str = "5m", range_: str = "5d") -> dict:
    url = CHART_URL.format(symbol=symbol, interval=interval, range_=range_)
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def parse_chart_response(payload: dict, symbol: str) -> pd.DataFrame:
    results = payload.get("chart", {}).get("result")
    if not results:
        return pd.DataFrame()

    result = results[0]
    timestamps = result.get("timestamp", [])
    quote = result.get("indicators", {}).get("quote", [{}])[0]

    df = pd.DataFrame(
        {
            "symbol": symbol,
            "timestamp": pd.to_datetime(timestamps, unit="s", utc=True),
            "open": quote.get("open", []),
            "high": quote.get("high", []),
            "low": quote.get("low", []),
            "close": quote.get("close", []),
            "volume": quote.get("volume", []),
        }
    )

    # The most recent bar is always still forming (volume reported as 0) — drop it
    # rather than report a fake "low volume" reading for a bar that hasn't closed yet.
    if not df.empty and df["volume"].iloc[-1] == 0:
        df = df.iloc[:-1]

    return df.reset_index(drop=True)


def add_volume_floor_flag(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["volume_floor_met"] = df["volume"] >= VOLUME_FLOOR_CONTRACTS
    return df


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect 5-minute futures volume from Yahoo Finance (free, unofficial, ~10min delayed)."
    )
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS, help="Lookback window in days.")
    parser.add_argument(
        "--tickers", nargs="*", default=None, help="Futures tickers to collect (Yahoo format, e.g. MNQ=F)."
    )
    args = parser.parse_args()

    tickers = args.tickers or load_futures_tickers()
    if not tickers:
        print("No futures tickers configured in tickers.yaml, nothing to do.")
        return

    frames = []
    for symbol in tickers:
        print(f"Fetching {symbol}...")
        try:
            payload = fetch_yahoo_chart(symbol, range_=f"{args.days}d")
        except Exception as exc:  # pragma: no cover - external API
            print(f"  [{symbol}] fetch failed, skipping: {exc}")
            continue

        df = parse_chart_response(payload, symbol)
        if df.empty:
            print(f"  [{symbol}] no bars returned")
            continue
        frames.append(add_volume_floor_flag(df))

    if not frames:
        print("No futures volume data collected.")
        return

    combined = pd.concat(frames, ignore_index=True)
    out_path = DATA_DIR / "futures_volume_5m.parquet"
    combined.to_parquet(out_path, index=False)
    print(f"Saved {len(combined)} rows to {out_path}")


if __name__ == "__main__":
    main()
