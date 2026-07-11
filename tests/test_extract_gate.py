"""
tests/test_extract_gate.py — stop_reason gate on the text-extraction call (M3.1).

Mocked Anthropic client, no network.
"""

import json
from types import SimpleNamespace

import pytest
import src.extract as extract
from src.extract import MAX_TOKENS, RETRY_MAX_TOKENS, ExtractionError, extract_from_text

RESULT = {"deceased": {"surname": "Neese"}, "relationships": {}}


def _response(text: str, stop_reason: str = "end_turn"):
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=[SimpleNamespace(type="text", text=text)],
    )


class FakeAnthropic:
    responses = []
    calls = []

    def __init__(self, api_key=None):
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        FakeAnthropic.calls.append(kwargs)
        return FakeAnthropic.responses.pop(0)


@pytest.fixture
def fake_client(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(extract, "Anthropic", FakeAnthropic)
    FakeAnthropic.responses = []
    FakeAnthropic.calls = []
    return FakeAnthropic


class TestExtractStopReasonGate:
    def test_end_turn_passes(self, fake_client):
        fake_client.responses = [_response(json.dumps(RESULT))]
        result = extract_from_text("some obituary text")
        assert result["deceased"]["surname"] == "Neese"
        assert fake_client.calls[0]["max_tokens"] == MAX_TOKENS

    def test_truncation_retries_once_then_succeeds(self, fake_client):
        fake_client.responses = [
            _response('{"deceased": {"surn', stop_reason="max_tokens"),
            _response(json.dumps(RESULT)),
        ]
        result = extract_from_text("some obituary text")
        assert result["deceased"]["surname"] == "Neese"
        assert [c["max_tokens"] for c in fake_client.calls] == [MAX_TOKENS, RETRY_MAX_TOKENS]

    def test_double_truncation_fails_loud(self, fake_client):
        fake_client.responses = [
            _response('{"partial', stop_reason="max_tokens"),
            _response('{"partial', stop_reason="max_tokens"),
        ]
        with pytest.raises(ExtractionError, match="still truncated"):
            extract_from_text("some obituary text")

    def test_unexpected_stop_reason_fails(self, fake_client):
        fake_client.responses = [_response("nope", stop_reason="refusal")]
        with pytest.raises(ExtractionError, match="refusal"):
            extract_from_text("some obituary text")
        assert len(fake_client.calls) == 1
