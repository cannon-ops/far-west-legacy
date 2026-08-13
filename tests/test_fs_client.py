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
    """Thresholds are against FamilySearch's real `score` field — a 0.0-1.0 float,
    confirmed live 2026-08-12 (FWL-012-H9) against a real exact-match response
    (0.9998555), not the 1-5 integer scale originally guessed before any real match
    had been seen."""

    def test_strong(self):
        assert bucket_for_confidence(0.9998555) == "strong"  # the real confirmed value
        assert bucket_for_confidence(0.90) == "strong"

    def test_possible(self):
        assert bucket_for_confidence(0.75) == "possible"
        assert bucket_for_confidence(0.50) == "possible"

    def test_weak(self):
        assert bucket_for_confidence(0.25) == "weak"
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

    def test_parses_candidates_against_real_captured_payload_shape(self, tmp_path):
        """Real Person Matches by Example response, captured live 2026-08-12
        (FWL-012-H9) against Aletha Gibbons Turley's actual approved record
        (trimmed to the fields that matter for parsing; real values otherwise) —
        not a guess. Each entry carries FOUR persons (the match plus her father,
        mother, and husband, included by FamilySearch for context), only one
        flagged `"principal": true`; the real confidence signal is `score`
        (0.9998555, a near-perfect match), not the `confidence` field (5) the
        original code assumed was a 1-5 quality scale; and `display` has no
        `lifespan` key at all, only `birthDate`/`deathDate` strings."""
        def handler(request):
            return httpx.Response(200, json={
                "entries": [
                    {
                        "id": "KWHX-41Y",
                        "score": 0.9998555,
                        "confidence": 5,
                        "content": {
                            "gedcomx": {
                                "persons": [
                                    {
                                        "id": "KWHX-41Y",
                                        "principal": True,
                                        "living": False,
                                        "display": {
                                            "name": "Aletha Gibbons",
                                            "birthDate": "8 February 1935",
                                            "birthPlace": "Eagar, Apache, Arizona, United States",
                                            "deathDate": "13 December 2023",
                                            "deathPlace": "Jameson, Grand River Township, Daviess, Missouri, United States",
                                            "gender": "Female",
                                        },
                                        "names": [{"nameForms": [{"fullText": "Aletha Gibbons"}]}],
                                    },
                                    {"id": "KWZM-MLV", "living": False, "display": {"name": "Austin Whitney Gibbons"}},
                                    {"id": "KWZM-MLR", "living": False, "display": {"name": "Mary B Burk"}},
                                    {"id": "KWHX-41R", "living": False, "display": {"name": "Arthur Austin Turley"}},
                                ]
                            }
                        },
                    },
                ]
            })

        client = FamilySearchClient(access_token="tok", dry_run=False, journal_path=tmp_path / "journal.json",
                                     transport=httpx.MockTransport(handler))
        candidates = client.search_matches({"names": []})

        assert len(candidates) == 1, "the three non-principal context persons must not become extra candidates"
        assert candidates[0] == {
            "pid": "KWHX-41Y",
            "name": "Aletha Gibbons",
            "lifespan": "1935–2023",
            "confidence": 0.9998555,
            "bucket": "strong",
        }

    def test_non_principal_person_never_selected_over_principal(self, tmp_path):
        """Order in the persons array must not matter — only the principal flag does."""
        def handler(request):
            return httpx.Response(200, json={
                "entries": [{
                    "score": 0.5,
                    "content": {"gedcomx": {"persons": [
                        {"id": "SPOUSE-1", "display": {"name": "Not The Match"}},
                        {"id": "MATCH-1", "principal": True, "display": {"name": "The Real Match"}},
                    ]}},
                }]
            })

        client = FamilySearchClient(access_token="tok", dry_run=False, journal_path=tmp_path / "journal.json",
                                     transport=httpx.MockTransport(handler))
        candidates = client.search_matches({"names": []})
        assert candidates[0]["pid"] == "MATCH-1"
        assert candidates[0]["name"] == "The Real Match"

    def test_no_matches_returns_empty_list(self, tmp_path):
        def handler(request):
            return httpx.Response(200, json={"persons": []})

        client = FamilySearchClient(access_token="tok", dry_run=False, journal_path=tmp_path / "journal.json",
                                     transport=httpx.MockTransport(handler))
        assert client.search_matches({"names": []}) == []

    def test_204_no_content_returns_empty_list(self, tmp_path):
        """FamilySearch returns 204 (no body at all), not 200 with an empty envelope, when
        a search finds zero candidates — confirmed live 2026-08-12 (FWL-012-H8). This is a
        legitimate no-matches outcome, not a parse failure the caller should ever see as
        an error."""
        def handler(request):
            return httpx.Response(204)

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


