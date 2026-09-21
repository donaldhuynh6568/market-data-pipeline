from __future__ import annotations

import re

DEFAULT_MODEL = "claude-opus-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_OUTPUT_TOKENS = 1024

SYSTEM_PROMPT = (
    "You edit one-paragraph trading-report notes. You are given a rule-based note whose facts, numbers, rating and "
    "target were computed deterministically. Rewrite it as 2-3 clear, natural sentences for a trader. Keep every number "
    "exactly as given, keep the meaning of the rating, and do not add facts, numbers, tickers or opinions that are not in "
    "the note. Reply with the rewritten note only."
)

_NUMBER = re.compile(r"\d[\d,]*\.?\d*")


def numbers_in(text: str) -> set[str]:
    """Numeric tokens in a note, normalised so '9,821' and '9821' compare equal."""
    return {token.replace(",", "").rstrip(".") for token in _NUMBER.findall(text)}


def is_faithful(rule_note: str, rewritten: str) -> bool:
    """A rewrite is accepted only if it keeps every number from the rule-based note."""
    return bool(rewritten.strip()) and numbers_in(rule_note) <= numbers_in(rewritten)


def _make_client():
    try:
        import anthropic
    except ImportError as exc:
        raise SystemExit(
            "--llm-notes needs the anthropic package. Run with: "
            "uv run --with anthropic python -m market_data_pipeline.reports.confluence_report --llm-notes"
        ) from exc
    return anthropic.Anthropic()


def rewrite_note(client, model: str, ticker: str, rating: str, rule_note: str) -> str | None:
    """Ask Claude to rewrite one note; None means keep the rule-based note (any failure, refusal, or unfaithful rewrite)."""
    try:
        response = client.beta.messages.create(
            model=model,
            max_tokens=MAX_OUTPUT_TOKENS,
            betas=[FALLBACK_BETA],
            fallbacks="default",
            thinking={"type": "adaptive"},
            output_config={"effort": "low"},
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": f"Ticker: {ticker}\nRating: {rating}\nNote: {rule_note}"}],
        )
    except Exception as exc:  # noqa: BLE001 - best effort; the rule-based note is always a valid fallback
        print(f"  [llm-notes] {ticker}: request failed, keeping rule-based note: {exc}")
        return None

    if response.stop_reason != "end_turn":
        print(f"  [llm-notes] {ticker}: stop_reason={response.stop_reason}, keeping rule-based note")
        return None
    text = "".join(block.text for block in response.content if block.type == "text").strip()
    if not is_faithful(rule_note, text):
        print(f"  [llm-notes] {ticker}: rewrite dropped a number from the rule-based note, keeping it")
        return None
    return text


def rewrite_notes(rows, model: str = DEFAULT_MODEL, client=None) -> dict[str, str]:
    """{ticker: rewritten note} for rows whose rewrite passed the faithfulness check. Ratings are never touched."""
    client = client or _make_client()
    rewritten: dict[str, str] = {}
    for row in rows:
        text = rewrite_note(client, model, row.ticker, row.rating.rating, row.note)
        if text:
            rewritten[row.ticker] = text
    return rewritten
