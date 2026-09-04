from __future__ import annotations

import argparse
from datetime import datetime, timedelta

import pandas as pd
from alpaca.data.historical.crypto import CryptoHistoricalDataClient
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.requests import CryptoBarsRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

from market_data_pipeline.config import require_alpaca_credentials
from market_data_pipeline.storage import save_parquet, upsert_to_supabase
from market_data_pipeline.tickers import load_tickers

TIMEFRAME_CHOICES: dict[str, TimeFrame] = {
    "1Day": TimeFrame.Day,
    "1Hour": TimeFrame(1, TimeFrameUnit.Hour),
    "4Hour": TimeFrame(4, TimeFrameUnit.Hour),
    "5Min": TimeFrame(5, TimeFrameUnit.Minute),
}

OUTPUT_FILENAMES: dict[str, str] = {
    "1Day": "price_data.parquet",
    "1Hour": "price_data_1h.parquet",
    "4Hour": "price_data_4h.parquet",
    "5Min": "price_data_5m.parquet",
}


def fetch_equity_bars(
    symbols: list[str], start: datetime, end: datetime, timeframe: TimeFrame
) -> pd.DataFrame:
    if not symbols:
        return pd.DataFrame()

    api_key, secret_key = require_alpaca_credentials()
    client = StockHistoricalDataClient(api_key, secret_key)
    request = StockBarsRequest(
        symbol_or_symbols=symbols,
        timeframe=timeframe,
        start=start,
        end=end,
    )
    bars = client.get_stock_bars(request)
    df = bars.df.reset_index()
    df["asset_class"] = "equity"
    return df


def fetch_crypto_bars(
    symbols: list[str], start: datetime, end: datetime, timeframe: TimeFrame
) -> pd.DataFrame:
    if not symbols:
        return pd.DataFrame()

    api_key, secret_key = require_alpaca_credentials()
    client = CryptoHistoricalDataClient(api_key, secret_key)
    request = CryptoBarsRequest(
        symbol_or_symbols=symbols,
        timeframe=timeframe,
        start=start,
        end=end,
    )
    bars = client.get_crypto_bars(request)
    df = bars.df.reset_index()
    df["asset_class"] = "crypto"
    return df


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect market price data.")
    parser.add_argument("--days", type=int, default=30, help="Lookback window in days")
    parser.add_argument(
        "--timeframe",
        choices=sorted(TIMEFRAME_CHOICES),
        default="1Day",
        help="Bar granularity. 1Day writes price_data.parquet (default, backward compatible); "
        "1Hour/4Hour/5Min write to their own files so they never overwrite the daily dataset.",
    )
    args = parser.parse_args()

    timeframe = TIMEFRAME_CHOICES[args.timeframe]
    tickers = load_tickers()
    end = datetime.now()
    start = end - timedelta(days=args.days)

    print(
        f"Fetching {args.days}d of {args.timeframe} bars for {len(tickers['equities'])} equities "
        f"and {len(tickers['crypto'])} crypto pairs..."
    )
    equity_df = fetch_equity_bars(tickers["equities"], start, end, timeframe)
    crypto_df = fetch_crypto_bars(tickers["crypto"], start, end, timeframe)
    combined = pd.concat([equity_df, crypto_df], ignore_index=True)

    if combined.empty:
        print("No data returned — check your ticker list and API keys.")
        return

    out_filename = OUTPUT_FILENAMES[args.timeframe]
    price_path = save_parquet(combined, out_filename)
    print(f"Saved {len(combined)} rows to {price_path}")

    records = combined.assign(timestamp=combined["timestamp"].astype(str)).to_dict(
        orient="records"
    )
    upsert_to_supabase("price_data", records, on_conflict="symbol,timestamp")


if __name__ == "__main__":
    main()
