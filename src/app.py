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
import os
import sys
import uuid
from pathlib import Path

# Allow `python src/app.py` (script mode) in addition to `python -m src.app`
_project_root = Path(__file__).parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from flask import Flask, redirect, render_template, request, url_for

from src.extract import ExtractionError, extract_from_text
from src.fetch import FetchError, fetch_obituary_text

app = Flask(__name__, template_folder="../templates")
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-in-prod")

BASE_DIR = Path(__file__).parent.parent
TMP_DIR = BASE_DIR / "tmp"
OUTPUT_DIR = BASE_DIR / "output"


def _tmp_path(job_id: str) -> Path:
    return TMP_DIR / f"{job_id}.json"


def _output_filename(deceased: dict) -> str:
    surname = (deceased.get("surname") or "unknown").strip()
    given = (deceased.get("given_names") or "unknown").strip().replace(" ", "_")
    return f"{surname}_{given}.json"


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
            return render_template("index.html", error=error, source_url=source_url)

    if not obituary_text:
        error = "Please paste obituary text or provide a URL."
        return render_template("index.html", error=error)

    try:
        result = extract_from_text(obituary_text, source_url=source_url)
    except ExtractionError as exc:
        error = f"Extraction failed: {exc}"
        return render_template(
            "index.html",
            error=error,
            obituary_text=obituary_text,
            source_url=source_url,
        )

    job_id = str(uuid.uuid4())
    TMP_DIR.mkdir(exist_ok=True)
    _tmp_path(job_id).write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    return redirect(url_for("review", job_id=job_id))


@app.get("/review/<job_id>")
def review(job_id: str):
    tmp = _tmp_path(job_id)
    if not tmp.exists():
        return render_template("index.html", error="Session expired or job not found. Please extract again.")
    result = json.loads(tmp.read_text(encoding="utf-8"))
    return render_template("review.html", job_id=job_id, data=result)


@app.post("/approve/<job_id>")
def approve(job_id: str):
    tmp = _tmp_path(job_id)
    if not tmp.exists():
        return render_template("index.html", error="Session expired or job not found.")

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

    # Clean up tmp file
    tmp.unlink(missing_ok=True)

    return render_template("confirmed.html", filename=filename, out_path=str(out_path), data=result)


if __name__ == "__main__":
    port = int(os.getenv("FLASK_PORT", 8081))
    app.run(host="0.0.0.0", port=port, debug=True)
