"""Unit tests for the M3.0 eval tooling: pure metrics and fixture-generator
determinism. No network, no API keys."""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

from eval_metrics import cer, iou, levenshtein, normalize, wer  # noqa: E402


class TestNormalize:
    def test_case_and_whitespace(self):
        assert normalize("Donna  Sue\nNEESE ") == "donna sue neese"

    def test_empty(self):
        assert normalize("") == ""


class TestLevenshtein:
    def test_identical(self):
        assert levenshtein("kitten", "kitten") == 0

    def test_classic(self):
        assert levenshtein("kitten", "sitting") == 3

    def test_empty_vs_full(self):
        assert levenshtein("", "abc") == 3
        assert levenshtein("abc", "") == 3

    def test_word_sequences(self):
        assert levenshtein(["a", "b", "c"], ["a", "x", "c"]) == 1


class TestErrorRates:
    def test_perfect(self):
        assert cer("Donna Sue Neese", "donna  sue neese") == 0.0
        assert wer("Donna Sue Neese", "donna  sue neese") == 0.0

    def test_cer_scale(self):
        # 1 substitution over 10 reference chars
        assert cer("donna neesX", "donna neese") == 1 / 11

    def test_wer_scale(self):
        assert wer("donna sue nease", "donna sue neese") == 1 / 3

    def test_empty_hypothesis(self):
        assert cer("", "abc") == 1.0
        assert wer("", "one two") == 1.0


class TestIoU:
    def test_identical(self):
        assert iou([0, 0, 1, 1], [0, 0, 1, 1]) == 1.0

    def test_disjoint(self):
        assert iou([0, 0, 0.4, 0.4], [0.5, 0.5, 1, 1]) == 0.0

    def test_half_overlap(self):
        # [0,0,1,1] vs [0.5,0,1.5,1]: inter 0.5, union 1.5
        assert abs(iou([0, 0, 1, 1], [0.5, 0, 1.5, 1]) - 1 / 3) < 1e-9


class TestFixtureGenerator:
    def test_deterministic_and_complete(self, tmp_path, monkeypatch):
        import gen_fixtures

        monkeypatch.setattr(gen_fixtures, "OUT", tmp_path / "scans")
        gen_fixtures.main()
        first = {p.name: p.read_bytes() for p in (tmp_path / "scans").iterdir()}

        # Regenerate into a second directory — byte-identical output
        monkeypatch.setattr(gen_fixtures, "OUT", tmp_path / "scans2")
        gen_fixtures.main()
        second = {p.name: p.read_bytes() for p in (tmp_path / "scans2").iterdir()}
        assert first.keys() == second.keys()
        assert all(first[k] == second[k] for k in first), "generator is not deterministic"

        # Every image has ground truth; the page has bbox ground truth
        names = first.keys()
        assert {"neese_clean.png", "neese_degraded.jpg", "neese_phone.png",
                "veteran_clean.png", "amish_clean.png", "page_3obits.png",
                "page_3obits.json", "manifest.json"} <= set(names)
        for img in ("neese_clean", "neese_degraded", "neese_phone", "veteran_clean",
                    "amish_clean"):
            assert f"{img}.gt.txt" in names

    def test_headline_derivation(self):
        import gen_fixtures

        assert gen_fixtures.headline_for(
            "Ada Mae (Yoder) Miller, age 74 years, of Jamesport"
        ) == "ADA MAE (YODER) MILLER"
