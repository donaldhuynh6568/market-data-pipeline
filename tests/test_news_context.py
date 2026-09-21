import pandas as pd

from market_data_pipeline.reports import rating_rules as rr
from market_data_pipeline.reports.news_context import (
    NOT_COLLECTED_TEXT,
    NO_DATA_TEXT,
    NewsConfig,
    news_context,
    news_flag,
    select_headlines,
    window_end,
)


def _news(rows: list[tuple]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"id": i, "ticker": t, "headline": h, "source": "benzinga", "url": f"https://x/{i}",
             "created_at": pd.Timestamp(c, tz="UTC"), "relevance": rel}
            for i, t, h, c, rel in rows
        ]
    )


NEWS = _news([
    ("1", "AVGO", "Broadcom downgraded by analyst", "2026-09-20 15:00", "HIGH"),
    ("2", "AVGO", "Chip roundup mentioning Broadcom", "2026-09-20 16:00", "LOW"),
    ("3", "AVGO", "Broadcom unveils new chip", "2026-09-19 09:00", "HIGH"),
    ("4", "AVGO", "Broadcom old story", "2026-09-05 09:00", "HIGH"),
    ("5", "XLE", "Oil majors upgrade outlook", "2026-09-18 09:00", "HIGH"),
])
CFG = NewsConfig()


def test_window_end_is_the_newest_article_time():
    assert window_end(NEWS) == pd.Timestamp("2026-09-20 16:00", tz="UTC")
    assert window_end(pd.DataFrame()) is None


def test_select_headlines_high_only_newest_first_within_lookback():
    items = select_headlines(NEWS, "AVGO", window_end(NEWS), CFG)

    assert [i["headline"] for i in items] == ["Broadcom downgraded by analyst", "Broadcom unveils new chip"]
    assert items[0]["date"] == "09-20" and items[0]["source"] == "Benzinga"


def test_select_headlines_respects_the_per_name_limit():
    items = select_headlines(NEWS, "AVGO", window_end(NEWS), NewsConfig(headlines_per_name=1))

    assert len(items) == 1


def test_flag_matrix_uses_the_rating_direction():
    negative, positive = ["Broadcom downgraded by analyst"], ["Oil majors upgrade outlook"]

    assert news_flag(negative, rr.BUY, CFG) == 'REVIEW NEWS: negative headline ("downgrade")'
    assert news_flag(negative, rr.WATCH, CFG).startswith("REVIEW NEWS: negative")
    assert news_flag(positive, rr.SELL, CFG) == 'REVIEW NEWS: positive headline ("upgrade")'
    assert news_flag(positive, rr.BUY, CFG) is None  # supportive news on a BUY is not a conflict
    assert news_flag(negative, rr.SELL, CFG) is None
    assert news_flag(negative, rr.HOLD, CFG) is None
    assert news_flag(["Broadcom unveils new chip"], rr.BUY, CFG) is None


def test_keywords_match_at_word_start_only():
    assert news_flag(["Firm probed over pricing"], rr.BUY, CFG) is not None  # prefix: probe -> probed
    assert news_flag(["Unprobeable claims"], rr.BUY, CFG) is None  # not at a word boundary


def test_news_context_text_and_flag():
    text, flag = news_context(NEWS, "AVGO", rr.BUY, CFG)

    assert flag == 'REVIEW NEWS: negative headline ("downgrade")'
    assert text.splitlines()[0] == flag
    assert "09-20 Benzinga: Broadcom downgraded by analyst" in text


def test_news_context_states_missing_data_explicitly():
    assert news_context(None, "AVGO", rr.BUY, CFG) == (NO_DATA_TEXT, None)
    assert news_context(NEWS, "AMD", rr.BUY, CFG) == (NOT_COLLECTED_TEXT, None)
    only_low = _news([("9", "AMD", "Roundup", "2026-09-20 15:00", "LOW")])
    assert news_context(only_low, "AMD", rr.BUY, CFG)[0].startswith("No relevant news in the last 7 days")


def test_long_headlines_are_truncated():
    long = _news([("1", "AVGO", "Broadcom " + "x" * 300, "2026-09-20 15:00", "HIGH")])

    text, _ = news_context(long, "AVGO", rr.HOLD, NewsConfig(max_headline_chars=50))

    assert text.endswith("…") and len(text.split(": ", 1)[1]) == 50


def test_news_context_is_deterministic():
    assert news_context(NEWS, "AVGO", rr.BUY, CFG) == news_context(NEWS.sample(frac=1, random_state=3), "AVGO", rr.BUY, CFG)


def test_high_relevance_headlines_come_before_medium_which_only_fill_spare_slots():
    news = _news([
        ("1", "AVGO", "Tagged-only story, newest", "2026-09-20 18:00", "MEDIUM"),
        ("2", "AVGO", "Broadcom named story", "2026-09-19 09:00", "HIGH"),
        ("3", "AVGO", "Tagged-only story, older", "2026-09-19 12:00", "MEDIUM"),
    ])

    items = select_headlines(news, "AVGO", window_end(news), NewsConfig(headlines_per_name=2))

    assert [i["headline"] for i in items] == ["Tagged-only story, newest", "Broadcom named story"]  # HIGH kept, newest MEDIUM fills, shown newest first
    assert select_headlines(news, "AVGO", window_end(news), NewsConfig(headlines_per_name=1))[0]["headline"] == "Broadcom named story"


def test_only_headlines_naming_the_asset_can_raise_a_flag():
    tag_only = _news([("1", "AVGO", "Analyst downgrade hits another chip name", "2026-09-20 15:00", "MEDIUM")])
    named = _news([("1", "AVGO", "Broadcom downgraded by analyst", "2026-09-20 15:00", "HIGH")])

    assert news_context(tag_only, "AVGO", rr.BUY, CFG)[1] is None
    assert "Analyst downgrade hits another chip name" in news_context(tag_only, "AVGO", rr.BUY, CFG)[0]
    assert news_context(named, "AVGO", rr.BUY, CFG)[1] is not None


def test_low_relevance_articles_are_never_shown():
    only_low = _news([("9", "AMD", "Roundup", "2026-09-20 15:00", "LOW")])

    assert "Roundup" not in news_context(only_low, "AMD", rr.BUY, CFG)[0]
