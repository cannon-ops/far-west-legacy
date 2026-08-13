"""fs_client.py — FamilySearchClient: journal, dry-run boundary, write sequencing.

All FamilySearch write traffic goes through client.send() (plan §4.1). No other module
should call FamilySearch directly. Read-only calls (auth, search/match GETs) always
execute for real; dry_run only short-circuits POST/PUT/DELETE.

Error handling matches plan §4.3: 429/503 honor Retry-After with backoff (cap
MAX_RETRIES), 401 raises FSAuthExpiredError (caller saves journal, bounces to
/auth/login), 4xx is not retried, network errors retry GETs only — a write is never
blind-retried.
"""

import hashlib
import json
import logging
import re
import time
from pathlib import Path

import httpx

from src import token_store

logger = logging.getLogger(__name__)

API_HOST = "https://apibeta.familysearch.org"
GEDCOMX_MEDIA_TYPE = "application/x-fs-v1+json"
MAX_RETRIES = 3

PERSONS_PATH = "/platform/tree/persons"
COUPLE_RELATIONSHIPS_PATH = "/platform/tree/couple-relationships"
CHILD_AND_PARENTS_RELATIONSHIPS_PATH = "/platform/tree/child-and-parents-relationships"
SOURCE_DESCRIPTIONS_PATH = "/platform/sources/descriptions"

# Person Matches by Example resource (plan §3.1, §8). Path and query params
# (count, confidence 1-5) confirmed via developers.familysearch.org 2026-08-08.
# The response envelope shape was originally guessed (not verifiable without a
# working access token) — confirmed live 2026-08-12 (FWL-012-H9) against a real
# match, see _parse_match_candidates()/_principal_person()/MATCH_BUCKET_* for what
# the guess got wrong.
MATCHES_PATH = "/platform/tree/matches"

# FamilySearch's own docs (Read Person Matches using Gedcomx usecase, fetched live
# 2026-08-12) show the full required reference chain: the primary person carries a
# local `id`; a `sourceDescriptions` entry carries its own `id` and an `about`
# pointing at the person (`"about": "#<personId>"`); and the top-level document
# carries a `description` pointing at that source description
# (`"description": "#<sourceDescriptionId>"`). All three pieces are required —
# confirmed the hard way: a first attempt at this fix (FWL-012-H5) added the
# `sourceDescriptions`/`about` link but left out the top-level `description`, and
# FamilySearch rejected it with the exact same error as before having any of this:
# "The gedcomx must contain a descriptionRef." These ids are scoped to one
# search_matches() request body only; never sent to any other endpoint (person
# creation does not need or want them).
MATCH_PRIMARY_PERSON_ID = "primary"
MATCH_SOURCE_DESCRIPTION_ID = "sourceDescription"

# Bucket cutoffs against FS's real `score` field — a 0.0-1.0 similarity float
# (confirmed live 2026-08-12, FWL-012-H9: a name/date/place-exact match scored
# 0.9998555), not the 1-5 integer scale originally guessed here before any real
# match response had been seen. `entry.confidence` also exists in the real payload
# (value 5 for that same match) but is a different, uncalibrated field — not the
# 1-5 quality scale it was assumed to be, since a value of 5 there did not track a
# near-perfect 0.9998555 score. Only `score` is used for bucketing now (see
# _parse_match_candidates). These cutoffs are anchored to one confirmed real data
# point at the top of the scale; still not empirically tuned in the middle —
# revisit if a genuinely "possible" or "weak" real match is ever seen.
MATCH_BUCKET_STRONG_MIN = 0.90
MATCH_BUCKET_POSSIBLE_MIN = 0.50

_YEAR_RE = re.compile(r"\b(\d{4})\b")


def bucket_for_confidence(confidence: float | int | None) -> str:
    """Strong/Possible/Weak bucketing against FS's real 0.0-1.0 `score` scale
    (see MATCH_BUCKET_* above)."""
    if confidence is None:
        return "weak"
    if confidence >= MATCH_BUCKET_STRONG_MIN:
        return "strong"
    if confidence >= MATCH_BUCKET_POSSIBLE_MIN:
        return "possible"
    return "weak"


def _display_name(person: dict) -> str:
    display = person.get("display") or {}
    if display.get("name"):
        return display["name"]
    for name in person.get("names", []):
        for form in name.get("nameForms", []):
            if form.get("fullText"):
                return form["fullText"]
            parts = [p.get("value", "") for p in form.get("parts", [])]
            if any(parts):
                return " ".join(p for p in parts if p)
    return "(name unavailable)"