class TestWriteSequenceOrder:
    """FWL-013-H1: plan §4.2 reorder — spouse/parent persons are created before the
    subject, and the subject's own couple/parent-CPR relationships are POSTed
    immediately after its creation instead of in a separate later pass. Children/
    siblings still come after the subject, since their relationships need its PID."""

    _PLAN = {
        "persons": {
            "subject": {"names": []},
            "spouse_0": {"names": []},
            "parent_0": {"names": []},
            "parent_1": {"names": []},
            "child_0": {"names": []},
        },
        "relationships": {
            "couples": [{"person1": "subject", "person2": "spouse_0"}],
            "child_and_parents": [
                {"child": "subject", "parent1": "parent_0", "parent2": "parent_1"},
                {"child": "child_0", "parent1": "subject", "parent2": None},
            ],
        },
    }

    def _client(self, tmp_path):
        counter = {"n": 0}

        def handler(request):
            counter["n"] += 1
            return httpx.Response(201, headers={"Location": f"https://apibeta.familysearch.org/x/PID-{counter['n']}"})

        return FamilySearchClient(access_token="tok", dry_run=False, journal_path=tmp_path / "journal.json",
                                   transport=httpx.MockTransport(handler))

    def test_spouse_and_parents_created_before_subject(self, tmp_path):
        client = self._client(tmp_path)
        run_upload_sequence(client, self._PLAN)

        person_steps = [e["step"] for e in client.journal if e["step"].startswith("person:")]
        assert person_steps.index("person:spouse_0") < person_steps.index("person:subject")
        assert person_steps.index("person:parent_0") < person_steps.index("person:subject")
        assert person_steps.index("person:parent_1") < person_steps.index("person:subject")
        assert person_steps.index("person:child_0") > person_steps.index("person:subject")

    def test_subjects_relationships_immediately_follow_its_creation(self, tmp_path):
        client = self._client(tmp_path)
        run_upload_sequence(client, self._PLAN)

        steps = [e["step"] for e in client.journal]
        subject_idx = steps.index("person:subject")
        # The couple and the subject's own parent-CPR come right after — not deferred
        # until after child_0 (the other relative) is also created.
        assert steps[subject_idx + 1] == "couple:0"
        assert steps[subject_idx + 2] == "cpr:0"
        assert "person:child_0" not in steps[: subject_idx + 3]

    def test_result_pids_are_still_correct_regardless_of_order(self, tmp_path):
        client = self._client(tmp_path)
        result = run_upload_sequence(client, self._PLAN)
        assert set(result["persons"].keys()) == {"subject", "spouse_0", "parent_0", "parent_1", "child_0"}
        assert len(result["couples"]) == 1
        assert len(result["child_and_parents"]) == 2


class TestPostCreateRelativeFallback:
    """FWL-013-H1: plan §4.2 step 4 / §3.4, degraded near-term form — Record Hinting
    Certification isn't granted yet, so this re-runs the same thin parent/spouse search
    §3.3 step 3 already ran, flagged so a caller never presents it as a real record-hint
    result. Deliberately not called from run_upload_sequence() — see the method's own
    docstring for why."""

    def test_only_searches_spouse_and_parent_roles(self, tmp_path):
        bodies_seen = []

        def handler(request):
            bodies_seen.append(json.loads(request.content))
            return httpx.Response(204)

        client = FamilySearchClient(access_token="tok", dry_run=True, journal_path=tmp_path / "journal.json",
                                     transport=httpx.MockTransport(handler))
        plan = {"persons": {
            "subject": {"names": []}, "spouse_0": {"names": []},
            "parent_0": {"names": []}, "child_0": {"names": []}, "sibling_0": {"names": []},
        }}
        results = client.post_create_relative_fallback(plan)

        assert {r["key"] for r in results} == {"spouse_0", "parent_0"}
        assert all(r["record_hint_status"] == "not_certified" for r in results)
        assert len(bodies_seen) == 2

    def test_is_a_live_read_even_under_dry_run(self, tmp_path):
        """Same rule as search_matches(): a read, so dry_run never short-circuits it."""
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(204)

        client = FamilySearchClient(access_token="tok", dry_run=True, journal_path=tmp_path / "journal.json",
                                     transport=httpx.MockTransport(handler))
        client.post_create_relative_fallback({"persons": {"subject": {"names": []}, "parent_0": {"names": []}}})

        assert len(calls) == 1

    def test_no_spouse_or_parent_in_plan_makes_no_calls(self, tmp_path):
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(204)

        client = FamilySearchClient(access_token="tok", dry_run=True, journal_path=tmp_path / "journal.json",
                                     transport=httpx.MockTransport(handler))
        results = client.post_create_relative_fallback({"persons": {"subject": {"names": []}, "child_0": {"names": []}}})

        assert results == []
        assert calls == []
