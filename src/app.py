"""
app.py — Flask review UI for the Far West Legacy obituary extraction pipeline.

Routes:
  GET  /                        — marketing homepage (farwestlegacy.com landing)
  GET  /tool                    — paste/URL/Stith-search/Resthaven-search input form
  POST /extract                 — run extraction, redirect to review
  POST /search/stith            — Stith Family Funeral Home name search, list matches
  POST /search/stith/extract    — fetch a picked match, extract, redirect to review
  POST /search/resthaven        — Resthaven Mortuary name search, list matches
  POST /search/resthaven/extract — fetch a picked match, extract, redirect to review
  GET  /review/<job_id>         — editable review form
  POST /approve/<job_id>        — save approved JSON to output/
  GET  /auth/login, /callback   — FamilySearch OAuth2 sign-in (M2.0)
  GET  /upload/<job_id>         — match-check + confirm-gate screen (M2.2)
  POST /upload/<job_id>/decide  — record per-person decisions (M2.2)
"""

import json
import logging
import os
import sys
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

# Allow `python src/app.py` (script mode) in addition to `python -m src.app`
_project_root = Path(__file__).parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from flask import Flask, jsonify, redirect, render_template, request, session, url_for

from src import fs_auth, fs_map
from src.extract import ExtractionError, extract_from_text
from src.fetch import FetchError, fetch_obituary_text
from src.fs_client import FamilySearchClient, FSAuthExpiredError, FSClientError
from src.obituary_source import SearchUnavailable
from src.sources.resthaven_source import BASE_URL as RESTHAVEN_BASE_URL
from src.sources.resthaven_source import ResthavenSource, ResthavenSourceError
from src.sources.stith_source import BASE_URL as STITH_BASE_URL
from src.sources.stith_source import StithSource, StithSourceError
from src.version import APP_VERSION, CHANGELOG_TEXT

app = Flask(__name__, template_folder="../templates")

IS_PRODUCTION = os.getenv("FLASK_ENV", "").lower() == "production"

# The signed session cookie carries fs_sid, which is the only thing standing between a
# visitor and someone else's FamilySearch session. A guessable secret means a forgeable
# fs_sid. House rule: no silent fallbacks — production refuses to start without a real key.
_secret = os.getenv("FLASK_SECRET_KEY", "")
if not _secret:
    if IS_PRODUCTION:
        raise RuntimeError(
            "FLASK_SECRET_KEY is unset. Refusing to start in production: a default key "
            "lets anyone forge the session cookie that identifies a FamilySearch session."
        )
    _secret = "dev-secret-change-in-prod"
app.secret_key = _secret

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=IS_PRODUCTION,
)

BASE_DIR = Path(__file__).parent.parent
TMP_DIR = BASE_DIR / "tmp"
OUTPUT_DIR = BASE_DIR / "output"

# Gates the /upload/* routes per plan §0 ("gate all upload routes ... so prod
# deploys are unaffected") — the registered redirect_uri is localhost-only, so
# this feature only ever works on Dev. Default off.
FWL_FS_UPLOAD_ENABLED = os.getenv("FWL_FS_UPLOAD_ENABLED", "0").strip().lower() not in ("", "0", "false")
FWL_FS_DRY_RUN = os.getenv("FWL_FS_DRY_RUN", "0").strip().lower() not in ("", "0", "false")

# Full record detail opens on FamilySearch.org per the API-terms rule in CLAUDE.md —
# the in-app panel only ever shows the search-result summary (plan §3.2).
FS_PERSON_LINK_BASE = "https://www.familysearch.org/tree/person/details/"

ROLE_TO_REL_KEY = {"spouse": "spouses", "parent": "parents", "child": "children", "sibling": "siblings"}

# ---------------------------------------------------------------------------
# In-memory log buffers (FWL 005)
# Both reset on process restart; matches Render free-tier reality.
# ---------------------------------------------------------------------------
APP_LOG_BUFFER: deque = deque(maxlen=200)
ACTIVITY_LOG: list = []
ACTIVITY_LOG_MAX = 50