def _year_from_display_date(date_str: str) -> str:
    """FamilySearch's `display.birthDate`/`display.deathDate` are natural-language
    strings ("8 February 1935"), not ISO dates — pull the year out for a lifespan
    display. Confirmed live 2026-08-12 (FWL-012-H9)."""
    if not date_str:
        return ""
    m = _YEAR_RE.search(date_str)
    return m.group(1) if m else ""


def _lifespan_from_display(display: dict) -> str:
    """FamilySearch's real `display` object has no `lifespan` field at all — confirmed
    live 2026-08-12 (FWL-012-H9), it was a guessed key that never matched anything,
    so this always rendered blank in production. Derives one from the real
    `birthDate`/`deathDate` fields instead. `display.get("lifespan")` is still tried
    first in case some other response shape does send a pre-formatted one."""
    if display.get("lifespan"):
        return display["lifespan"]
    birth_year = _year_from_display_date(display.get("birthDate", ""))
    death_year = _year_from_display_date(display.get("deathDate", ""))
    if not birth_year and not death_year:
        return ""
    return f"{birth_year or '?'}–{death_year or '?'}"


def _principal_person(entry: dict) -> dict:
    """The matched person lives at entry.content.gedcomx.persons, flagged
    `"principal": true` — confirmed live 2026-08-12 (FWL-012-H9): a real match
    entry carried 4 persons (the match itself plus her father, mother, and husband,
    included by FamilySearch for context), not the single `entry.person` this code
    previously assumed. Falls back to the old guessed shape if no principal person
    is found, rather than raising, matching this module's existing degrade-gracefully
    philosophy for an unexpected response shape."""
    persons = entry.get("content", {}).get("gedcomx", {}).get("persons", [])
    for person in persons:
        if person.get("principal"):
            return person
    return entry.get("person", entry)


def _parse_match_candidates(payload: dict) -> list[dict]:
    """Parse a Person Matches by Example response into a flat candidate list.
    Confirmed live 2026-08-12 (FWL-012-H9) against a real match — see
    _principal_person() and MATCH_BUCKET_* for what was wrong and what changed."""
    entries = payload.get("persons") or payload.get("entries") or []
    candidates = []
    for entry in entries:
        person = _principal_person(entry)
        confidence = entry.get("score")
        display = person.get("display") or {}
        candidates.append({
            "pid": person.get("id"),
            "name": _display_name(person),
            "lifespan": _lifespan_from_display(display),
            "confidence": confidence,
            "bucket": bucket_for_confidence(confidence),
        })
    return candidates


class FSClientError(Exception):
    """Non-retryable FamilySearch API failure."""


class FSAuthExpiredError(FSClientError):
    """401 mid-sequence — caller should save the journal and bounce to /auth/login."""


class FSUncertainWriteError(FSClientError):
    """A previous run sent this write but never recorded an outcome, so we do not know
    whether it landed in the tree. Resuming would risk creating the person twice, so the
    sequence halts and a human checks FamilySearch. Never auto-retried."""


class FSJobLockedError(FSClientError):
    """Another request already holds this upload job. Raised for the duplicate-tab and
    double-submit cases so only one sequence can be writing a given job at a time."""


def _digest(body: dict | None) -> str:
    if not body:
        return ""
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()[:16]


