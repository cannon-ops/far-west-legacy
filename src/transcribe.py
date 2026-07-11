"""
transcribe.py — Claude vision calls for the scanned-obituary pipeline (M3.1).

Two calls live here:
  - segment_probe(): how many distinct obituaries are on this page image
  - transcribe_page(): image → verbatim transcript JSON with [illegible:n]
    markers, layout notes, portrait detection, and header context

Model: Haiku 4.5 per the M3.0 eval (docs/m3-0-eval-note.md §1) — parity with
Sonnet 5 at ≥1092px on the fixture set; ingest.py normalizes input to 1568px
long edge. Sonnet 5 is the ratified low-confidence retry tier; the tier
logic itself is M3.4 scope and is not wired here.

Every call is gated on stop_reason: a max_tokens truncation retries once at
a higher cap, then fails loud. Truncated output is never returned.
"""

import base64
import json
import os
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv

from src.extract import _strip_markdown_fences

load_dotenv()

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"
TRANSCRIBE_PROMPT_PATH = PROMPTS_DIR / "obituary_transcribe.md"

TRANSCRIBE_MODEL = "claude-haiku-4-5"
MAX_TOKENS = 4096
RETRY_MAX_TOKENS = 8192

# Same probe prompt the M3.0 segment eval measured (scripts/m3_eval.py);
# the probe also ran with the transcription prompt as system, so we keep
# that configuration rather than re-tuning an unmeasured one.
SEGMENT_PROMPT = (
    "Identify each distinct obituary on this newspaper page image.\n"
    'Return ONLY valid JSON (no fences): {"count": N, "obituaries": '
    '[{"name": "deceased\'s full name as printed", "bbox": '
    '{"left": 0.0, "top": 0.0, "right": 0.0, "bottom": 0.0}}]}\n'
    "bbox values are fractions of image width/height. Include ONLY obituaries "
    "— ignore mastheads, page headers, and advertisements."
)


class TranscriptionError(Exception):
    """Raised when a vision call fails, is truncated, or returns bad JSON."""


def _client() -> Anthropic:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise TranscriptionError("ANTHROPIC_API_KEY is not set.")
    return Anthropic(api_key=api_key)


def _gated_create(client: Anthropic, *, system: str, content: list) -> str:
    """
    One vision call, gated on stop_reason. max_tokens truncation retries once
    at RETRY_MAX_TOKENS; a second truncation or any other unexpected
    stop_reason raises. Returns the response text.
    """
    for cap in (MAX_TOKENS, RETRY_MAX_TOKENS):
        try:
            response = client.messages.create(
                model=TRANSCRIBE_MODEL,
                max_tokens=cap,
                system=system,
                messages=[{"role": "user", "content": content}],
            )
        except Exception as exc:
            raise TranscriptionError(f"Anthropic API call failed: {exc}") from exc
        if response.stop_reason == "max_tokens":
            continue  # retry once at the higher cap
        if response.stop_reason != "end_turn":
            raise TranscriptionError(
                f"Unexpected stop_reason {response.stop_reason!r} — "
                "refusing to use this response."
            )
        return "".join(b.text for b in response.content if b.type == "text")
    raise TranscriptionError(
        f"Model output still truncated at max_tokens={RETRY_MAX_TOKENS} — "
        "refusing to return a partial transcript."
    )


def _image_block(image_path: Path) -> dict:
    data = base64.standard_b64encode(image_path.read_bytes()).decode()
    media = "image/jpeg" if image_path.suffix.lower() in (".jpg", ".jpeg") else "image/png"
    return {"type": "image", "source": {"type": "base64", "media_type": media, "data": data}}


def _parse_json(raw: str, context: str) -> dict:
    try:
        return json.loads(_strip_markdown_fences(raw))
    except json.JSONDecodeError as exc:
        raise TranscriptionError(
            f"{context} returned non-JSON output. JSONDecodeError: {exc}\n"
            f"Raw output: {raw[:500]}"
        ) from exc


def transcribe_page(image_path: Path, target_name: str | None = None) -> dict:
    """
    Transcribe one page image to verbatim text.

    Returns the transcript payload:
        {text, illegible_spans, layout_notes, portrait, header_context}

    Raises:
        TranscriptionError: On API failure, truncation, or unparseable output.
    """
    system = TRANSCRIBE_PROMPT_PATH.read_text(encoding="utf-8")
    ask = (
        f"Transcribe only the obituary of {target_name}; ignore all other text."
        if target_name
        else "Transcribe this obituary."
    )
    raw = _gated_create(
        _client(),
        system=system,
        content=[_image_block(image_path), {"type": "text", "text": ask}],
    )
    payload = _parse_json(raw, "Transcription")
    if not isinstance(payload.get("text"), str) or not payload["text"].strip():
        raise TranscriptionError(
            f"Transcription returned no 'text' field. Raw output: {raw[:500]}"
        )
    return payload


def segment_probe(image_path: Path) -> dict:
    """
    Ask how many distinct obituaries are on this page image.

    Returns {"count": N, "obituaries": [{"name": ..., "bbox": {...}}]}.
    M3.1 uses only the count and names; region crops arrive in M3.3.

    Raises:
        TranscriptionError: On API failure, truncation, or unparseable output.
    """
    system = TRANSCRIBE_PROMPT_PATH.read_text(encoding="utf-8")
    raw = _gated_create(
        _client(),
        system=system,
        content=[_image_block(image_path), {"type": "text", "text": SEGMENT_PROMPT}],
    )
    payload = _parse_json(raw, "Segmentation probe")
    if not isinstance(payload.get("obituaries"), list):
        raise TranscriptionError(
            f"Segmentation probe returned no 'obituaries' list. Raw output: {raw[:500]}"
        )
    return payload
