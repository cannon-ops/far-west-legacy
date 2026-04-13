"""
tests/test_extract.py — Tests for the obituary extraction pipeline.
"""

import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

# Load .env before checking for API key — dotenv in extract.py runs at import,
# but the skip-marker is evaluated at collection time so we must load here too.
load_dotenv()

from src.extract import ExtractionError, _strip_markdown_fences, extract_from_text

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SAMPLE_OBIT = FIXTURES_DIR / "sample_obituary_01.txt"

API_KEY_PRESENT = bool(os.getenv("ANTHROPIC_API_KEY"))


# ---------------------------------------------------------------------------
# Unit test — no API call
# ---------------------------------------------------------------------------


class TestStripMarkdownFences:
    def test_strips_json_fence(self):
        raw = "```json\n{\"key\": \"value\"}\n```"
        assert _strip_markdown_fences(raw) == '{"key": "value"}'

    def test_strips_plain_fence(self):
        raw = "```\n{\"key\": \"value\"}\n```"
        assert _strip_markdown_fences(raw) == '{"key": "value"}'

    def test_passthrough_clean_json(self):
        raw = '{"key": "value"}'
        assert _strip_markdown_fences(raw) == '{"key": "value"}'

    def test_strips_leading_trailing_whitespace(self):
        raw = "  \n```json\n{\"key\": \"value\"}\n```\n  "
        assert _strip_markdown_fences(raw) == '{"key": "value"}'

    def test_fence_with_no_newline_after_opening(self):
        raw = "```json{\"key\": \"value\"}```"
        assert _strip_markdown_fences(raw) == '{"key": "value"}'


# ---------------------------------------------------------------------------
# Integration test — real API call
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not API_KEY_PRESENT,
    reason="ANTHROPIC_API_KEY not set — skipping live API test",
)
class TestExtractFromText:
    @pytest.fixture(scope="class")
    def result(self):
        obituary_text = SAMPLE_OBIT.read_text(encoding="utf-8")
        return extract_from_text(obituary_text, source_url="")

    def test_returns_dict(self, result):
        assert isinstance(result, dict)

    def test_has_required_top_level_keys(self, result):
        assert "deceased" in result
        assert "relationships" in result
        assert "eulogy_text" in result
        assert "service_details" in result
        assert "source_url" in result
        assert "raw_text" in result

    def test_deceased_has_required_fields(self, result):
        deceased = result["deceased"]
        for field in ("given_names", "surname", "maiden_name", "suffix", "gender",
                      "birth_date", "birth_place", "death_date", "death_place", "burial_place"):
            assert field in deceased, f"Missing deceased field: {field}"

    def test_relationships_has_required_keys(self, result):
        rels = result["relationships"]
        for key in ("spouses", "parents", "children", "siblings"):
            assert key in rels, f"Missing relationships key: {key}"
            assert isinstance(rels[key], list), f"relationships.{key} should be a list"

    def test_given_names(self, result):
        assert result["deceased"]["given_names"] == "Donna Sue"

    def test_surname(self, result):
        assert result["deceased"]["surname"] == "Neese"

    def test_gender(self, result):
        assert result["deceased"]["gender"] == "Female"

    def test_birth_date(self, result):
        assert result["deceased"]["birth_date"] == "1939-07-25"

    def test_death_date(self, result):
        assert result["deceased"]["death_date"] == "2025-12-10"

    def test_birth_place_contains_jamesport(self, result):
        assert "Jamesport" in result["deceased"]["birth_place"]

    def test_parents_count(self, result):
        parents = result["relationships"]["parents"]
        assert len(parents) == 2, f"Expected 2 parents, got {len(parents)}: {parents}"

    def test_parent_given_names(self, result):
        parents = result["relationships"]["parents"]
        given_names = [p["given_names"] for p in parents]
        assert "Andrew" in given_names
        assert any("Nellie" in n for n in given_names)

    def test_mother_maiden_name(self, result):
        parents = result["relationships"]["parents"]
        nellie = next((p for p in parents if "Nellie" in p.get("given_names", "")), None)
        assert nellie is not None, "Nellie (mother) not found in parents"
        assert nellie.get("maiden_name") == "Walker", (
            f"Expected mother maiden_name='Walker', got '{nellie.get('maiden_name')}'"
        )

    def test_siblings_at_least_four(self, result):
        siblings = result["relationships"]["siblings"]
        assert len(siblings) >= 4, f"Expected at least 4 siblings, got {len(siblings)}: {siblings}"

    def test_all_siblings_deceased(self, result):
        siblings = result["relationships"]["siblings"]
        for sib in siblings:
            assert sib.get("deceased") is True, (
                f"Sibling {sib.get('given_names')} {sib.get('surname')} "
                f"should be marked deceased=true (all preceded in death)"
            )

    def test_raw_text_populated(self, result):
        assert len(result.get("raw_text", "")) > 50