class FamilySearchClient:
    def __init__(self, access_token: str, dry_run: bool, journal_path: str | Path, transport: httpx.BaseTransport | None = None):
        self.access_token = access_token
        self.dry_run = dry_run
        self.journal_path = Path(journal_path)
        self.journal: list[dict] = []
        self._pid_counter = 0
        self._http = httpx.Client(base_url=API_HOST, timeout=15.0, transport=transport)

        if self.journal_path.exists():
            self.journal = json.loads(self.journal_path.read_text(encoding="utf-8"))

    def close(self) -> None:
        self._http.close()

    def _next_dry_run_pid(self) -> str:
        self._pid_counter += 1
        return f"DRYRUN-P{self._pid_counter:03d}"

    def _write_journal(self) -> None:
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        self.journal_path.write_text(json.dumps(self.journal, indent=2), encoding="utf-8")

    def _completed_entry(self, step: str) -> dict | None:
        for entry in self.journal:
            if entry["step"] == step and entry["status"] in ("ok", "dry_run"):
                return entry
        return None

    def _entry(self, step: str) -> dict | None:
        for entry in self.journal:
            if entry["step"] == step:
                return entry
        return None

    def _append(self, step: str, method: str, url: str, digest: str, status: str,
                resulting_pid: str | None, processing_time_ms: str | None = None) -> dict:
        entry = {
            "step": step,
            "method": method,
            "url": url,
            "body_digest": digest,
            "status": status,
            "resulting_pid": resulting_pid,
            "processing_time_ms": processing_time_ms,
            "ts": time.time(),
        }
        self.journal.append(entry)
        self._write_journal()
        return entry

    def _settle(self, entry: dict, status: str, resulting_pid: str | None,
                processing_time_ms: str | None) -> None:
        """Close out an in-flight entry in place. Pairs with the pre-call _append so the
        journal always shows intent before outcome (plan §4.2)."""
        entry["status"] = status
        entry["resulting_pid"] = resulting_pid
        entry["processing_time_ms"] = processing_time_ms
        entry["ts"] = time.time()
        self._write_journal()

    def send(self, step: str, method: str, url: str, body: dict | None = None) -> str | None:
        """The single boundary all FS traffic goes through. Idempotent per `step`:
        a step already completed (ok or dry_run) in the journal is not re-sent."""
        existing = self._completed_entry(step)
        if existing:
            logger.info("fs_client: step %s already completed (%s) — resuming from journal", step, existing["status"])
            return existing.get("resulting_pid")

        # An in_flight entry means a previous run sent this and died before hearing back.
        # Reads are free to retry; a write is not, because it may already be in the tree.
        stale = self._entry(step)
        if stale is not None and stale["status"] == "in_flight" and method != "GET":
            logger.error("fs_client: step %s was in flight when the last run ended — refusing to resend", step)
            raise FSUncertainWriteError(
                f"Step {step} ({method} {url}) was sent but never confirmed. It may already "
                f"exist in FamilySearch. Check the tree before resuming this upload."
            )

        digest = _digest(body)

        if self.dry_run and method != "GET":
            pid = self._next_dry_run_pid()
            self._append(step, method, url, digest, "dry_run", pid)
            logger.info("DRY RUN — would %s %s (step=%s) -> %s", method, url, step, pid)
            return pid

        return self._send_live(step, method, url, body, digest)

    def search_matches(self, person_gedcomx: dict, count: int = 5) -> list[dict]:
        """Person Matches by Example (plan §3.1) — a read, so it always executes for
        real regardless of dry_run, and (unlike send()) is not journaled: match
        results aren't a write and don't need idempotent resume."""
        primary = {**person_gedcomx, "id": MATCH_PRIMARY_PERSON_ID}
        body = {
            "description": f"#{MATCH_SOURCE_DESCRIPTION_ID}",
            "persons": [primary],
            "sourceDescriptions": [{
                "id": MATCH_SOURCE_DESCRIPTION_ID,
                "about": f"#{MATCH_PRIMARY_PERSON_ID}",
            }],
        }
        try:
            resp = self._http.post(
                f"{MATCHES_PATH}?count={count}",
                json=body,
                headers={
                    "Authorization": f"Bearer {self.access_token}",
                    "Content-Type": GEDCOMX_MEDIA_TYPE,
                    "Accept": "application/json",
                },
            )
        except httpx.TransportError as exc:
            logger.error("fs_client: network error on match search", exc_info=True)
            raise FSClientError(f"Network error on match search: {exc}") from exc

        if resp.status_code == 401:
            raise FSAuthExpiredError("FamilySearch session expired — re-auth required")
        if resp.status_code >= 400:
            body_preview = resp.text[:500]
            logger.error("fs_client: match search failed: %s %s", resp.status_code, body_preview)
            raise FSClientError(f"Match search failed: HTTP {resp.status_code}, body: {body_preview!r}")

        # FamilySearch returns 204 No Content — no body at all — when the search finds
        # zero candidates, rather than 200 with an empty envelope. Confirmed live
        # 2026-08-12 (FWL-012-H8): this is a legitimate "no matches" outcome, not a parse
        # failure, and must not be treated as an error.
        if resp.status_code == 204:
            return []

        # A 2xx/3xx status does not otherwise guarantee a parseable JSON body — confirmed
        # live 2026-08-12 (FWL-012-H7): FamilySearch can return a response resp.json()
        # chokes on (json.decoder.JSONDecodeError: "Expecting value"), which previously
        # reached Flask's debugger raw instead of the app's normal error handling. Never
        # assume .json() succeeds just because the status check passed.
        try:
            payload = resp.json()
        except json.JSONDecodeError as exc:
            body_preview = resp.text[:500]
            logger.error("fs_client: match search returned unparseable body: %s %s", resp.status_code, body_preview)
            raise FSClientError(
                f"Match search returned an unparseable response: HTTP {resp.status_code}, body: {body_preview!r}"
            ) from exc

        return _parse_match_candidates(payload)

    def post_create_relative_fallback(self, plan: dict) -> list[dict]:
        """Plan §4.2 step 4 / §3.4, degraded near-term form. The long-term mechanism for
        finding a newly-created subject's parents/spouse/household is Record Hinting
        (birth/marriage/census matches against the new subject PID) — not available until
        FamilySearch grants Record Hinting Certification (requested 2026-08-12, gated on
        Solutions Provider acceptance). Until then, this re-runs the same §3.3 step 3
        parent/spouse Matches-by-Example search the match-check screen already ran, using
        the identical thin (name-only) request bodies. It genuinely finds nothing new —
        Matches by Example still has no way to accept a real PID as search context
        (confirmed FWL-012-H11, carried forward at plan §3.3) — its purpose is to keep this
        step visibly attempted rather than silently skipped, per §3.4's explicit
        instruction not to omit the row. Every entry is flagged `record_hint_status:
        "not_certified"` so a caller can render the same message search_note shows on the
        match-check screen instead of presenting this as if it were a real record-hint
        result.

        A read, like search_matches() — always live, not journaled, dry_run has no effect
        on it. **Not called automatically by run_upload_sequence()/_run_upload_sequence()
        (FWL-013-H1 decision, see repo-memory.md):** that function is exercised by fully
        offline tests with no transport mock (the dry-run golden test), and a search here
        would dial FamilySearch for real regardless of dry_run, same as search_matches()
        always does. It also has no way yet to know whether the subject was actually
        newly created this run or attached to an existing match — decisions aren't
        threaded into the plan until M2.3 builds that — so calling this unconditionally
        from inside the sequence would search even when the subject was already found and
        attached, which the plan does not call for. Intended caller: the M2.3 write route,
        once built, right after a real (non-dry-run, non-resumed) subject creation.
        """
        results = []
        for key, person_body in plan["persons"].items():
            role = key.split("_", 1)[0]
            if role not in ("spouse", "parent"):
                continue
            results.append({
                "key": key,
                "candidates": self.search_matches(person_body),
                "record_hint_status": "not_certified",
            })
        return results

    def _send_live(self, step: str, method: str, url: str, body: dict | None, digest: str) -> str | None:
        # Intent before outcome: if the process dies between here and _settle, the journal
        # shows in_flight and the next run halts instead of creating a duplicate person.
        entry = self._entry(step) or self._append(step, method, url, digest, "in_flight", None)

        for attempt in range(MAX_RETRIES + 1):
            try:
                resp = self._http.request(
                    method, url, json=body,
                    headers={
                        "Authorization": f"Bearer {self.access_token}",
                        "Content-Type": GEDCOMX_MEDIA_TYPE,
                        "Accept": GEDCOMX_MEDIA_TYPE,
                    },
                )
            except httpx.TransportError as exc:
                if method != "GET":
                    logger.error("fs_client: network error on %s %s, not retrying a write", method, url, exc_info=True)
                    raise FSClientError(f"Network error on {method} {url}: {exc}") from exc
                if attempt == MAX_RETRIES:
                    logger.error("fs_client: network error on GET %s, out of retries", url, exc_info=True)
                    raise FSClientError(f"Network error on {method} {url}: {exc}") from exc
                time.sleep(2 ** attempt)
                continue

            processing_time = resp.headers.get("X-PROCESSING-TIME")

            if resp.status_code == 401:
                # A rejected request did not land, so this is a clean error, not uncertain.
                self._settle(entry, "error", None, processing_time)
                raise FSAuthExpiredError("FamilySearch session expired — re-auth required")

            if resp.status_code in (429, 503) and attempt < MAX_RETRIES:
                retry_after = float(resp.headers.get("Retry-After", 2 ** attempt))
                logger.info("fs_client: %s on %s, retrying after %.1fs", resp.status_code, url, retry_after)
                time.sleep(retry_after)
                continue

            if resp.status_code >= 400:
                logger.error("fs_client: %s %s failed: %s %s", method, url, resp.status_code, resp.text)
                self._settle(entry, "error", None, processing_time)
                raise FSClientError(f"{method} {url} failed: HTTP {resp.status_code}")

            pid = _extract_pid(resp)
            self._settle(entry, "ok", pid, processing_time)
            return pid

        raise FSClientError(f"{method} {url} exhausted retries")


