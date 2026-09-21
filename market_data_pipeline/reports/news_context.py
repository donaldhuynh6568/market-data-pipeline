from __future__ import annotations

import re
from dataclasses import dataclass, field

import pandas as pd

from market_data_pipeline.reports import rating_rules as rr

DEFAULT_NEGATIVE = [
    "downgrade", "probe", "investigation", "lawsuit", "sued", "recall", "plunge", "slump", "subpoena", "fraud",
    "layoff", "misses", "cuts guidance", "lowers guidance", "guidance cut", "warning",
]
DEFAULT_POSITIVE = ["upgrade", "beats", "raises guidance", "raised guidance", "price target raised", "surge"]

NO_DATA_TEXT = "News data not loaded"
NOT_COLLECTED_TEXT = "No news collected for this ticker (run collectors.news)"


@dataclass(frozen=True)
class NewsConfig:
    lookback_days: int = 7
    headlines_per_name: int = 3
    max_headline_chars: int = 140
    negative_keywords: list[str] = field(default_factory=lambda: list(DEFAULT_NEGATIVE))
    positive_keywords: list[str] = field(default_factory=lambda: list(DEFAULT_POSITIVE))


def news_config_from_rules(rules: dict) -> NewsConfig:
    section = rules.get("news", {})
    allowed = {"lookback_days", "headlines_per_name", "max_headline_chars", "negative_keywords", "positive_keywords"}
    return NewsConfig(**{k: v for k, v in section.items() if k in allowed})


def window_end(news: pd.DataFrame) -> pd.Timestamp | None:
    """The newest article time in the data, so results depend only on the parquet (not on today's date)."""
    if news is None or news.empty:
        return None
    return pd.to_datetime(news["created_at"], utc=True).max()


def select_headlines(news: pd.DataFrame, ticker: str, end: pd.Timestamp, config: NewsConfig) -> list[dict]:
    """Newest articles for `ticker` in the lookback window ending at `end`, up to `headlines_per_name`.

    Articles whose headline names the asset (HIGH) come first, newest first; any remaining slots are filled with
    MEDIUM articles (tagged to the ticker and only a few others). LOW roundups are never shown.
    """
    rows = news[(news["ticker"] == ticker) & (news["relevance"].isin(["HIGH", "MEDIUM"]))].copy()
    if rows.empty:
        return []
    rows["_created"] = pd.to_datetime(rows["created_at"], utc=True)
    rows = rows[rows["_created"] > end - pd.Timedelta(days=config.lookback_days)]
    rows["_rank"] = rows["relevance"].map({"HIGH": 0, "MEDIUM": 1})
    rows = rows.sort_values(["_rank", "_created", "id"], ascending=[True, False, True]).head(config.headlines_per_name)
    rows = rows.sort_values(["_created", "id"], ascending=[False, True])  # show newest first
    return [
        {
            "date": row["_created"].strftime("%m-%d"),
            "source": str(row["source"]).title(),
            "headline": row["headline"],
            "url": row["url"],
            "relevance": row["relevance"],
        }
        for _, row in rows.iterrows()
    ]


def _first_match(headlines: list[str], keywords: list[str]) -> str | None:
    for headline in headlines:
        lowered = headline.lower()
        for keyword in keywords:
            # Prefix match so "downgrade" also catches "downgraded"/"downgrades"; the start must be a word boundary.
            if re.search(rf"\b{re.escape(keyword.lower())}", lowered):
                return keyword
    return None


def news_flag(headlines: list[str], rating: str, config: NewsConfig) -> str | None:
    """Flag news that cuts against the rating. A keyword screen, not sentiment; the rating itself never changes."""
    if rating in (rr.BUY, rr.WATCH):
        hit = _first_match(headlines, config.negative_keywords)
        if hit:
            return f'REVIEW NEWS: negative headline ("{hit}")'
    if rating == rr.SELL:
        hit = _first_match(headlines, config.positive_keywords)
        if hit:
            return f'REVIEW NEWS: positive headline ("{hit}")'
    return None


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def news_context(
    news: pd.DataFrame | None, ticker: str, rating: str, config: NewsConfig
) -> tuple[str, str | None]:
    """(text for the News column, conflict flag). Text is the flag (if any) followed by the newest headlines."""
    if news is None:
        return NO_DATA_TEXT, None
    if news[news["ticker"] == ticker].empty:
        return NOT_COLLECTED_TEXT, None

    end = window_end(news)
    items = select_headlines(news, ticker, end, config)
    if not items:
        return f"No relevant news in the last {config.lookback_days} days", None

    # Only headlines that name the asset can raise a flag; MEDIUM (tag-only) items are context, not evidence.
    flag = news_flag([item["headline"] for item in items if item["relevance"] == "HIGH"], rating, config)
    lines = [f"{item['date']} {item['source']}: {_truncate(item['headline'], config.max_headline_chars)}" for item in items]
    return "\n".join(([flag] if flag else []) + lines), flag
