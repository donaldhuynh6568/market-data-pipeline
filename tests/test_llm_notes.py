from types import SimpleNamespace

from market_data_pipeline.reports import rating_rules as rr
from market_data_pipeline.reports.llm_notes import is_faithful, numbers_in, rewrite_note, rewrite_notes


class _FakeMessages:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def _client(outcome):
    messages = _FakeMessages(outcome)
    return SimpleNamespace(beta=SimpleNamespace(messages=messages)), messages


def _response(text, stop_reason="end_turn"):
    return SimpleNamespace(stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=text)])


NOTE = "Primary target 600 (9,821 call OI). Already 1.3% through reported wall (550)."


def test_numbers_are_normalised():
    assert numbers_in(NOTE) == {"600", "9821", "1.3", "550"}


def test_faithful_rewrite_keeps_every_number():
    assert is_faithful(NOTE, "AMD is 1.3% above its 550 wall; 600 holds 9821 call contracts.")
    assert not is_faithful(NOTE, "AMD is above its wall and targets 600.")
    assert not is_faithful(NOTE, "   ")


def test_rewrite_note_accepts_a_faithful_rewrite_and_asks_for_low_effort():
    client, messages = _client(_response("Through the 550 wall by 1.3%, with 9,821 call OI at 600."))

    text = rewrite_note(client, "claude-opus-5", "AMD", rr.BUY, NOTE)

    assert text == "Through the 550 wall by 1.3%, with 9,821 call OI at 600."
    call = messages.calls[0]
    assert call["model"] == "claude-opus-5" and call["output_config"] == {"effort": "low"}
    assert "Rating: BUY" in call["messages"][0]["content"]


def test_rewrite_note_falls_back_on_errors_refusals_and_dropped_numbers():
    assert rewrite_note(_client(RuntimeError("boom"))[0], "m", "AMD", rr.BUY, NOTE) is None
    assert rewrite_note(_client(_response("x 600", stop_reason="refusal"))[0], "m", "AMD", rr.BUY, NOTE) is None
    assert rewrite_note(_client(_response("Targets 600."))[0], "m", "AMD", rr.BUY, NOTE) is None


def test_rewrite_notes_only_returns_accepted_rewrites():
    row = SimpleNamespace(ticker="AMD", rating=rr.Rating(rr.BUY, rr.R_CONFIRMED), note=NOTE)
    client, _ = _client(_response("Targets 600."))  # drops numbers -> rejected

    assert rewrite_notes([row], client=client) == {}
