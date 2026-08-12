"""tests/test_fs_client.py — src/fs_client.py tests against a fake httpx transport.

No real network calls. Per plan §7: journal write/resume, 429 + Retry-After honored,
401 triggers re-auth path, dry-run blocks non-GETs.
"""

import json

import httpx
import pytest

from src import token_store
from src.fs_client import (
    FSAuthExpiredError,
    FSClientError,
    FSJobLockedError,
    FSUncertainWriteError,
    FamilySearchClient,
    bucket_for_confidence,
    run_upload_sequence,
)


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


class TestInterruptedWrite:
    """A write whose response never came back is the one case we must never retry blind:
    the person may already be in the tree (plan §4.3, network-error row)."""

    def _dropped_write(self, journal_path):
        def handler(request):
            raise httpx.ConnectError("connection dropped")

        client = FamilySearchClient(access_token="tok", dry_run=False, journal_path=journal_path,
                                     transport=httpx.MockTransport(handler))
        with pytest.raises(FSClientError):
            client.send("person:subject", "POST", "https://apibeta.familysearch.org/platform/tree/persons", {"names": []})
        return client

    def test_intent_recorded_before_the_call(self, tmp_path):
        journal_path = tmp_path / "journal.json"
        client = self._dropped_write(journal_path)

        on_disk = json.loads(journal_path.read_text(encoding="utf-8"))
        assert [e["status"] for e in on_disk] == ["in_flight"]

    def test_resume_refuses_to_resend_an_in_flight_write(self, tmp_path):
        journal_path = tmp_path / "journal.json"
        self._dropped_write(journal_path)

        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(201, headers={"Location": ".../persons/REAL-PID"})

        resumed = FamilySearchClient(access_token="tok", dry_run=False, journal_path=journal_path,
                                      transport=httpx.MockTransport(handler))
        with pytest.raises(FSUncertainWriteError):
            resumed.send("person:subject", "POST", "https://apibeta.familysearch.org/platform/tree/persons", {"names": []})
        assert calls == []  # never silently re-created the person

    def test_in_flight_get_is_safely_retried(self, tmp_path):
        """Reads have no side effects, so an interrupted search just runs again."""
        journal_path = tmp_path / "journal.json"

        def dropping(request):
            raise httpx.ConnectError("connection dropped")

        client = FamilySearchClient(access_token="tok", dry_run=False, journal_path=journal_path,
                                     transport=httpx.MockTransport(dropping))
        with pytest.raises(FSClientError):
            client.send("search:subject", "GET", "https://apibeta.familysearch.org/platform/tree/search")

        calls = []

        def ok(request):
            calls.append(request)
            return httpx.Response(200, json={})

        resumed = FamilySearchClient(access_token="tok", dry_run=False, journal_path=journal_path,
                                      transport=httpx.MockTransport(ok))
        resumed.send("search:subject", "GET", "https://apibeta.familysearch.org/platform/tree/search")
        assert len(calls) == 1

    def test_settled_write_leaves_one_entry_not_two(self, tmp_path):
        journal_path = tmp_path / "journal.json"

        def handler(request):
            return httpx.Response(201, headers={"Location": ".../persons/REAL-PID"})

        client = FamilySearchClient(access_token="tok", dry_run=False, journal_path=journal_path,
                                     transport=httpx.MockTransport(handler))
        client.send("person:subject", "POST", "https://apibeta.familysearch.org/platform/tree/persons", {"names": []})

        on_disk = json.loads(journal_path.read_text(encoding="utf-8"))
        assert len(on_disk) == 1
        assert on_disk[0]["status"] == "ok"


