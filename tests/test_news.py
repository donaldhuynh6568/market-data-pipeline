from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd

from market_data_pipeline.collectors.news import (
    classify_relevance,
    clean_text,
    fetch_news,
    headline_mentions,
    merge_news,
)

NOW = datetime(2026, 9, 20, 12, tzinfo=timezone.utc)


def _article(id_, headline, symbols, created="2026-09-19T10:00:00Z", updated=None, source="benzinga", summary=""):
    return SimpleNamespace(
        id=id_, headline=headline, symbols=symbols, source=source, summary=summary, author="A",
        url=f"https://example.com/{id_}", created_at=pd.Timestamp(created), updated_at=pd.Timestamp(updated or created),
    )


class _FakeClient:
    def __init__(self, by_symbol, fail_on=()):
        self.by_symbol, self.fail_on, self.requests = by_symbol, set(fail_on), []

    def get_news(self, request):
        self.requests.append(request)
        if request.symbols in self.fail_on:
            raise RuntimeError("boom")
        return SimpleNamespace(data={"news": self.by_symbol.get(request.symbols, [])})


def test_clean_text_unescapes_html_entities():
    assert clean_text("Semiconductors &amp; Equipment") == "Semiconductors & Equipment"


def test_clean_text_removes_replacement_characters_and_collapses_whitespace():
    assert clean_text("VanEck\ufffds  Nick \n Frasse\ufffd ") == "VanEcks Nick Frasse"
    assert clean_text(None) == ""


def test_relevance_by_tag_count_and_headline():
    tags5 = ["AMZN", "AVGO", "MSFT", "NVDA", "ORCL"]

    assert classify_relevance("AVGO", "Nvidia Stock Keeps Failing", tags5, 3) == "LOW"  # roundup
    assert classify_relevance("AVGO", "Broadcom Stock Remains in Bear Territory", ["AVGO", "C", "IT"], 3) == "MEDIUM"
    assert classify_relevance("AVGO", "AVGO rallies as Nvidia falls", tags5, 3) == "HIGH"  # ticker in headline
    assert classify_relevance("AVGO", "Some other story", ["NVDA"], 3) == "LOW"  # not tagged to it


def test_crypto_relevance_uses_base_symbol_pair_tags_and_aliases():
    assert classify_relevance("BTC/USD", "Bitcoin Price Stabilizes", ["BTC", "BTCUSD"], 3) == "HIGH"
    assert classify_relevance("BTC/USD", "ETF inflows jump", ["BTC", "BTCUSD"], 3) == "MEDIUM"
    assert classify_relevance("BTC/USD", "XRP Price Prediction", ["BTCUSD", "ETHUSD", "RLUSDUSD", "XRP", "XRPUSD"], 3) == "LOW"
    assert classify_relevance("SOL/USD", "Solana forms a bullish flag", ["SOLUSD", "BTCUSD", "ETHUSD", "ARB"], 3) == "HIGH"
    assert headline_mentions("ETH/USD", "Ether jumps 6%")
    assert not headline_mentions("ETH/USD", "Together we rise")  # word boundary, not substring


def test_fetch_news_builds_one_row_per_article_and_ticker_and_skips_failures():
    client = _FakeClient(
        {"AVGO": [_article("1", "Broadcom up", ["AVGO"]), _article("2", "Chip roundup", ["A", "B", "C", "D", "AVGO"])]},
        fail_on={"BAD"},
    )

    df = fetch_news(client, ["AVGO", "BAD", "AMD"], days=7, limit=50, now=NOW)

    assert df["ticker"].tolist() == ["AVGO", "AVGO"]
    assert df["relevance"].tolist() == ["MEDIUM", "LOW"]
    assert df["n_tagged"].tolist() == [1, 5]
    assert df.loc[1, "tagged_symbols"] == "A,B,C,D,AVGO"
    assert [r.symbols for r in client.requests] == ["AVGO", "BAD", "AMD"]  # a failure does not stop later symbols
    request = client.requests[0]
    assert request.limit == 50 and request.include_content is False
    assert request.start == NOW - pd.Timedelta(days=7)


def _rows(*items):
    return pd.DataFrame(
        [
            {"id": i, "ticker": t, "headline": h, "created_at": pd.Timestamp(c), "updated_at": pd.Timestamp(u)}
            for i, t, h, c, u in items
        ]
    )


def test_merge_dedupes_on_id_and_ticker_keeping_the_latest_update():
    existing = _rows(("1", "AVGO", "old headline", "2026-09-19", "2026-09-19"))
    new = _rows(("1", "AVGO", "edited headline", "2026-09-19", "2026-09-20"), ("1", "AMD", "same article, other pick", "2026-09-19", "2026-09-19"))

    merged = merge_news(existing, new, keep_days=90, now=NOW)

    assert len(merged) == 2
    assert merged.loc[merged["ticker"] == "AVGO", "headline"].iloc[0] == "edited headline"
    assert set(merged["ticker"]) == {"AVGO", "AMD"}


def test_merge_keeps_older_picks_and_prunes_by_keep_days():
    existing = _rows(("1", "XLE", "kept from an earlier run", "2026-09-10", "2026-09-10"), ("2", "XLE", "ancient", "2026-05-01", "2026-05-01"))
    new = _rows(("3", "AVGO", "fresh", "2026-09-19", "2026-09-19"))

    merged = merge_news(existing, new, keep_days=30, now=NOW)

    assert sorted(merged["id"]) == ["1", "3"]


def test_merge_with_nothing_returns_an_empty_frame():
    assert merge_news(None, pd.DataFrame(), keep_days=90, now=NOW).empty


def test_company_name_alias_makes_a_multi_ticker_article_relevant():
    tags5 = ["AMZN", "AVGO", "MSFT", "NVDA", "ORCL"]

    assert classify_relevance("AVGO", "What Is Going on With Broadcom Stock on Friday?", tags5, 3) == "LOW"
    assert classify_relevance("AVGO", "What Is Going on With Broadcom Stock on Friday?", tags5, 3, ["Broadcom"]) == "HIGH"
    assert classify_relevance("AVGO", "Nvidia Stock Keeps Failing to Retest Its Record High", tags5, 3, ["Broadcom"]) == "LOW"


def test_fetch_news_applies_aliases_per_ticker():
    article = _article("9", "Broadcom lands a deal", ["A", "B", "C", "D", "AVGO"])
    client = _FakeClient({"AVGO": [article]})

    with_alias = fetch_news(client, ["AVGO"], 7, 50, aliases={"AVGO": ["Broadcom"]}, now=NOW)
    without = fetch_news(client, ["AVGO"], 7, 50, now=NOW)

    assert with_alias["relevance"].tolist() == ["HIGH"] and without["relevance"].tolist() == ["LOW"]


def test_refetch_replaces_a_stored_row_with_the_same_updated_at():
    old = _rows(("1", "AVGO", "old classification", "2026-09-19", "2026-09-19"))
    new = _rows(("1", "AVGO", "re-fetched", "2026-09-19", "2026-09-19"))

    assert merge_news(old, new, keep_days=90, now=NOW)["headline"].tolist() == ["re-fetched"]
