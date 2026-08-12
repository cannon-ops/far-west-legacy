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
# (count, confidence 1-5) confirmed via developers.familysearch.org this session
# (2026-08-08); the exact response envelope shape was NOT verifiable against live
# docs without a working access token (M2.0 auth is currently broken — see
# repo-memory.md Pending Decisions), so `_parse_match_candidates` below is
# best-effort and defensive, not confirmed live. Re-verify at M2.3 build time.
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

# Placeholder bucket cutoffs against FS's 1-5 confidence scale (plan §9 open
# question 5 — "tuned empirically against beta during M2.2"). Cannot be tuned
# empirically until a live token exists; these are a reasonable starting split,
# not a measured threshold. Revisit once real match responses are seen.
MATCH_BUCKET_STRONG_MIN = 4
MATCH_BUCKET_POSSIBLE_MIN = 2


def bucket_for_confidence(confidence: float | int | None) -> str:
    """Placeholder Strong/Possible/Weak bucketing (see MATCH_BUCKET_* above)."""
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


def _parse_match_candidates(payload: dict) -> list[dict]:
    """Best-effort parse of a Person Matches by Example response into a flat
    candidate list. FamilySearch's response envelope was not verifiable live this
    session (see MATCHES_PATH note) — tries the shapes documented for FS person
    search/match resources and degrades to an empty list rather than raising if
    neither is present, so an unexpected shape shows "no matches" instead of
    crashing the match-check screen."""
    entries = payload.get("persons") or payload.get("entries") or []
    candidates = []
    for entry in entries:
        person = entry.get("person", entry)
        confidence = entry.get("score") if entry.get("score") is not None else entry.get("confidence")
        display = person.get("display") or {}
        candidates.append({
            "pid": person.get("id"),
            "name": _display_name(person),
            "lifespan": display.get("lifespan", ""),
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

        # A 2xx/3xx status does not guarantee a parseable JSON body — confirmed live
        # 2026-08-12 (FWL-012-H7): FamilySearch can return a response resp.json() chokes
        # on (json.decoder.JSONDecodeError: "Expecting value"), which previously reached
        # Flask's debugger raw instead of the app's normal error handling. Never assume
        # .json() succeeds just because the status check passed.
        try:
            payload = resp.json()
        except json.JSONDecodeError as exc:
            body_preview = resp.text[:500]
            logger.error("fs_client: match search returned unparseable body: %s %s", resp.status_code, body_preview)
            raise FSClientError(
                f"Match search returned an unparseable response: HTTP {resp.status_code}, body: {body_preview!r}"
            ) from exc

        return _parse_match_candidates(payload)

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
    pids: dict[str, str] = {}
    for key, person_body in plan["persons"].items():
        pids[key] = client.send(f"person:{key}", "POST", f"{API_HOST}{PERSONS_PATH}", person_body)

    couple_pids = []
    for i, couple in enumerate(plan["relationships"]["couples"]):
        body = {"relationships": [{
            "type": "http://gedcomx.org/Couple",
            "person1": _person_ref(pids[couple["person1"]]),
            "person2": _person_ref(pids[couple["person2"]]),
        }]}
        couple_pids.append(client.send(f"couple:{i}", "POST", f"{API_HOST}{COUPLE_RELATIONSHIPS_PATH}", body))

    cpr_pids = []
    for i, cpr in enumerate(plan["relationships"]["child_and_parents"]):
        relationship = {"child": _person_ref(pids[cpr["child"]])}
        if cpr.get("parent1"):
            relationship["parent1"] = _person_ref(pids[cpr["parent1"]])
        if cpr.get("parent2"):
            relationship["parent2"] = _person_ref(pids[cpr["parent2"]])
        body = {"childAndParentsRelationships": [relationship]}
        cpr_pids.append(client.send(f"cpr:{i}", "POST", f"{API_HOST}{CHILD_AND_PARENTS_RELATIONSHIPS_PATH}", body))

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
