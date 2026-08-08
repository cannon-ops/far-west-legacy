"""tests/test_fs_client.py — src/fs_client.py tests against a fake httpx transport.

No real network calls. Per plan §7: journal write/resume, 429 + Retry-After honored,
401 triggers re-auth path, dry-run blocks non-GETs.
"""

import json

import httpx
import pytest

from src.fs_client import FSAuthExpiredError, FSClientError, FamilySearchClient


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    monkeypatch.setattr("src.fs_client.time.sleep", lambda seconds: None)


class TestDryRunBlocksNonGets:
    def test_post_never_reaches_transport(self, tmp_path):
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(201, headers={"Location": "https://apibeta.familysearch.org/platform/tree/persons/REAL-PID"})

        client = FamilySearchClient(
            access_token="tok", dry_run=True,
            journal_path=tmp_path / "journal.json",
            transport=httpx.MockTransport(handler),
        )
        pid = client.send("person:subject", "POST", "https://apibeta.familysearch.org/platform/tree/persons", {"names": []})

        assert calls == []
        assert pid == "DRYRUN-P001"
        entry = client.journal[0]
        assert entry["status"] == "dry_run"
        assert entry["method"] == "POST"

    def test_get_executes_for_real_even_in_dry_run(self, tmp_path):
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(200, json={"ok": True})

        client = FamilySearchClient(
            access_token="tok", dry_run=True,
            journal_path=tmp_path / "journal.json",
            transport=httpx.MockTransport(handler),
        )
        client.send("search:subject", "GET", "https://apibeta.familysearch.org/platform/tree/search")

        assert len(calls) == 1


class TestJournalResume:
    def test_second_client_skips_completed_step(self, tmp_path):
        journal_path = tmp_path / "journal.json"
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(201, headers={"Location": ".../persons/REAL-PID"})

        client1 = FamilySearchClient(access_token="tok", dry_run=False, journal_path=journal_path,
                                      transport=httpx.MockTransport(handler))
        pid1 = client1.send("person:subject", "POST", "https://apibeta.familysearch.org/platform/tree/persons", {"names": []})
        assert len(calls) == 1

        client2 = FamilySearchClient(access_token="tok", dry_run=False, journal_path=journal_path,
                                      transport=httpx.MockTransport(handler))
        pid2 = client2.send("person:subject", "POST", "https://apibeta.familysearch.org/platform/tree/persons", {"names": []})

        assert len(calls) == 1  # not re-sent
        assert pid1 == pid2 == "REAL-PID"

    def test_journal_persisted_to_disk(self, tmp_path):
        journal_path = tmp_path / "journal.json"

        def handler(request):
            return httpx.Response(201, headers={"Location": ".../persons/REAL-PID"})

        client = FamilySearchClient(access_token="tok", dry_run=False, journal_path=journal_path,
                                     transport=httpx.MockTransport(handler))
        client.send("person:subject", "POST", "https://apibeta.familysearch.org/platform/tree/persons", {"names": []})

        on_disk = json.loads(journal_path.read_text(encoding="utf-8"))
        assert on_disk[0]["step"] == "person:subject"
        assert on_disk[0]["status"] == "ok"


class TestThrottling:
    def test_429_honors_retry_after_then_succeeds(self, tmp_path):
        responses = [
            httpx.Response(429, headers={"Retry-After": "1"}),
            httpx.Response(201, headers={"Location": ".../persons/REAL-PID"}),
        ]

        def handler(request):
            return responses.pop(0)

        client = FamilySearchClient(access_token="tok", dry_run=False, journal_path=tmp_path / "journal.json",
                                     transport=httpx.MockTransport(handler))
        pid = client.send("person:subject", "POST", "https://apibeta.familysearch.org/platform/tree/persons", {"names": []})

        assert pid == "REAL-PID"
        assert client.journal[-1]["status"] == "ok"

    def test_429_exhausts_retries_raises(self, tmp_path):
        def handler(request):
            return httpx.Response(429, headers={"Retry-After": "1"})

        client = FamilySearchClient(access_token="tok", dry_run=False, journal_path=tmp_path / "journal.json",
                                     transport=httpx.MockTransport(handler))
        with pytest.raises(FSClientError):
            client.send("person:subject", "POST", "https://apibeta.familysearch.org/platform/tree/persons", {"names": []})


class TestAuthExpired:
    def test_401_raises_fs_auth_expired(self, tmp_path):
        def handler(request):
            return httpx.Response(401)

        client = FamilySearchClient(access_token="tok", dry_run=False, journal_path=tmp_path / "journal.json",
                                     transport=httpx.MockTransport(handler))
        with pytest.raises(FSAuthExpiredError):
            client.send("person:subject", "POST", "https://apibeta.familysearch.org/platform/tree/persons", {"names": []})

    def test_401_recorded_as_error_in_journal(self, tmp_path):
        def handler(request):
            return httpx.Response(401)

        client = FamilySearchClient(access_token="tok", dry_run=False, journal_path=tmp_path / "journal.json",
                                     transport=httpx.MockTransport(handler))
        with pytest.raises(FSAuthExpiredError):
            client.send("person:subject", "POST", "https://apibeta.familysearch.org/platform/tree/persons", {"names": []})
        assert client.journal[-1]["status"] == "error"


class TestValidationErrorHalts:
    def test_4xx_not_retried(self, tmp_path):
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(422, text="invalid body")

        client = FamilySearchClient(access_token="tok", dry_run=False, journal_path=tmp_path / "journal.json",
                                     transport=httpx.MockTransport(handler))
        with pytest.raises(FSClientError):
            client.send("person:subject", "POST", "https://apibeta.familysearch.org/platform/tree/persons", {"names": []})
        assert len(calls) == 1
