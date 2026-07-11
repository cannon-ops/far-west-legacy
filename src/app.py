"""
app.py — Flask review UI for the Far West Legacy obituary extraction pipeline.

Routes:
  GET  /                  — marketing homepage (farwestlegacy.com landing)
  GET  /tool              — paste/URL input form
  POST /extract           — run extraction, redirect to review
  GET  /review/<job_id>   — editable review form
  POST /approve/<job_id>  — save approved JSON to output/
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

from flask import Flask, jsonify, redirect, render_template, request, send_file, url_for

from src.extract import ExtractionError, extract_from_text
from src.fetch import FetchError, fetch_obituary_text
from src.ingest import MAX_UPLOAD_BYTES, IngestError, ingest_upload
from src.transcribe import (
    TRANSCRIBE_MODEL,
    TranscriptionError,
    segment_probe,
    transcribe_page,
)
from src.version import APP_VERSION, CHANGELOG_TEXT

app = Flask(__name__, template_folder="../templates")
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-in-prod")
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES

BASE_DIR = Path(__file__).parent.parent
TMP_DIR = BASE_DIR / "tmp"
OUTPUT_DIR = BASE_DIR / "output"

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


def build_citation(deceased: dict, capture_meta: dict) -> tuple[str, bool]:
    """
    Assemble the source-citation line from capture metadata.

    "unknown" is a legal answer for newspaper and date; it produces a
    visibly weaker citation. Returns (citation_text, is_weak).
    """
    name = f"{deceased.get('given_names', '')} {deceased.get('surname', '')}".strip()
    newspaper = (capture_meta.get("newspaper") or "unknown").strip()
    pub_date = (capture_meta.get("publication_date") or "unknown").strip()
    weak = newspaper.lower() == "unknown" or pub_date.lower() == "unknown"
    paper_part = "unidentified newspaper" if newspaper.lower() == "unknown" else newspaper
    date_part = "date unknown" if pub_date.lower() == "unknown" else pub_date
    return f"Obituary of {name or 'unknown person'}, {paper_part}, {date_part}.", weak


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
        result = extract_from_text(obituary_text, source_url=source_url)
    except ExtractionError as exc:
        error = f"Extraction failed: {exc}"
        record_activity("extract_error", error_type="ExtractionError", message=str(exc))
        return render_template(
            "index.html",
            error=error,
            obituary_text=obituary_text,
            source_url=source_url,
        )

    job_id = str(uuid.uuid4())
    TMP_DIR.mkdir(exist_ok=True)
    _tmp_path(job_id).write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    record_activity("extract_ok", job_id=job_id, source=("url" if source_url else "paste"))
    return redirect(url_for("review", job_id=job_id))


@app.post("/upload")
def upload():
    """Scanned-obituary path (M3.1): ingest → segment probe → transcribe →
    existing extract → same review UI. Anthropic is the only network use."""
    file = request.files.get("scan_file")
    if file is None or not file.filename:
        record_activity("scan_error", error_type="ValidationError", message="missing file")
        return render_template("index.html", error="Please choose a scan file to upload.")

    # Capture metadata is asked every time; blank is a legal "unknown" and
    # produces a visibly weaker citation (Chief's ratified design).
    newspaper = (request.form.get("capture_newspaper") or "").strip() or "unknown"
    pub_date = (request.form.get("capture_date") or "").strip() or "unknown"

    try:
        manifest = ingest_upload(file.filename, file.read(), TMP_DIR)
    except IngestError as exc:
        record_activity("scan_error", error_type="IngestError", message=str(exc))
        return render_template("index.html", error=f"Could not ingest scan: {exc}")

    job_id = manifest["job_id"]
    job_dir = TMP_DIR / job_id

    try:
        # One probe per page; M3.1 handles exactly one obituary total.
        found = []  # (page_number, obituary dict)
        for page_no, page_file in enumerate(manifest["page_files"], 1):
            probe = segment_probe(job_dir / page_file)
            for obit in probe.get("obituaries") or []:
                found.append((page_no, obit))

        if not found:
            record_activity("scan_error", error_type="NoObituary", job_id=job_id)
            return render_template(
                "index.html",
                error="No obituary was found on the uploaded scan. "
                      "Check that the image shows the obituary clearly.",
            )
        if len(found) > 1:
            names = ", ".join(o.get("name", "?") for _, o in found)
            record_activity("scan_error", error_type="MultiObituary",
                            job_id=job_id, count=len(found))
            return render_template(
                "index.html",
                error=f"This scan contains {len(found)} obituaries ({names}). "
                      "Multi-obituary pages arrive in M3.3 — for now, upload a "
                      "single-obituary clipping or crop the page first.",
            )

        page_no = found[0][0]
        transcript = transcribe_page(job_dir / manifest["page_files"][page_no - 1])
    except TranscriptionError as exc:
        record_activity("scan_error", error_type="TranscriptionError", message=str(exc))
        return render_template("index.html", error=f"Transcription failed: {exc}")

    try:
        result = extract_from_text(transcript["text"])
    except ExtractionError as exc:
        record_activity("scan_error", error_type="ExtractionError", message=str(exc))
        return render_template("index.html", error=f"Extraction failed: {exc}")

    result["source_url"] = ""
    result["raw_text"] = transcript["text"]  # transcript is the durable evidence
    result["scan"] = {
        "job_id": job_id,
        "page": page_no,
        "pages": manifest["pages"],
        "original_filename": manifest["original_filename"],
        "sha256": manifest["sha256"],
        "transcription_model": TRANSCRIBE_MODEL,
        "illegible_spans": transcript.get("illegible_spans") or [],
        "layout_notes": transcript.get("layout_notes") or "",
        "portrait": transcript.get("portrait") or {"present": False},
        "header_context": transcript.get("header_context") or {},
    }
    result["capture_meta"] = {
        "newspaper": newspaper,
        "publication_date": pub_date,
        "entered_by": "operator",
        "auto_detected": transcript.get("header_context") or {},
    }

    TMP_DIR.mkdir(exist_ok=True)
    _tmp_path(job_id).write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    record_activity("scan_ok", job_id=job_id, pages=manifest["pages"],
                    illegible=len(result["scan"]["illegible_spans"]))
    return redirect(url_for("review", job_id=job_id))


@app.get("/scan/<job_id>/<int:page>")
def scan_image(job_id: str, page: int):
    """Serve a normalized page image from a scan job dir (review UI)."""
    try:
        uuid.UUID(job_id)  # reject anything that isn't a bare job UUID
    except ValueError:
        return "Not found", 404
    path = TMP_DIR / job_id / f"page_{page}.png"
    if not path.exists():
        return "Not found", 404
    return send_file(path, mimetype="image/png")


@app.get("/review/<job_id>")
def review(job_id: str):
    tmp = _tmp_path(job_id)
    if not tmp.exists():
        return redirect(url_for("tool"))
    result = json.loads(tmp.read_text(encoding="utf-8"))
    citation = citation_weak = None
    if result.get("capture_meta"):
        citation, citation_weak = build_citation(result.get("deceased", {}), result["capture_meta"])
    return render_template("review.html", job_id=job_id, data=result,
                           citation=citation, citation_weak=citation_weak)


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

    # Scan jobs (M3.1): carry provenance through; capture metadata is
    # human-confirmable in the review form, so re-read it here.
    if original.get("scan"):
        result["scan"] = original["scan"]
    if original.get("capture_meta"):
        capture_meta = dict(original["capture_meta"])
        capture_meta["newspaper"] = (
            request.form.get("capture_newspaper") or ""
        ).strip() or "unknown"
        capture_meta["publication_date"] = (
            request.form.get("capture_date") or ""
        ).strip() or "unknown"
        result["capture_meta"] = capture_meta
        result["citation"] = build_citation(deceased, capture_meta)[0]

    OUTPUT_DIR.mkdir(exist_ok=True)
    filename = _output_filename(deceased)
    out_path = OUTPUT_DIR / filename
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    # Clean up tmp file
    tmp.unlink(missing_ok=True)

    return render_template("confirmed.html", filename=filename, out_path=str(out_path), data=result)


# ---------------------------------------------------------------------------
# FWL 005 routes: version banner + logs modal
# ---------------------------------------------------------------------------

@app.context_processor
def inject_version():
    return {"app_version": APP_VERSION}


@app.route("/changelog")
def changelog():
    return jsonify({"version": APP_VERSION, "markdown": CHANGELOG_TEXT})


@app.route("/logs")
def logs():
    return jsonify({
        "app": list(APP_LOG_BUFFER),
        "activity": ACTIVITY_LOG,
    })


if __name__ == "__main__":
    port = int(os.getenv("FLASK_PORT", 8081))
    app.run(host="0.0.0.0", port=port, debug=True)