class TestJobLocking:
    """Duplicate tab / double-submitted commit button. The journal alone does not stop
    this: both runs load an empty journal and both POST."""

    @pytest.fixture(autouse=True)
    def _isolated_store(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FWL_TOKEN_STORE_PATH", str(tmp_path / "store.sqlite3"))

    def _client(self, tmp_path, calls):
        def handler(request):
            calls.append(request)
            return httpx.Response(201, headers={"Location": ".../persons/REAL-PID"})

        return FamilySearchClient(access_token="tok", dry_run=True,
                                   journal_path=tmp_path / "journal.json",
                                   transport=httpx.MockTransport(handler))

    _PLAN = {
        "persons": {"subject": {"names": []}},
        "relationships": {"couples": [], "child_and_parents": []},
    }

    def test_second_owner_is_refused_while_the_first_holds_the_lock(self, tmp_path):
        token_store.acquire_job_lock("job1", "tab-a")
        client = self._client(tmp_path, [])
        with pytest.raises(FSJobLockedError):
            run_upload_sequence(client, self._PLAN, job_id="job1", owner="tab-b")

    def test_lock_is_released_when_the_sequence_finishes(self, tmp_path):
        client = self._client(tmp_path, [])
        run_upload_sequence(client, self._PLAN, job_id="job1", owner="tab-a")
        assert token_store.job_lock_owner("job1") is None

    def test_lock_is_released_when_the_sequence_raises(self, tmp_path):
        client = self._client(tmp_path, [])
        with pytest.raises(KeyError):
            run_upload_sequence(client, {"persons": {}, "relationships": {
                "couples": [{"person1": "missing", "person2": "missing"}], "child_and_parents": []}},
                job_id="job1", owner="tab-a")
        assert token_store.job_lock_owner("job1") is None

    def test_omitting_the_lock_keeps_the_old_behavior(self, tmp_path):
        client = self._client(tmp_path, [])
        result = run_upload_sequence(client, self._PLAN)
        assert result["persons"]["subject"] == "DRYRUN-P001"


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


class TestBucketForConfidence:
    def test_strong(self):
        assert bucket_for_confidence(5) == "strong"
        assert bucket_for_confidence(4) == "strong"

    def test_possible(self):
        assert bucket_for_confidence(3) == "possible"
        assert bucket_for_confidence(2) == "possible"

    def test_weak(self):
        assert bucket_for_confidence(1) == "weak"
        assert bucket_for_confidence(0) == "weak"

    def test_missing_confidence_is_weak(self):
        assert bucket_for_confidence(None) == "weak"


class TestSearchMatches:
    def test_dry_run_still_executes_live(self, tmp_path):
        """Match search is a read — must never be short-circuited by dry_run,
        unlike send()'s POST/PUT/DELETE block."""
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(200, json={"persons": []})

        client = FamilySearchClient(access_token="tok", dry_run=True, journal_path=tmp_path / "journal.json",
                                     transport=httpx.MockTransport(handler))
        client.search_matches({"names": []})

        assert len(calls) == 1

    def test_parses_candidates_with_confidence_and_bucket(self, tmp_path):
        def handler(request):
            return httpx.Response(200, json={
                "persons": [
                    {
                        "id": "PID-1",
                        "score": 5,
                        "display": {"name": "Donna Sue Neese", "lifespan": "1939-2024"},
                    },
                    {
                        "id": "PID-2",
                        "confidence": 2,
                        "display": {"name": "D. Neese", "lifespan": "1940-2023"},
                    },
                ]
            })

        client = FamilySearchClient(access_token="tok", dry_run=False, journal_path=tmp_path / "journal.json",
                                     transport=httpx.MockTransport(handler))
        candidates = client.search_matches({"names": []})

        assert candidates[0] == {
            "pid": "PID-1", "name": "Donna Sue Neese", "lifespan": "1939-2024",
            "confidence": 5, "bucket": "strong",
        }
        assert candidates[1]["bucket"] == "possible"

    def test_no_matches_returns_empty_list(self, tmp_path):
        def handler(request):
            return httpx.Response(200, json={"persons": []})

        client = FamilySearchClient(access_token="tok", dry_run=False, journal_path=tmp_path / "journal.json",
                                     transport=httpx.MockTransport(handler))
        assert client.search_matches({"names": []}) == []

    def test_unexpected_shape_degrades_to_empty_list(self, tmp_path):
        def handler(request):
            return httpx.Response(200, json={"something_else": True})

        client = FamilySearchClient(access_token="tok", dry_run=False, journal_path=tmp_path / "journal.json",
                                     transport=httpx.MockTransport(handler))
        assert client.search_matches({"names": []}) == []

    def test_401_raises_fs_auth_expired(self, tmp_path):
        def handler(request):
            return httpx.Response(401)

        client = FamilySearchClient(access_token="tok", dry_run=False, journal_path=tmp_path / "journal.json",
                                     transport=httpx.MockTransport(handler))
        with pytest.raises(FSAuthExpiredError):
            client.search_matches({"names": []})

    def test_4xx_raises_fs_client_error(self, tmp_path):
        def handler(request):
            return httpx.Response(400, text="bad request")

        client = FamilySearchClient(access_token="tok", dry_run=False, journal_path=tmp_path / "journal.json",
                                     transport=httpx.MockTransport(handler))
        with pytest.raises(FSClientError):
            client.search_matches({"names": []})

    def test_4xx_error_message_includes_real_body_text(self, tmp_path):
        """The raised exception must carry FamilySearch's actual body, not just the status
        code — the original gap FWL-012-H4 hit: the app's error only ever showed "HTTP 400"
        while the real cause sat in a body only logger.error() saw, never the exception."""
        def handler(request):
            return httpx.Response(400, text='{"errors":[{"message":"The gedcomx must contain a descriptionRef."}]}')

        client = FamilySearchClient(access_token="tok", dry_run=False, journal_path=tmp_path / "journal.json",
                                     transport=httpx.MockTransport(handler))
        with pytest.raises(FSClientError, match="descriptionRef"):
            client.search_matches({"names": []})

    def test_2xx_with_unparseable_body_raises_fs_client_error_not_crash(self, tmp_path):
        """FamilySearch can return a 2xx whose body isn't valid JSON (confirmed live
        2026-08-12, FWL-012-H7: json.decoder.JSONDecodeError reached Flask's debugger raw
        instead of the app's normal error handling). A response that passes the status
        checks must never be assumed to parse cleanly."""
        def handler(request):
            return httpx.Response(200, text="")

        client = FamilySearchClient(access_token="tok", dry_run=False, journal_path=tmp_path / "journal.json",
                                     transport=httpx.MockTransport(handler))
        with pytest.raises(FSClientError, match="HTTP 200"):
            client.search_matches({"names": []})

    def test_not_journaled(self, tmp_path):
        """Match search isn't a write — it must not appear in the resumable journal."""
        def handler(request):
            return httpx.Response(200, json={"persons": []})

        client = FamilySearchClient(access_token="tok", dry_run=False, journal_path=tmp_path / "journal.json",
                                     transport=httpx.MockTransport(handler))
        client.search_matches({"names": []})

    def test_request_body_has_full_description_ref_chain(self, tmp_path):
        """FamilySearch rejects a Person Matches by Example body missing any link in the
        description-ref chain: primary person `id` -> sourceDescriptions[].about -> that
        person, AND a top-level `description` -> that sourceDescriptions[].id. Confirmed
        live 2026-08-12 (FWL-012-H4, then FWL-012-H6 after a first fix attempt in
        FWL-012-H5 got the about-link right but omitted the top-level `description`,
        still failing with "The gedcomx must contain a descriptionRef." — same error,
        because that field specifically is the "descriptionRef"). Also confirms the
        original person dict passed in isn't mutated (these ids are call-scoped, not
        smuggled back into the plan's shared person body used elsewhere, e.g. person
        creation)."""
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(200, json={"persons": []})

        client = FamilySearchClient(access_token="tok", dry_run=False, journal_path=tmp_path / "journal.json",
                                     transport=httpx.MockTransport(handler))
        person = {"names": [], "living": False}
        client.search_matches(person)

        assert person == {"names": [], "living": False}, "search_matches() must not mutate the caller's person dict"

        sent = json.loads(calls[0].content)
        primary = sent["persons"][0]
        assert primary["id"], "primary person must carry a non-empty id"

        source_descriptions = sent["sourceDescriptions"]
        assert len(source_descriptions) == 1
        sd = source_descriptions[0]
        assert sd["id"], "sourceDescriptions entry must carry a non-empty id"
        assert sd["about"] == f"#{primary['id']}", "sourceDescriptions.about must reference the primary person"

        assert sent["description"] == f"#{sd['id']}", "top-level description must reference the sourceDescriptions id"

        assert client.journal == []
