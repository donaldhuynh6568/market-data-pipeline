from __future__ import annotations

import argparse
from datetime import datetime, timedelta

import pandas as pd
from alpaca.data.historical.crypto import CryptoHistoricalDataClient
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.requests import CryptoBarsRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame

from market_data_pipeline.config import require_alpaca_credentials
from market_data_pipeline.storage import save_parquet, upsert_to_supabase
from market_data_pipeline.tickers import load_tickers


def fetch_equity_bars(symbols: list[str], start: datetime, end: datetime) -> pd.DataFrame:
    if not symbols:
        return pd.DataFrame()

    api_key, secret_key = require_alpaca_credentials()
    client = StockHistoricalDataClient(api_key, secret_key)
    request = StockBarsRequest(
        symbol_or_symbols=symbols,
        timeframe=TimeFrame.Day,
        start=start,
        end=end,
    )
    bars = client.get_stock_bars(request)
    df = bars.df.reset_index()
    df["asset_class"] = "equity"
    return df


def fetch_crypto_bars(symbols: list[str], start: datetime, end: datetime) -> pd.DataFrame:
    if not symbols:
        return pd.DataFrame()

    api_key, secret_key = require_alpaca_credentials()
    client = CryptoHistoricalDataClient(api_key, secret_key)
    request = CryptoBarsRequest(
        symbol_or_symbols=symbols,
        timeframe=TimeFrame.Day,
        start=start,
        end=end,
    )
    bars = client.get_crypto_bars(request)
    df = bars.df.reset_index()
    df["asset_class"] = "crypto"
    return df


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect daily market price data.")
    parser.add_argument("--days", type=int, default=30, help="Lookback window in days")
    args = parser.parse_args()

    tickers = load_tickers()
    end = datetime.now()
    start = end - timedelta(days=args.days)

    print(
        f"Fetching {args.days}d of daily bars for {len(tickers['equities'])} equities "
        f"and {len(tickers['crypto'])} crypto pairs..."
    )
    equity_df = fetch_equity_bars(tickers["equities"], start, end)
    crypto_df = fetch_crypto_bars(tickers["crypto"], start, end)
    combined = pd.concat([equity_df, crypto_df], ignore_index=True)

    if combined.empty:
        print("No data returned — check your ticker list and API keys.")
        return

    price_path = save_parquet(combined, "price_data.parquet")
    print(f"Saved {len(combined)} rows to {price_path}")

    records = combined.assign(timestamp=combined["timestamp"].astype(str)).to_dict(
        orient="records"
    )
    upsert_to_supabase("price_data", records, on_conflict="symbol,timestamp")


if __name__ == "__main__":
    main()