def _extract_pid(resp: httpx.Response) -> str | None:
    """FS create endpoints return the new resource id via a Location header (path tail)."""
    location = resp.headers.get("Location", "")
    return location.rstrip("/").rsplit("/", 1)[-1] if location else None


def _person_ref(pid: str) -> dict:
    return {"resource": f"{API_HOST}{PERSONS_PATH}/{pid}", "resourceId": pid}


def run_upload_sequence(client: FamilySearchClient, plan: dict,
                        job_id: str | None = None, owner: str | None = None) -> dict:
    """Execute (or dry-run) the write sequence from plan §4.2 against a fs_map plan.
    Persons first, then relationships (need PIDs from persons), then source + attach.

    Pass job_id and owner to take the cross-worker advisory lock for the duration. The
    journal alone does not stop two concurrent runs of the same job (both load an empty
    journal, both POST); the lock does. Callers that omit it get the old, unlocked
    behavior, which is only safe for tests and single-shot CLI use.
    """
    locked = False
    if job_id and owner:
        if not token_store.acquire_job_lock(job_id, owner):
            raise FSJobLockedError(
                f"Upload {job_id} is already running in another window. "
                f"Finish or close that one before starting again."
            )
        locked = True
    try:
        return _run_upload_sequence(client, plan)
    finally:
        if locked:
            token_store.release_job_lock(job_id, owner)


