from __future__ import annotations

import argparse
import html
import re
from datetime import datetime, timedelta, timezone

import pandas as pd
from alpaca.data.historical.news import NewsClient
from alpaca.data.requests import NewsRequest

from market_data_pipeline.config import DATA_DIR, require_alpaca_credentials
from market_data_pipeline.picks import RULES_PATH, load_picks, load_rules
from market_data_pipeline.storage import save_parquet, upsert_to_supabase

OUTPUT_FILENAME = "news_data.parquet"
DEFAULT_DAYS = 7
DEFAULT_LIMIT = 50
DEFAULT_KEEP_DAYS = 90
DEFAULT_MAX_TAGGED_FOR_HIGH = 3

# Names used to spot a crypto asset in a headline (article tags use the base ticker, e.g. BTC / BTCUSD).
CRYPTO_ALIASES = {
    "BTC": ["bitcoin"],
    "ETH": ["ethereum", "ether"],
    "SOL": ["solana"],
}

COLUMNS = [
    "id", "ticker", "headline", "summary", "source", "url", "author",
    "created_at", "updated_at", "tagged_symbols", "n_tagged", "relevance",
]


def clean_text(value) -> str:
    """Unescape HTML entities (&amp;), drop U+FFFD replacement characters, and collapse whitespace."""
    if value is None:
        return ""
    return re.sub(r"\s+", " ", html.unescape(str(value)).replace("\ufffd", "")).strip()


def base_symbol(symbol: str) -> str:
    """'BTC/USD' -> 'BTC'; equity tickers pass through."""
    return symbol.split("/")[0].upper()


def tagged_matches(ticker: str, tagged: list[str]) -> bool:
    base = base_symbol(ticker)
    tags = {t.upper() for t in tagged}
    return base in tags or base + "USD" in tags


def headline_mentions(ticker: str, headline: str, aliases: list[str] | None = None) -> bool:
    """True when the headline names the ticker, a crypto alias, or a company-name alias from the rules file."""
    base = base_symbol(ticker)
    names = [base.lower(), *CRYPTO_ALIASES.get(base, []), *[a.lower() for a in (aliases or [])]]
    lowered = headline.lower()
    return any(re.search(rf"\b{re.escape(name)}\b", lowered) for name in names)


def classify_relevance(
    ticker: str, headline: str, tagged: list[str], max_tagged_for_high: int, aliases: list[str] | None = None
) -> str:
    """HIGH: the headline names the asset. MEDIUM: tagged to it and to at most `max_tagged_for_high` symbols. Else LOW.

    Multi-ticker roundups (an Nvidia piece tagged with five symbols) rate LOW for each of their tags. The feed
    has no company names for equities, so a headline only matches by name for tickers with an alias in the
    rules file (`news.aliases`); otherwise it must contain the ticker itself.
    """
    if headline_mentions(ticker, headline, aliases):
        return "HIGH"
    if tagged_matches(ticker, tagged) and len(tagged) <= max_tagged_for_high:
        return "MEDIUM"
    return "LOW"


def article_rows(
    ticker: str, articles, max_tagged_for_high: int = DEFAULT_MAX_TAGGED_FOR_HIGH, aliases: list[str] | None = None
) -> list[dict]:
    rows = []
    for article in articles:
        tagged = [str(s) for s in (article.symbols or [])]
        headline = clean_text(article.headline)
        rows.append(
            {
                "id": str(article.id),
                "ticker": ticker,
                "headline": headline,
                "summary": clean_text(article.summary),
                "source": clean_text(article.source),
                "url": article.url,
                "author": clean_text(article.author),
                "created_at": pd.Timestamp(article.created_at),
                "updated_at": pd.Timestamp(article.updated_at),
                "tagged_symbols": ",".join(tagged),
                "n_tagged": len(tagged),
                "relevance": classify_relevance(ticker, headline, tagged, max_tagged_for_high, aliases),
            }
        )
    return rows