class RingBufferHandler(logging.Handler):
    """logging.Handler that appends formatted records to APP_LOG_BUFFER."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            APP_LOG_BUFFER.append({
                "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "msg": self.format(record),
            })
        except Exception:
            self.handleError(record)


_ring_handler = RingBufferHandler()
_ring_handler.setFormatter(logging.Formatter("%(message)s"))
_ring_handler.setLevel(logging.INFO)

# Attach to Werkzeug + src loggers; disable propagation to avoid duplicates
for _logger_name in ("werkzeug", "src"):
    _l = logging.getLogger(_logger_name)
    _l.addHandler(_ring_handler)
    _l.propagate = False
    if _l.level == logging.NOTSET or _l.level > logging.INFO:
        _l.setLevel(logging.INFO)

app.logger.addHandler(_ring_handler)
app.logger.propagate = False
app.logger.setLevel(logging.INFO)


def record_activity(event: str, **details) -> None:
    """Append a user-activity record to ACTIVITY_LOG (newest first)."""
    ACTIVITY_LOG.insert(0, {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "details": details,
    })
    del ACTIVITY_LOG[ACTIVITY_LOG_MAX:]


def _tmp_path(job_id: str) -> Path:
    return TMP_DIR / f"{job_id}.json"


def _output_filename(deceased: dict) -> str:
    surname = (deceased.get("surname") or "unknown").strip()
    given = (deceased.get("given_names") or "unknown").strip().replace(" ", "_")
    return f"{surname}_{given}.json"


def _extract_and_save(obituary_text: str, source_url: str, activity_source: str) -> str:
    """Shared tail of every input channel (paste, URL fetch, Stith/Resthaven search): run
    extraction, write tmp/<job_id>.json, log activity. Returns the new job_id.
    Raises ExtractionError — callers render their own error context per channel."""
    result = extract_from_text(obituary_text, source_url=source_url)
    job_id = str(uuid.uuid4())
    TMP_DIR.mkdir(exist_ok=True)
    _tmp_path(job_id).write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    record_activity("extract_ok", job_id=job_id, source=activity_source)
    return job_id


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/")
def index():
    """Marketing homepage — farwestlegacy.com landing."""
    return render_template("home.html")


@app.get("/tool")
def tool():
    """Obituary extraction tool — paste/URL input form."""
    return render_template("index.html")


@app.post("/extract")
def extract():
    obituary_text = request.form.get("obituary_text", "").strip()
    source_url = request.form.get("source_url", "").strip()
    error = None

    if source_url and not obituary_text:
        try:
            obituary_text = fetch_obituary_text(source_url)
        except FetchError as exc:
            error = f"Could not fetch URL: {exc}"
            record_activity("extract_error", error_type="FetchError", message=str(exc))
            return render_template("index.html", error=error, source_url=source_url)

    if not obituary_text:
        error = "Please paste obituary text or provide a URL."
        record_activity("extract_error", error_type="ValidationError", message="missing input")
        return render_template("index.html", error=error)

    try:
        job_id = _extract_and_save(obituary_text, source_url, "url" if source_url else "paste")
    except ExtractionError as exc:
        error = f"Extraction failed: {exc}"
        record_activity("extract_error", error_type="ExtractionError", message=str(exc))
        return render_template(
            "index.html",
            error=error,
            obituary_text=obituary_text,
            source_url=source_url,
        )

    return redirect(url_for("review", job_id=job_id))


@app.post("/search/stith")
def search_stith():
    query = request.form.get("stith_query", "").strip()
    if not query:
        return render_template(
            "index.html", error="Enter a name to search Stith Family Funeral Home.",
        )

    source = StithSource()
    try:
        results = source.search(query)
    except StithSourceError as exc:
        record_activity("stith_search_error", error=str(exc))
        return render_template("index.html", error=f"Stith search failed: {exc}")

    if isinstance(results, SearchUnavailable):
        return render_template("index.html", error=f"Stith search unavailable: {results.reason}")

    record_activity("stith_search", query=query, result_count=len(results))
    return render_template("stith_results.html", query=query, results=results)


@app.post("/search/stith/extract")
def search_stith_extract():
    detail_url = request.form.get("detail_url", "").strip()
    if not detail_url.startswith(STITH_BASE_URL):
        return render_template("index.html", error="Invalid obituary link.")

    source = StithSource()
    try:
        detail = source.fetch_detail(detail_url)
    except StithSourceError as exc:
        record_activity("stith_fetch_error", error=str(exc))
        return render_template("index.html", error=f"Could not fetch obituary: {exc}")

    try:
        job_id = _extract_and_save(detail.text, detail.source_url, "stith")
    except ExtractionError as exc:
        error = f"Extraction failed: {exc}"
        record_activity("extract_error", error_type="ExtractionError", message=str(exc))
        return render_template(
            "index.html",
            error=error,
            obituary_text=detail.text,
            source_url=detail.source_url,
        )

    return redirect(url_for("review", job_id=job_id))


@app.post("/search/resthaven")
def search_resthaven():
    query = request.form.get("resthaven_query", "").strip()
    if not query:
        return render_template(
            "index.html", error="Enter a name to search Resthaven Mortuary.",
        )

    source = ResthavenSource()
    try:
        results = source.search(query)
    except ResthavenSourceError as exc:
        record_activity("resthaven_search_error", error=str(exc))
        return render_template("index.html", error=f"Resthaven search failed: {exc}")

    if isinstance(results, SearchUnavailable):
        return render_template(
            "index.html", error=f"Resthaven search unavailable: {results.reason}",
        )

    record_activity("resthaven_search", query=query, result_count=len(results))
    return render_template("resthaven_results.html", query=query, results=results)


@app.post("/search/resthaven/extract")
def search_resthaven_extract():
    detail_url = request.form.get("detail_url", "").strip()
    if not detail_url.startswith(RESTHAVEN_BASE_URL):
        return render_template("index.html", error="Invalid obituary link.")

    source = ResthavenSource()
    try:
        detail = source.fetch_detail(detail_url)
    except ResthavenSourceError as exc:
        record_activity("resthaven_fetch_error", error=str(exc))
        return render_template("index.html", error=f"Could not fetch obituary: {exc}")

    try:
        job_id = _extract_and_save(detail.text, detail.source_url, "resthaven")
    except ExtractionError as exc:
        error = f"Extraction failed: {exc}"
        record_activity("extract_error", error_type="ExtractionError", message=str(exc))
        return render_template(
            "index.html",
            error=error,
            obituary_text=detail.text,
            source_url=detail.source_url,
        )

    return redirect(url_for("review", job_id=job_id))


@app.get("/review/<job_id>")
def review(job_id: str):
    tmp = _tmp_path(job_id)
    if not tmp.exists():
        return redirect(url_for("tool"))
    result = json.loads(tmp.read_text(encoding="utf-8"))
    return render_template("review.html", job_id=job_id, data=result)


@app.get("/approve/<job_id>")
def approve_get(job_id: str):
    """A GET to /approve/<id> means a stale bookmark or back-button.
    Silently redirect to /tool (kills 405 on cold load of post-confirmed URL)."""
    return redirect(url_for("tool"))


@app.post("/approve/<job_id>")
def approve(job_id: str):
    tmp = _tmp_path(job_id)
    if not tmp.exists():
        return redirect(url_for("tool"))

    original = json.loads(tmp.read_text(encoding="utf-8"))

    # --- Rebuild deceased ---
    deceased = {
        "given_names": request.form.get("given_names", "").strip(),
        "surname": request.form.get("surname", "").strip(),
        "maiden_name": request.form.get("maiden_name", "").strip(),
        "suffix": request.form.get("suffix", "").strip(),
        "gender": request.form.get("gender", "").strip(),
        "birth_date": request.form.get("birth_date", "").strip(),
        "birth_place": request.form.get("birth_place", "").strip(),
        "death_date": request.form.get("death_date", "").strip(),
        "death_place": request.form.get("death_place", "").strip(),
        "burial_place": request.form.get("burial_place", "").strip(),
    }

    def _collect_rel(prefix: str, fields: list[str]) -> list[dict]:
        entries = []
        idx = 0
        while True:
            key = f"{prefix}_{idx}_{fields[0]}"
            if key not in request.form:
                break
            entry = {}
            for f in fields:
                raw = request.form.get(f"{prefix}_{idx}_{f}", "")
                if f == "deceased":
                    entry[f] = raw == "true"
                else:
                    entry[f] = raw.strip()
            entries.append(entry)
            idx += 1
        return entries

    relationships = {
        "spouses": _collect_rel("spouse", ["given_names", "surname", "deceased"]),
        "parents": _collect_rel("parent", ["given_names", "surname", "maiden_name", "deceased"]),
        "children": _collect_rel("child", ["given_names", "surname", "deceased"]),
        "siblings": _collect_rel("sibling", ["given_names", "surname", "maiden_name", "deceased"]),
    }

    result = {
        "deceased": deceased,
        "relationships": relationships,
        "eulogy_text": request.form.get("eulogy_text", "").strip(),
        "service_details": request.form.get("service_details", "").strip(),
        "source_url": original.get("source_url", ""),
        "raw_text": original.get("raw_text", ""),
    }

    OUTPUT_DIR.mkdir(exist_ok=True)
    filename = _output_filename(deceased)
    out_path = OUTPUT_DIR / filename
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    # Keep tmp/<job_id>.json alive (now holding the approved data, not the raw
    # extraction) so /upload/<job_id> (M2.2) can load it after approval.
    tmp.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    return render_template("confirmed.html", job_id=job_id, filename=filename, out_path=str(out_path), data=result)


# ---------------------------------------------------------------------------
# FWL 010 routes: FamilySearch OAuth2 sign-in (M2.0)
# ---------------------------------------------------------------------------


@app.get("/auth/login")
def auth_login():
    client_id = os.getenv("FAMILYSEARCH_CLIENT_ID", "")
    redirect_uri = os.getenv("FAMILYSEARCH_REDIRECT_URI", "")
    if not client_id or not redirect_uri:
        return "FamilySearch OAuth is not configured (missing FAMILYSEARCH_CLIENT_ID / FAMILYSEARCH_REDIRECT_URI).", 500

    url, state = fs_auth.build_authorize_url(client_id, redirect_uri)
    session["fs_oauth_state"] = state
    logging.getLogger("src.app").info(
        "FamilySearch authorize redirect built. pkce=%s", fs_auth.DEFAULT_USE_PKCE
    )
    return redirect(url)


@app.get("/callback")
def auth_callback():
    fs_error = request.args.get("error")
    if fs_error:
        record_activity("fs_auth_error", error=fs_error, description=request.args.get("error_description", ""))
        return render_template("index.html", error=f"FamilySearch sign-in failed: {fs_error}")

    state = request.args.get("state", "")
    code = request.args.get("code", "")
    expected_state = session.pop("fs_oauth_state", None)
    if not code or not state or state != expected_state:
        record_activity("fs_auth_error", error="state_mismatch")
        return render_template("index.html", error="FamilySearch sign-in failed: invalid state.")

    client_id = os.getenv("FAMILYSEARCH_CLIENT_ID", "")
    try:
        token = fs_auth.exchange_code(client_id, code, state)
        user_data = fs_auth.fetch_current_user(token["access_token"])
    except fs_auth.FSAuthError as exc:
        record_activity("fs_auth_error", error=str(exc))
        return render_template("index.html", error=f"FamilySearch sign-in failed: {exc}")

    display_name = fs_auth.display_name_from_user_response(user_data)
    sid = session.get("fs_sid") or uuid.uuid4().hex
    session["fs_sid"] = sid
    fs_auth.store_session(sid, token, display_name)

    record_activity("fs_auth_ok", display_name=display_name, scope=token.get("scope", ""))
    return redirect(url_for("tool"))


@app.post("/auth/logout")
def auth_logout():
    """Explicit sign-out. On a shared booth device this is the control that stops the next
    visitor inheriting the last one's FamilySearch session, so it is a POST (no drive-by
    logout via a prefetched link) and it clears both the server-side token and the cookie."""
    sid = session.pop("fs_sid", None)
    if sid:
        fs_auth.clear_session(sid)
    session.pop("fs_oauth_state", None)
    record_activity("fs_auth_signed_out")
    return redirect(url_for("tool"))


# ---------------------------------------------------------------------------
# FWL 010 routes: match-check + confirm-gate (M2.2)
# ---------------------------------------------------------------------------


def _decisions_path(job_id: str) -> Path:
    return TMP_DIR / f"{job_id}.decisions.json"


def _plan_person_label(key: str, data: dict) -> dict:
    """Friendly name/lifespan for a fs_map plan person key, read from the original
    (not GEDCOM X) extraction data so it matches what the user reviewed/approved."""
    if key == "subject":
        person = data.get("deceased", {})
    else:
        role, _, idx = key.rpartition("_")
        rel_list = data.get("relationships", {}).get(ROLE_TO_REL_KEY.get(role, ""), [])
        person = rel_list[int(idx)] if idx.isdigit() and int(idx) < len(rel_list) else {}

    given = person.get("given_names", "")
    surname = person.get("surname", "")
    birth_year = (person.get("birth_date") or "")[:4]
    death_year = (person.get("death_date") or "")[:4]
    lifespan = f"{birth_year or '?'}–{death_year or '?'}" if (birth_year or death_year) else ""
    return {"name": f"{given} {surname}".strip() or "(unnamed)", "lifespan": lifespan}


@app.get("/upload/<job_id>")
def upload_match_check(job_id: str):
    if not FWL_FS_UPLOAD_ENABLED:
        return "FamilySearch upload is not enabled on this deployment.", 404

    tmp = _tmp_path(job_id)
    if not tmp.exists():
        return redirect(url_for("tool"))

    sid = session.get("fs_sid")
    fs_session = fs_auth.get_session(sid) if sid else None
    if not fs_session:
        return redirect(url_for("auth_login"))

    data = json.loads(tmp.read_text(encoding="utf-8"))
    plan = fs_map.map_extraction_to_plan(data)

    client = FamilySearchClient(
        access_token=fs_session["token"]["access_token"],
        dry_run=FWL_FS_DRY_RUN,
        journal_path=TMP_DIR / f"{job_id}.upload.json",
    )
    persons = []
    try:
        for key, person_body in plan["persons"].items():
            label = _plan_person_label(key, data)
            candidates = client.search_matches(person_body)
            persons.append({
                "key": key,
                "name": label["name"],
                "lifespan": label["lifespan"],
                "candidates": candidates,
                "has_strong_or_possible": any(c["bucket"] in ("strong", "possible") for c in candidates),
            })
    except FSAuthExpiredError:
        return redirect(url_for("auth_login"))
    except FSClientError as exc:
        record_activity("fs_match_error", job_id=job_id, error=str(exc))
        return render_template("index.html", error=f"FamilySearch match search failed: {exc}")
    finally:
        client.close()

    return render_template(
        "upload.html",
        job_id=job_id,
        persons=persons,
        skipped=plan["skipped"],
        person_link_base=FS_PERSON_LINK_BASE,
        dry_run=FWL_FS_DRY_RUN,
    )


@app.post("/upload/<job_id>/decide")
def upload_decide(job_id: str):
    if not FWL_FS_UPLOAD_ENABLED:
        return "FamilySearch upload is not enabled on this deployment.", 404

    tmp = _tmp_path(job_id)
    if not tmp.exists():
        return redirect(url_for("tool"))

    data = json.loads(tmp.read_text(encoding="utf-8"))
    plan = fs_map.map_extraction_to_plan(data)

    decisions = {}
    for key in plan["persons"]:
        decision = request.form.get(f"decision_{key}", "").strip()
        existing_pid = request.form.get(f"existing_pid_{key}", "").strip()
        if decision not in ("use_existing", "create_new", "skip"):
            return render_template("index.html", error=f"Missing or invalid decision for {key} — every person must be decided before committing.")
        if decision == "use_existing" and not existing_pid:
            return render_template("index.html", error=f"'{key}' is set to Use Existing but no candidate PID was selected.")
        decisions[key] = {"decision": decision, "existing_pid": existing_pid if decision == "use_existing" else None}

    _decisions_path(job_id).write_text(json.dumps(decisions, indent=2), encoding="utf-8")
    record_activity("fs_decisions_recorded", job_id=job_id, decisions=decisions)

    return render_template("decided.html", job_id=job_id, decisions=decisions)


# ---------------------------------------------------------------------------
# FWL 005 routes: version banner + logs modal
# ---------------------------------------------------------------------------

@app.context_processor
def inject_version():
    return {"app_version": APP_VERSION}


@app.context_processor
def inject_fs_user():
    # peek, not get: rendering a page must not slide the idle window, or a tab left open
    # on the kiosk would keep a walked-away visitor signed in indefinitely.
    sid = session.get("fs_sid")
    fs_session = fs_auth.peek_session(sid) if sid else None
    return {"fs_display_name": fs_session["display_name"] if fs_session else None}


@app.context_processor
def inject_fs_upload_enabled():
    return {"fs_upload_enabled": FWL_FS_UPLOAD_ENABLED}


@app.route("/changelog")
def changelog():
    return jsonify({"version": APP_VERSION, "markdown": CHANGELOG_TEXT})


@app.route("/logs")
def logs():
    # These buffers hold FamilySearch display names and raw API error bodies. On a booth
    # kiosk that means one visitor could read the previous visitor's name out of /logs, so
    # production serves it only when explicitly opted in. Also note the buffers are
    # per-worker: under `gunicorn -w 2` a request sees one worker's half of the log.
    if IS_PRODUCTION and os.getenv("FWL_LOGS_PUBLIC", "").lower() not in ("1", "true", "yes"):
        return jsonify({"error": "Logs are disabled in production."}), 404
    return jsonify({
        "app": list(APP_LOG_BUFFER),
        "activity": ACTIVITY_LOG,
    })


if __name__ == "__main__":
    port = int(os.getenv("FLASK_PORT", 8081))
    app.run(host="0.0.0.0", port=port, debug=True)
