from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone

import pandas as pd
from alpaca.data.historical.crypto import CryptoHistoricalDataClient
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.enums import Adjustment
from alpaca.data.requests import CryptoBarsRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

from market_data_pipeline.config import require_alpaca_credentials
from market_data_pipeline.raw import raw_max, raw_run
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

# Lookback for a first run, a new ticker, or --full, unless --start is given.
DEFAULT_DAYS = 730

# How far back --probe asks for, to find the real history depth the account can get.
PROBE_START = date(2000, 1, 1)
PROBE_SYMBOLS = {"equities": ["SPY"], "crypto": ["BTC/USD"]}

# On an incremental run each symbol is re-fetched from its last stored bar minus this overlap. The overlap replaces
# the bar that was still forming when it was last fetched, and heals a short gap if a run was skipped.
OVERLAP: dict[str, timedelta] = {
    "1Day": timedelta(days=5),
    "1Hour": timedelta(days=2),
    "4Hour": timedelta(days=2),
    "5Min": timedelta(days=1),
}

RAW_SOURCE = "alpaca"
RAW_DATASETS: dict[str, str] = {
    "1Day": "bars_1d",
    "1Hour": "bars_1h",
    "4Hour": "bars_4h",
    "5Min": "bars_5m",
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
        # Split- and dividend-adjusted. Unadjusted bars turn every split into a fake crash in the return series.
        adjustment=Adjustment.ALL,
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


def collect_bars(
    tickers: dict[str, list[str]], existing: pd.DataFrame | None, days: int, end: datetime, timeframe: TimeFrame,
    overlap: timedelta,
) -> pd.DataFrame:
    """Fetch only what raw is missing, for equities and crypto separately."""
    frames = []
    for kind, fetch in (("equities", fetch_equity_bars), ("crypto", fetch_crypto_bars)):
        for symbols, start in plan_fetch(existing, tickers[kind], days, end, overlap):
            print(f"  {kind}: {len(symbols)} symbols from {start:%Y-%m-%d %H:%M} UTC")
            frames.append(fetch(symbols, start, end, timeframe))
    frames = [f for f in frames if not f.empty]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def probe_history_depth(end: datetime) -> None:
    """Print the earliest daily bar Alpaca returns for one stock and one crypto pair. Writes nothing."""
    start = datetime.combine(PROBE_START, datetime.min.time(), tzinfo=timezone.utc)
    for kind, fetch in (("equities", fetch_equity_bars), ("crypto", fetch_crypto_bars)):
        for symbol in PROBE_SYMBOLS[kind]:
            df = fetch([symbol], start, end, TIMEFRAME_CHOICES["1Day"])
            if df.empty:
                print(f"  {symbol}: no bars returned since {PROBE_START}")
            else:
                print(f"  {symbol}: earliest bar {df['timestamp'].min():%Y-%m-%d}, {len(df)} daily bars")


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect Alpaca bars into the raw layer.")
    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_DAYS,
        help=f"Lookback window in days (default {DEFAULT_DAYS}) for a first run, a newly added ticker or --full. "
        "Routine refreshes only fetch what is newer than what raw already holds.",
    )
    parser.add_argument(
        "--start",
        type=date.fromisoformat,
        help="Backfill from this date (YYYY-MM-DD) instead of --days. Implies --full.",
    )
    parser.add_argument(
        "--timeframe",
        choices=sorted(TIMEFRAME_CHOICES),
        default="1Day",
        help="Bar granularity; each lands in its own raw dataset (alpaca/bars_1d, bars_1h, ...).",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Re-download the whole window for every symbol, ignoring what raw already holds. Raw only ever "
        "appends, so this never loses data; staging keeps the newest copy of each bar.",
    )
    parser.add_argument(
        "--probe", action="store_true", help="Report how far back daily history goes for SPY and BTC/USD, then exit."
    )
    args = parser.parse_args()

    end = datetime.now(timezone.utc) - timedelta(minutes=SIP_DELAY_MINUTES)
    if args.probe:
        probe_history_depth(end)
        return

    days = (end.date() - args.start).days if args.start else args.days
    timeframe = TIMEFRAME_CHOICES[args.timeframe]
    dataset = RAW_DATASETS[args.timeframe]
    tickers = load_tickers()
    known = None if (args.full or args.start) else raw_max(RAW_SOURCE, dataset, "symbol", "timestamp")

    print(
        f"{'Backfilling' if known is None or known.empty else 'Refreshing'} {args.timeframe} bars for "
        f"{len(tickers['equities'])} equities and {len(tickers['crypto'])} crypto pairs..."
    )
    params = {"timeframe": args.timeframe, "days": days, "full": bool(args.full or args.start), "end": end}
    with raw_run(RAW_SOURCE, dataset, params, date_col="timestamp") as run:
        fetched = collect_bars(tickers, known, days, end, timeframe, OVERLAP[args.timeframe])
        if fetched.empty:
            print("No data returned - check your ticker list and API keys.")
            return
        run.write(fetched)


if __name__ == "__main__":
    main()