def fetch_news(
    client,
    symbols: list[str],
    days: int,
    limit: int,
    max_tagged_for_high: int = DEFAULT_MAX_TAGGED_FOR_HIGH,
    now: datetime | None = None,
    aliases: dict[str, list[str]] | None = None,
) -> pd.DataFrame:
    """One row per (article, requested ticker). A ticker that errors is skipped, not fatal.

    alpaca-py pages internally up to `limit`, so no page-token handling is needed here.
    """
    end = now or datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    rows: list[dict] = []
    for symbol in symbols:
        try:
            result = client.get_news(
                NewsRequest(
                    symbols=symbol, start=start, end=end, limit=limit, include_content=False, sort="desc"
                )
            )
        except Exception as exc:  # pragma: no cover - external API
            print(f"  [news] {symbol} failed, skipping: {exc}")
            continue
        articles = result.data.get("news", [])
        print(f"  [news] {symbol}: {len(articles)} articles")
        rows.extend(article_rows(symbol, articles, max_tagged_for_high, (aliases or {}).get(symbol)))
    return pd.DataFrame(rows, columns=COLUMNS)


def merge_news(existing: pd.DataFrame | None, new: pd.DataFrame, keep_days: int, now: datetime | None = None) -> pd.DataFrame:
    """Append new rows, keep one row per (id, ticker) with the latest updated_at, and prune old articles."""
    frames = [f for f in (existing, new) if f is not None and not f.empty]
    if not frames:
        return pd.DataFrame(columns=COLUMNS)
    combined = pd.concat(frames, ignore_index=True)
    combined = combined.sort_values("updated_at", kind="stable").drop_duplicates(["id", "ticker"], keep="last")
    cutoff = pd.Timestamp(now or datetime.now(timezone.utc)) - pd.Timedelta(days=keep_days)
    combined = combined[pd.to_datetime(combined["created_at"], utc=True) >= cutoff]
    return combined.sort_values(["ticker", "created_at"], ascending=[True, False]).reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect Alpaca (Benzinga) news for your picks.")
    parser.add_argument(
        "--symbols",
        nargs="*",
        default=None,
        help="Tickers to fetch (crypto as BTC/USD). Default: the confluence report's picks "
        "(focus list in confluence_report_rules.yaml plus ACCELERATING names in momentum.parquet).",
    )
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS, help="How far back each run looks")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="Max articles per ticker per run")
    parser.add_argument(
        "--keep-days", type=int, default=DEFAULT_KEEP_DAYS, help="Drop stored articles older than this many days"
    )
    args = parser.parse_args()

    symbols = args.symbols if args.symbols else load_picks()
    if not symbols:
        raise SystemExit("No tickers to fetch. Pass --symbols or add names to the focus list in the rules file.")

    news_rules = load_rules(RULES_PATH).get("news", {})
    max_tagged = news_rules.get("max_tagged_for_high", DEFAULT_MAX_TAGGED_FOR_HIGH)
    aliases = news_rules.get("aliases", {})
    api_key, secret_key = require_alpaca_credentials()
    print(f"Fetching {args.days}d of news for {len(symbols)} tickers: {', '.join(symbols)}")
    new = fetch_news(NewsClient(api_key, secret_key), symbols, args.days, args.limit, max_tagged, aliases=aliases)
    if new.empty:
        print("No news returned; leaving the existing file untouched.")
        return

    path = DATA_DIR / OUTPUT_FILENAME
    existing = pd.read_parquet(path) if path.exists() else None
    merged = merge_news(existing, new, args.keep_days)
    save_parquet(merged, OUTPUT_FILENAME)
    print(f"Saved {len(merged)} article rows ({len(new)} fetched this run) to {path}")

    records = new.assign(
        created_at=new["created_at"].astype(str), updated_at=new["updated_at"].astype(str)
    ).to_dict(orient="records")
    upsert_to_supabase("news_data", records, on_conflict="id,ticker")


if __name__ == "__main__":
    main()