def _run_upload_sequence(client: FamilySearchClient, plan: dict) -> dict:
    """Order per plan §4.2 (amended FWL-013-H1, implementing the FWL-012-H12 subject-first
    strategy): spouse/parent persons are created (or, once §3.3 step 2/3's match-check
    already resolved them to a real PID, attached) *before* the subject, because they're
    the ones a not-found-subject's fallback search (§3.3 step 3) may already have anchored.
    The subject is created next, and its own couple/parent-CPR relationships are POSTed
    immediately after — not deferred to a separate later pass — so a confirmed relative
    never sits linked to nothing while the subject exists floating. Children/siblings (and
    their CPRs) still follow the subject, since those relationships need the subject's own
    PID to exist first."""
    persons = plan["persons"]
    couples = plan["relationships"]["couples"]
    cprs = plan["relationships"]["child_and_parents"]
    pids: dict[str, str] = {}

    def _create_person(key: str) -> str | None:
        pid = client.send(f"person:{key}", "POST", f"{API_HOST}{PERSONS_PATH}", persons[key])
        pids[key] = pid
        return pid

    def _create_couple(i: int, couple: dict) -> str | None:
        body = {"relationships": [{
            "type": "http://gedcomx.org/Couple",
            "person1": _person_ref(pids[couple["person1"]]),
            "person2": _person_ref(pids[couple["person2"]]),
        }]}
        return client.send(f"couple:{i}", "POST", f"{API_HOST}{COUPLE_RELATIONSHIPS_PATH}", body)

    def _create_cpr(i: int, cpr: dict) -> str | None:
        relationship = {"child": _person_ref(pids[cpr["child"]])}
        if cpr.get("parent1"):
            relationship["parent1"] = _person_ref(pids[cpr["parent1"]])
        if cpr.get("parent2"):
            relationship["parent2"] = _person_ref(pids[cpr["parent2"]])
        body = {"childAndParentsRelationships": [relationship]}
        return client.send(f"cpr:{i}", "POST", f"{API_HOST}{CHILD_AND_PARENTS_RELATIONSHIPS_PATH}", body)

    for key in persons:
        if key.startswith("spouse_") or key.startswith("parent_"):
            _create_person(key)

    _create_person("subject")

    couple_pids = [_create_couple(i, couple) for i, couple in enumerate(couples)]

    subject_cpr_indices = [i for i, cpr in enumerate(cprs) if cpr["child"] == "subject"]
    cpr_pids = [_create_cpr(i, cprs[i]) for i in subject_cpr_indices]

    # Children/siblings need the subject (or, for siblings, the parent CPR already made
    # above) to exist first, so their persons and CPRs both come after it.
    for key in persons:
        if key.startswith("child_") or key.startswith("sibling_"):
            _create_person(key)

    cpr_pids += [_create_cpr(i, cprs[i]) for i in range(len(cprs)) if i not in subject_cpr_indices]

    source_pid = None
    if plan.get("source"):
        source_pid = client.send(
            "source", "POST", f"{API_HOST}{SOURCE_DESCRIPTIONS_PATH}",
            {"sourceDescriptions": [plan["source"]]},
        )
        for key, pid in pids.items():
            attach_body = {"sourceReference": {"description": f"{API_HOST}{SOURCE_DESCRIPTIONS_PATH}/{source_pid}"}}
            client.send(f"attach:{key}", "POST", f"{API_HOST}{PERSONS_PATH}/{pid}/source-references", attach_body)

    return {
        "persons": pids,
        "couples": couple_pids,
        "child_and_parents": cpr_pids,
        "source": source_pid,
        "skipped": plan.get("skipped", []),
    }
