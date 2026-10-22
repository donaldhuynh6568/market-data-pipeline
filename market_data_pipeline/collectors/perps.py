from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from urllib.request import Request, urlopen

import pandas as pd

from market_data_pipeline.storage import merge_parquet, save_parquet
from market_data_pipeline.tickers import load_tickers

DEFAULT_VENUES = ["binance", "hyperliquid"]
ALL_VENUES = ["binance", "hyperliquid", "bybit"]
REQUEST_TIMEOUT_SECONDS = 30


def base_asset(pair: str) -> str:
    """'SOL/USD' -> 'SOL'."""
    return pair.split("/")[0].upper()


def _http_json(url: str, payload: dict | None = None):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"User-Agent": "Mozilla/5.0"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=headers)
    with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        return json.load(response)


def _row(symbol: str, venue: str, funding_rate, open_interest, mark_price, timestamp: datetime) -> dict:
    mark = float(mark_price) if mark_price is not None else None
    oi = float(open_interest) if open_interest is not None else None
    return {
        "symbol": symbol,
        "venue": venue,
        "timestamp": timestamp,
        "funding_rate": float(funding_rate) if funding_rate is not None else None,
        "open_interest": oi,
        "mark_price": mark,
        "open_interest_usd": oi * mark if oi is not None and mark is not None else None,
    }


def parse_binance(pair: str, premium_index: dict, open_interest: dict, now: datetime) -> dict:
    return _row(
        pair,
        "binance",
        premium_index.get("lastFundingRate"),
        open_interest.get("openInterest"),
        premium_index.get("markPrice"),
        now,
    )


def parse_bybit(pair: str, ticker_response: dict, now: datetime) -> dict:
    items = ticker_response["result"]["list"]
    if not items:
        raise ValueError("Bybit returned no ticker rows")
    item = items[0]
    return _row(pair, "bybit", item.get("fundingRate"), item.get("openInterest"), item.get("markPrice"), now)


def parse_hyperliquid(pairs: list[str], response: list, now: datetime) -> list[dict]:
    """Hyperliquid returns [meta, asset_ctxs]; ctxs are positionally aligned with meta['universe']."""
    meta, contexts = response
    ctx_by_coin = {asset["name"].upper(): ctx for asset, ctx in zip(meta["universe"], contexts)}
    rows = []
    for pair in pairs:
        ctx = ctx_by_coin.get(base_asset(pair))
        if ctx is None:
            print(f"  [perps] hyperliquid has no perp for {pair}, skipping")
            continue
        rows.append(_row(pair, "hyperliquid", ctx.get("funding"), ctx.get("openInterest"), ctx.get("markPx"), now))
    return rows


def fetch_binance(pairs: list[str], now: datetime) -> list[dict]:
    rows = []
    for pair in pairs:
        symbol = f"{base_asset(pair)}USDT"
        premium = _http_json(f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={symbol}")
        interest = _http_json(f"https://fapi.binance.com/fapi/v1/openInterest?symbol={symbol}")
        rows.append(parse_binance(pair, premium, interest, now))
    return rows


def fetch_bybit(pairs: list[str], now: datetime) -> list[dict]:
    rows = []
    for pair in pairs:
        symbol = f"{base_asset(pair)}USDT"
        response = _http_json(f"https://api.bybit.com/v5/market/tickers?category=linear&symbol={symbol}")
        rows.append(parse_bybit(pair, response, now))
    return rows


def fetch_hyperliquid(pairs: list[str], now: datetime) -> list[dict]:
    response = _http_json("https://api.hyperliquid.xyz/info", {"type": "metaAndAssetCtxs"})
    return parse_hyperliquid(pairs, response, now)


VENUE_FETCHERS = {
    "binance": fetch_binance,
    "hyperliquid": fetch_hyperliquid,
    "bybit": fetch_bybit,
}


def collect_perps(pairs: list[str], venues: list[str]) -> pd.DataFrame:
    """Fetch funding/OI per venue; a venue that errors (e.g. geo-blocked) is skipped, not fatal."""
    now = datetime.now(timezone.utc)
    rows: list[dict] = []
    for venue in venues:
        try:
            venue_rows = VENUE_FETCHERS[venue](pairs, now)
        except Exception as exc:  # pragma: no cover - external API
            print(f"  [perps] {venue} failed, skipping: {exc}")
            continue
        print(f"  [perps] {venue}: {len(venue_rows)} rows")
        rows.extend(venue_rows)
    return pd.DataFrame(rows)


def prune_snapshots(df: pd.DataFrame, keep_days: int | None, column: str = "timestamp") -> pd.DataFrame:
    """Drop rows older than `keep_days` (None keeps everything)."""
    if keep_days is None or df.empty:
        return df
    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=keep_days)
    return df[pd.to_datetime(df[column], utc=True) >= cutoff].reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect crypto perpetual funding rates and open interest.")
    parser.add_argument(
        "--venues",
        nargs="*",
        choices=ALL_VENUES,
        default=DEFAULT_VENUES,
        help="Venues to query (Binance/Bybit are geo-blocked in some regions, e.g. the US).",
    )
    parser.add_argument(
        "--keep-days",
        type=int,
        default=None,
        help="Drop stored snapshots older than this many days (default: keep everything).",
    )
    args = parser.parse_args()

    pairs = load_tickers()["crypto"]
    df = collect_perps(pairs, args.venues)
    if df.empty:
        print("No perp data returned from any venue.")
        return

    # Each run is one snapshot at `now`; appending builds the funding/OI history instead of replacing it.
    merged = prune_snapshots(merge_parquet(df, "perps_data.parquet", ["symbol", "venue", "timestamp"]), args.keep_days)
    path = save_parquet(merged, "perps_data.parquet")
    print(f"Saved {len(merged)} perp rows to {path} ({len(df)} added this run)")


if __name__ == "__main__":
    main()
