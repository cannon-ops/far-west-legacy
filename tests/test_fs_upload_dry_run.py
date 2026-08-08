"""tests/test_fs_upload_dry_run.py — End-to-end dry-run golden test (plan §7).

Uses the hand-authored extraction fixture for the Neese sample obituary (matches the
extraction asserted in tests/test_extract.py, so it stays consistent with what the
live extractor actually returns) through fs_map -> fs_client, with no network calls
(dry_run=True). This is the M2.1 acceptance artifact: a correct intended-writes
journal, offline and reproducible.
"""

import json
from pathlib import Path

from src.fs_client import FamilySearchClient, run_upload_sequence
from src.fs_map import map_extraction_to_plan

FIXTURES_DIR = Path(__file__).parent / "fixtures"
EXTRACTED_JSON = FIXTURES_DIR / "sample_obituary_01_extracted.json"


def _load_neese():
    return json.loads(EXTRACTED_JSON.read_text(encoding="utf-8"))


def test_neese_dry_run_produces_expected_write_plan():
    data = _load_neese()
    plan = map_extraction_to_plan(data)

    # subject + 2 parents + 4 siblings (all preceded in death, no spouses/children in this obit)
    assert set(plan["persons"].keys()) == {
        "subject", "parent_0", "parent_1",
        "sibling_0", "sibling_1", "sibling_2", "sibling_3",
    }
    assert plan["relationships"]["couples"] == []
    # one CPR linking subject to parents, one CPR per sibling to the same parents
    assert len(plan["relationships"]["child_and_parents"]) == 5
    subject_cpr = next(c for c in plan["relationships"]["child_and_parents"] if c["child"] == "subject")
    assert subject_cpr == {"child": "subject", "parent1": "parent_0", "parent2": "parent_1"}
    for sib_key in ("sibling_0", "sibling_1", "sibling_2", "sibling_3"):
        cpr = next(c for c in plan["relationships"]["child_and_parents"] if c["child"] == sib_key)
        assert cpr["parent1"] == "parent_0"
        assert cpr["parent2"] == "parent_1"

    assert "about" not in plan["source"]  # no source_url in this fixture (pasted text)
    assert "Donna Sue Neese" in plan["source"]["titles"][0]["value"]
    assert plan["skipped"] == []


def test_neese_dry_run_journal_is_correct(tmp_path):
    data = _load_neese()
    plan = map_extraction_to_plan(data)

    journal_path = tmp_path / "neese.upload.json"
    client = FamilySearchClient(access_token="dry-run-token", dry_run=True, journal_path=journal_path)
    summary = run_upload_sequence(client, plan)

    # 7 person creates + 5 relationship creates + 1 source create + 7 source attaches
    assert len(client.journal) == 7 + 5 + 1 + 7
    assert all(entry["status"] == "dry_run" for entry in client.journal)
    assert all(entry["method"] == "POST" for entry in client.journal)

    steps = [entry["step"] for entry in client.journal]
    for key in plan["persons"]:
        assert f"person:{key}" in steps
        assert f"attach:{key}" in steps
    for i in range(len(plan["relationships"]["child_and_parents"])):
        assert f"cpr:{i}" in steps
    assert "source" in steps

    assert summary["source"] == "DRYRUN-P013"  # 7 persons + 5 relationships created before the source
    assert set(summary["persons"].keys()) == set(plan["persons"].keys())
    assert all(pid.startswith("DRYRUN-P") for pid in summary["persons"].values())

    # Journal on disk matches in-memory (resume source of truth)
    on_disk = json.loads(journal_path.read_text(encoding="utf-8"))
    assert on_disk == client.journal


def test_neese_dry_run_resumes_without_duplicate_creates(tmp_path):
    data = _load_neese()
    plan = map_extraction_to_plan(data)
    journal_path = tmp_path / "neese.upload.json"

    client1 = FamilySearchClient(access_token="tok", dry_run=True, journal_path=journal_path)
    run_upload_sequence(client1, plan)
    first_len = len(client1.journal)

    # Simulate a second pass (e.g. after a mid-sequence crash) against the same journal
    client2 = FamilySearchClient(access_token="tok", dry_run=True, journal_path=journal_path)
    summary2 = run_upload_sequence(client2, plan)

    assert len(client2.journal) == first_len  # nothing re-created
    assert summary2["persons"]["subject"] == "DRYRUN-P001"
