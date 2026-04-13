"""
extract.py — Obituary extraction pipeline.

Takes raw obituary text and returns a structured JSON dict using Claude Haiku.
"""

import json
import os
import re
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"
SYSTEM_PROMPT_PATH = PROMPTS_DIR / "obituary_extract.md"

MODEL = "claude-haiku-4-5-20251001"


class ExtractionError(Exception):
    """Raised when extraction fails due to API errors or unparseable output."""


def _strip_markdown_fences(text: str) -> str:
    """Remove markdown code fences if the model wraps its JSON response in them."""
    text = text.strip()
    # Remove opening fence (```json or ```)
    text = re.sub(r"^```(?:json)?\s*\n?", "", text)
    # Remove closing fence
    text = re.sub(r"\n?```\s*$", "", text)
    return text.strip()


def _load_system_prompt() -> str:
    return SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")


def extract_from_text(obituary_text: str, source_url: str = "") -> dict:
    """
    Extract genealogical data from obituary text.

    Args:
        obituary_text: Raw obituary text.
        source_url: Optional URL where the obituary was found.

    Returns:
        Structured dict matching the obituary JSON schema.

    Raises:
        ExtractionError: On API failure or unparseable JSON response.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ExtractionError("ANTHROPIC_API_KEY is not set.")

    system_prompt = _load_system_prompt()

    user_message = obituary_text
    if source_url:
        user_message = f"source_url: {source_url}\n\n{obituary_text}"

    client = Anthropic(api_key=api_key)

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=2048,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
    except Exception as exc:
        raise ExtractionError(f"Anthropic API call failed: {exc}") from exc

    raw_output = response.content[0].text
    cleaned = _strip_markdown_fences(raw_output)

    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ExtractionError(
            f"Model returned non-JSON output. JSONDecodeError: {exc}\n"
            f"Raw output: {raw_output[:500]}"
        ) from exc

    return result
