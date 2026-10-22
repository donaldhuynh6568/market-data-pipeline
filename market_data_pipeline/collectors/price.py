from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone

import pandas as pd
from alpaca.data.historical.crypto import CryptoHistoricalDataClient
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.requests import CryptoBarsRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

from market_data_pipeline.config import require_alpaca_credentials
from market_data_pipeline.config import DATA_DIR
from market_data_pipeline.storage import merge_frames, save_parquet, upsert_to_supabase
from market_data_pipeline.tickers import load_tickers

TIMEFRAME_CHOICES: dict[str, TimeFrame] = {
    "1Day": TimeFrame.Day,
    "1Hour": TimeFrame(1, TimeFrameUnit.Hour),
    "4Hour": TimeFrame(4, TimeFrameUnit.Hour),
    "5Min": TimeFrame(5, TimeFrameUnit.Minute),
}

# The free data tier refuses "recent SIP data", so a window ending at this instant is rejected outright.
# Backing the end off past that embargo is the actual fix, and 16 minutes still includes the current bar.
# Until now this worked only by accident: datetime.now() returned naive *local* time, which the API read as
# UTC and so happened to land hours in the past.
SIP_DELAY_MINUTES = 16

# Lookback for a first run, a new ticker, or --full. The existing 1H/4H/5m files already hold two years, so the
# free tier is not capping intraday at ~60 days as this comment used to claim.
DEFAULT_DAYS = 730

# On an incremental run each symbol is re-fetched from its last stored bar minus this overlap. The overlap replaces
# the bar that was still forming when it was last fetched, and heals a short gap if a run was skipped.
OVERLAP: dict[str, timedelta] = {
    "1Day": timedelta(days=5),
    "1Hour": timedelta(days=2),
    "4Hour": timedelta(days=2),
    "5Min": timedelta(days=1),
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


# A symbol whose last stored bar trails the freshest one by more than this (halted, delisted, or a skipped ticker) is
# fetched in its own request, so it cannot drag every other symbol's request window back with it.
STALE_AFTER = timedelta(days=7)


def plan_fetch(
    existing: pd.DataFrame | None, symbols: list[str], days: int, end: datetime, overlap: timedelta
) -> list[tuple[list[str], datetime]]:
    """(symbols, start) request groups: new tickers backfill the whole window, known ones only the recent tail.

    Current symbols share one request starting at the stalest of them minus `overlap`, never earlier than the full
    window. Symbols stale by more than STALE_AFTER get their own request, and a symbol with no stored rows (a ticker
    added since the last run) gets the full window.
    """
    full_start = end - timedelta(days=days)
    if existing is None or existing.empty:
        return [(symbols, full_start)] if symbols else []

    last_bar = existing.groupby("symbol")["timestamp"].max()
    known = [s for s in symbols if s in last_bar.index]
    new = [s for s in symbols if s not in last_bar.index]
    groups: list[tuple[list[str], datetime]] = []
    if known:
        newest = last_bar[known].max()
        for members in (
            [s for s in known if last_bar[s] >= newest - STALE_AFTER],
            [s for s in known if last_bar[s] < newest - STALE_AFTER],
        ):
            if members:
                stalest = last_bar[members].min().to_pydatetime()
                groups.append((members, max(full_start, stalest - overlap)))
    if new:
        groups.append((new, full_start))
    return groups


def shrink_warning(existing: pd.DataFrame | None, days: int, end: datetime) -> str | None:
    """A --full run replaces the file, so say so when the requested window is shorter than what is stored."""
    if existing is None or existing.empty:
        return None
    cutoff = pd.Timestamp(end - timedelta(days=days))
    lost = int((existing["timestamp"] < cutoff).sum())
    if not lost:
        return None
    oldest = existing["timestamp"].min().date()
    return (
        f"--full with --days {days} drops {lost} stored bars older than {cutoff.date()} (file reaches back to "
        f"{oldest}). Re-run with a larger --days, or omit --full to keep them."
    )


def collect_bars(
    tickers: dict[str, list[str]], existing: pd.DataFrame | None, days: int, end: datetime, timeframe: TimeFrame,
    overlap: timedelta,
) -> pd.DataFrame:
    """Fetch only what the stored file is missing, for equities and crypto separately."""
    frames = []
    for kind, fetch in (("equities", fetch_equity_bars), ("crypto", fetch_crypto_bars)):
        for symbols, start in plan_fetch(existing, tickers[kind], days, end, overlap):
            print(f"  {kind}: {len(symbols)} symbols from {start:%Y-%m-%d %H:%M} UTC")
            frames.append(fetch(symbols, start, end, timeframe))
    frames = [f for f in frames if not f.empty]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect market price data.")
    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_DAYS,
        help=f"Lookback window in days (default {DEFAULT_DAYS}) for a first run, a newly added ticker or --full. "
        "Routine refreshes only fetch what is newer than the stored file.",
    )
    parser.add_argument(
        "--timeframe",
        choices=sorted(TIMEFRAME_CHOICES),
        default="1Day",
        help="Bar granularity. 1Day writes price_data.parquet (default, backward compatible); "
        "1Hour/4Hour/5Min write to their own files so they never overwrite the daily dataset.",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Re-download the whole --days window and overwrite the file. Without it, a refresh fetches only "
        "bars newer than what is already stored and appends them; --days then only sizes a first run or a new ticker.",
    )
    args = parser.parse_args()

    timeframe = TIMEFRAME_CHOICES[args.timeframe]
    tickers = load_tickers()
    end = datetime.now(timezone.utc) - timedelta(minutes=SIP_DELAY_MINUTES)

    out_filename = OUTPUT_FILENAMES[args.timeframe]
    out_path = DATA_DIR / out_filename
    stored = pd.read_parquet(out_path) if out_path.exists() else None
    if args.full and (warning := shrink_warning(stored, args.days, end)):
        print(f"WARNING: {warning}")
    known = None if args.full else stored

    print(
        f"{'Rebuilding' if known is None else 'Refreshing'} {args.timeframe} bars for {len(tickers['equities'])} "
        f"equities and {len(tickers['crypto'])} crypto pairs..."
    )
    fetched = collect_bars(tickers, known, args.days, end, timeframe, OVERLAP[args.timeframe])
    if fetched.empty:
        print("No data returned - check your ticker list and API keys. Existing file left untouched.")
        return

    combined = merge_frames(known, fetched, keys=["symbol", "timestamp"])
    price_path = save_parquet(combined, out_filename)
    added = len(combined) - (len(known) if known is not None else 0)
    print(f"Saved {len(combined)} rows to {price_path} ({len(fetched)} fetched, {added} new)")

    records = fetched.assign(timestamp=fetched["timestamp"].astype(str)).to_dict(orient="records")
    upsert_to_supabase("price_data", records, on_conflict="symbol,timestamp")


if __name__ == "__main__":
    main()
