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

logger = logging.getLogger(__name__)

API_HOST = "https://apibeta.familysearch.org"
GEDCOMX_MEDIA_TYPE = "application/x-fs-v1+json"
MAX_RETRIES = 3

PERSONS_PATH = "/platform/tree/persons"
COUPLE_RELATIONSHIPS_PATH = "/platform/tree/couple-relationships"
CHILD_AND_PARENTS_RELATIONSHIPS_PATH = "/platform/tree/child-and-parents-relationships"
SOURCE_DESCRIPTIONS_PATH = "/platform/sources/descriptions"


class FSClientError(Exception):
    """Non-retryable FamilySearch API failure."""


class FSAuthExpiredError(FSClientError):
    """401 mid-sequence — caller should save the journal and bounce to /auth/login."""


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

    def _append(self, step: str, method: str, url: str, digest: str, status: str,
                resulting_pid: str | None, processing_time_ms: str | None = None) -> None:
        self.journal.append({
            "step": step,
            "method": method,
            "url": url,
            "body_digest": digest,
            "status": status,
            "resulting_pid": resulting_pid,
            "processing_time_ms": processing_time_ms,
            "ts": time.time(),
        })
        self._write_journal()

    def send(self, step: str, method: str, url: str, body: dict | None = None) -> str | None:
        """The single boundary all FS traffic goes through. Idempotent per `step`:
        a step already completed (ok or dry_run) in the journal is not re-sent."""
        existing = self._completed_entry(step)
        if existing:
            logger.info("fs_client: step %s already completed (%s) — resuming from journal", step, existing["status"])
            return existing.get("resulting_pid")

        digest = _digest(body)

        if self.dry_run and method != "GET":
            pid = self._next_dry_run_pid()
            self._append(step, method, url, digest, "dry_run", pid)
            logger.info("DRY RUN — would %s %s (step=%s) -> %s", method, url, step, pid)
            return pid

        return self._send_live(step, method, url, body, digest)

    def _send_live(self, step: str, method: str, url: str, body: dict | None, digest: str) -> str | None:
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
                self._append(step, method, url, digest, "error", None, processing_time)
                raise FSAuthExpiredError("FamilySearch session expired — re-auth required")

            if resp.status_code in (429, 503) and attempt < MAX_RETRIES:
                retry_after = float(resp.headers.get("Retry-After", 2 ** attempt))
                logger.info("fs_client: %s on %s, retrying after %.1fs", resp.status_code, url, retry_after)
                time.sleep(retry_after)
                continue

            if resp.status_code >= 400:
                logger.error("fs_client: %s %s failed: %s %s", method, url, resp.status_code, resp.text)
                self._append(step, method, url, digest, "error", None, processing_time)
                raise FSClientError(f"{method} {url} failed: HTTP {resp.status_code}")

            pid = _extract_pid(resp)
            self._append(step, method, url, digest, "ok", pid, processing_time)
            return pid

        raise FSClientError(f"{method} {url} exhausted retries")


def _extract_pid(resp: httpx.Response) -> str | None:
    """FS create endpoints return the new resource id via a Location header (path tail)."""
    location = resp.headers.get("Location", "")
    return location.rstrip("/").rsplit("/", 1)[-1] if location else None


def _person_ref(pid: str) -> dict:
    return {"resource": f"{API_HOST}{PERSONS_PATH}/{pid}", "resourceId": pid}


def run_upload_sequence(client: FamilySearchClient, plan: dict) -> dict:
    """Execute (or dry-run) the write sequence from plan §4.2 against a fs_map plan.
    Persons first, then relationships (need PIDs from persons), then source + attach."""
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
