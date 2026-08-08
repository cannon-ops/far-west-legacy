"""tests/test_app_upload.py — GET /upload/<job_id> and POST /upload/<job_id>/decide (M2.2).

FamilySearchClient is monkeypatched to a fake so these stay offline like every
other FS-layer test; no real network calls, no real FamilySearch session.
"""

import json
from pathlib import Path

import pytest

from src import app as app_module

FIXTURES_DIR = Path(__file__).parent / "fixtures"
EXTRACTED_JSON = FIXTURES_DIR / "sample_obituary_01_extracted.json"


class FakeMatchClient:
    """Stands in for FamilySearchClient — every person gets one strong candidate
    unless STRONG_MATCH is set False, in which case no candidates are returned."""

    STRONG_MATCH = True

    def __init__(self, *args, **kwargs):
        pass

    def search_matches(self, person_body, count=5):
        if not FakeMatchClient.STRONG_MATCH:
            return []
        return [{"pid": "PID-EXISTING", "name": "Candidate Match", "lifespan": "1939-2024",
                  "confidence": 5, "bucket": "strong"}]

    def close(self):
        pass


@pytest.fixture(autouse=True)
def _reset_fake_client():
    FakeMatchClient.STRONG_MATCH = True
    yield
    FakeMatchClient.STRONG_MATCH = True


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "TMP_DIR", tmp_path)
    monkeypatch.setattr(app_module, "FWL_FS_UPLOAD_ENABLED", True)
    monkeypatch.setattr(app_module, "FamilySearchClient", FakeMatchClient)
    monkeypatch.setattr(
        app_module.fs_auth, "get_session",
        lambda sid: {"token": {"access_token": "fake-tok"}, "display_name": "Joel Cannon"} if sid == "test-sid" else None,
    )
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()


def _seed_job(tmp_path, job_id="job-1"):
    data = json.loads(EXTRACTED_JSON.read_text(encoding="utf-8"))
    (tmp_path / f"{job_id}.json").write_text(json.dumps(data), encoding="utf-8")
    return data


def _signed_in(client):
    with client.session_transaction() as sess:
        sess["fs_sid"] = "test-sid"


class TestUploadGate:
    def test_404_when_upload_disabled(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr(app_module, "FWL_FS_UPLOAD_ENABLED", False)
        _seed_job(tmp_path)
        resp = client.get("/upload/job-1")
        assert resp.status_code == 404

    def test_missing_job_redirects_to_tool(self, client):
        _signed_in(client)
        resp = client.get("/upload/does-not-exist")
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/tool")

    def test_not_signed_in_redirects_to_auth_login(self, client, tmp_path):
        _seed_job(tmp_path)
        resp = client.get("/upload/job-1")
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/auth/login")


class TestUploadMatchCheck:
    def test_renders_match_panel_for_every_plan_person(self, client, tmp_path):
        _seed_job(tmp_path)
        _signed_in(client)
        resp = client.get("/upload/job-1")

        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        # Neese fixture: subject + 2 parents + 4 siblings, no spouses/children
        for key in ("subject", "parent_0", "parent_1", "sibling_0", "sibling_1", "sibling_2", "sibling_3"):
            assert f'data-person-key="{key}"' in body
        assert "PID-EXISTING" in body
        assert "Commit Decisions" in body

    def test_no_matches_shows_no_candidates_message(self, client, tmp_path):
        FakeMatchClient.STRONG_MATCH = False
        _seed_job(tmp_path)
        _signed_in(client)
        resp = client.get("/upload/job-1")

        assert resp.status_code == 200
        assert "No candidate matches found." in resp.get_data(as_text=True)


class TestUploadDecide:
    def _decide_form(self, data_keys, decision="skip", pid=""):
        form = {}
        for key in data_keys:
            form[f"decision_{key}"] = decision
            if decision == "use_existing":
                form[f"existing_pid_{key}"] = pid
        return form

    def test_all_decided_records_decisions(self, client, tmp_path):
        _seed_job(tmp_path)
        _signed_in(client)
        keys = ["subject", "parent_0", "parent_1", "sibling_0", "sibling_1", "sibling_2", "sibling_3"]
        resp = client.post(f"/upload/job-1/decide", data=self._decide_form(keys, "create_new"))

        assert resp.status_code == 200
        assert "Decisions Recorded" in resp.get_data(as_text=True)
        on_disk = json.loads((tmp_path / "job-1.decisions.json").read_text(encoding="utf-8"))
        assert set(on_disk.keys()) == set(keys)
        assert all(d["decision"] == "create_new" for d in on_disk.values())

    def test_missing_decision_rejected(self, client, tmp_path):
        _seed_job(tmp_path)
        _signed_in(client)
        # Only decide the subject, leave the rest undecided
        resp = client.post("/upload/job-1/decide", data={"decision_subject": "create_new"})

        assert resp.status_code == 200
        assert "Missing or invalid decision" in resp.get_data(as_text=True)
        assert not (tmp_path / "job-1.decisions.json").exists()

    def test_use_existing_without_pid_rejected(self, client, tmp_path):
        _seed_job(tmp_path)
        _signed_in(client)
        keys = ["subject", "parent_0", "parent_1", "sibling_0", "sibling_1", "sibling_2", "sibling_3"]
        form = self._decide_form(keys, "create_new")
        form["decision_subject"] = "use_existing"  # no existing_pid_subject set
        resp = client.post("/upload/job-1/decide", data=form)

        assert resp.status_code == 200
        assert "no candidate PID was selected" in resp.get_data(as_text=True)
        assert not (tmp_path / "job-1.decisions.json").exists()

    def test_use_existing_with_pid_recorded(self, client, tmp_path):
        _seed_job(tmp_path)
        _signed_in(client)
        keys = ["subject", "parent_0", "parent_1", "sibling_0", "sibling_1", "sibling_2", "sibling_3"]
        form = self._decide_form(keys, "create_new")
        form["decision_subject"] = "use_existing"
        form["existing_pid_subject"] = "PID-EXISTING"
        resp = client.post("/upload/job-1/decide", data=form)

        assert resp.status_code == 200
        on_disk = json.loads((tmp_path / "job-1.decisions.json").read_text(encoding="utf-8"))
        assert on_disk["subject"] == {"decision": "use_existing", "existing_pid": "PID-EXISTING"}
