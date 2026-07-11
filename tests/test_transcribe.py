"""
tests/test_transcribe.py — Vision transcription + segmentation probe (M3.1).

Unit tests run against a fake Anthropic client (no network). One integration
test is gated behind RUN_NETWORK_TESTS=1 so plain `pytest` never spends
vision tokens.
"""

import io
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
import src.transcribe as transcribe
from PIL import Image
from src.transcribe import (
    MAX_TOKENS,
    RETRY_MAX_TOKENS,
    TranscriptionError,
    segment_probe,
    transcribe_page,
)

SCANS = Path(__file__).parent / "fixtures" / "scans"

TRANSCRIPT_PAYLOAD = {
    "text": "NEESE — Donna Sue Neese, age 86, of Pleasant Valley...",
    "illegible_spans": [],
    "layout_notes": "single column clipping",
    "portrait": {"present": False, "region": None, "caption": None},
    "header_context": {"newspaper": None, "page_date": None, "page_number": None},
}


def _response(text: str, stop_reason: str = "end_turn"):
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=[SimpleNamespace(type="text", text=text)],
    )


class FakeClient:
    """Scripted messages.create: pops responses in order, records kwargs."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return self._responses.pop(0)


@pytest.fixture
def image_path(tmp_path):
    path = tmp_path / "page_1.png"
    buf = io.BytesIO()
    Image.new("RGB", (100, 100), "white").save(buf, "PNG")
    path.write_bytes(buf.getvalue())
    return path


def _install(monkeypatch, client):
    monkeypatch.setattr(transcribe, "_client", lambda: client)
    return client


class TestTranscribePage:
    def test_happy_path(self, monkeypatch, image_path):
        client = _install(monkeypatch, FakeClient([_response(json.dumps(TRANSCRIPT_PAYLOAD))]))
        payload = transcribe_page(image_path)
        assert payload["text"].startswith("NEESE")
        assert len(client.calls) == 1
        assert client.calls[0]["max_tokens"] == MAX_TOKENS

    def test_fenced_json_tolerated(self, monkeypatch, image_path):
        raw = "```json\n" + json.dumps(TRANSCRIPT_PAYLOAD) + "\n```"
        _install(monkeypatch, FakeClient([_response(raw)]))
        assert transcribe_page(image_path)["text"].startswith("NEESE")

    def test_target_name_in_prompt(self, monkeypatch, image_path):
        client = _install(monkeypatch, FakeClient([_response(json.dumps(TRANSCRIPT_PAYLOAD))]))
        transcribe_page(image_path, target_name="Donna Sue Neese")
        text_block = client.calls[0]["messages"][0]["content"][1]
        assert "Donna Sue Neese" in text_block["text"]

    def test_truncation_retries_once_at_higher_cap(self, monkeypatch, image_path):
        client = _install(monkeypatch, FakeClient([
            _response("{\"text\": \"partial", stop_reason="max_tokens"),
            _response(json.dumps(TRANSCRIPT_PAYLOAD)),
        ]))
        payload = transcribe_page(image_path)
        assert payload["text"].startswith("NEESE")
        assert [c["max_tokens"] for c in client.calls] == [MAX_TOKENS, RETRY_MAX_TOKENS]

    def test_double_truncation_fails_loud(self, monkeypatch, image_path):
        _install(monkeypatch, FakeClient([
            _response("{\"text\": \"partial", stop_reason="max_tokens"),
            _response("{\"text\": \"partial", stop_reason="max_tokens"),
        ]))
        with pytest.raises(TranscriptionError, match="still truncated"):
            transcribe_page(image_path)

    def test_unexpected_stop_reason_fails_without_retry(self, monkeypatch, image_path):
        client = _install(monkeypatch, FakeClient([
            _response("I can't help with that.", stop_reason="refusal"),
        ]))
        with pytest.raises(TranscriptionError, match="refusal"):
            transcribe_page(image_path)
        assert len(client.calls) == 1

    def test_non_json_output_fails(self, monkeypatch, image_path):
        _install(monkeypatch, FakeClient([_response("Here is the transcript: ...")]))
        with pytest.raises(TranscriptionError, match="non-JSON"):
            transcribe_page(image_path)

    def test_missing_text_field_fails(self, monkeypatch, image_path):
        _install(monkeypatch, FakeClient([_response('{"illegible_spans": []}')]))
        with pytest.raises(TranscriptionError, match="no 'text' field"):
            transcribe_page(image_path)

    def test_api_error_wrapped(self, monkeypatch, image_path):
        def boom(**kwargs):
            raise RuntimeError("connection reset")

        client = FakeClient([])
        client.messages = SimpleNamespace(create=boom)
        _install(monkeypatch, client)
        with pytest.raises(TranscriptionError, match="API call failed"):
            transcribe_page(image_path)


class TestSegmentProbe:
    def test_happy_path(self, monkeypatch, image_path):
        raw = json.dumps({"count": 1, "obituaries": [
            {"name": "Donna Sue Neese",
             "bbox": {"left": 0.1, "top": 0.1, "right": 0.9, "bottom": 0.9}},
        ]})
        _install(monkeypatch, FakeClient([_response(raw)]))
        probe = segment_probe(image_path)
        assert probe["obituaries"][0]["name"] == "Donna Sue Neese"

    def test_missing_obituaries_list_fails(self, monkeypatch, image_path):
        _install(monkeypatch, FakeClient([_response('{"count": 1}')]))
        with pytest.raises(TranscriptionError, match="no 'obituaries' list"):
            segment_probe(image_path)


@pytest.mark.skipif(
    os.getenv("RUN_NETWORK_TESTS") != "1",
    reason="RUN_NETWORK_TESTS=1 not set — skipping live vision API test",
)
@pytest.mark.skipif(
    not (SCANS / "neese_clean.png").exists(),
    reason="scan fixtures not generated — run `make fixtures`",
)
class TestTranscribeLive:
    def test_neese_clean_fixture(self):
        payload = transcribe_page(SCANS / "neese_clean.png")
        assert "Neese" in payload["text"]
        assert "Donna" in payload["text"]
