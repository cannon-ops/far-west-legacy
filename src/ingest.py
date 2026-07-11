"""
ingest.py — Scan upload validation + normalization (M3.1).

Turns an uploaded scan (JPEG / PNG / TIFF / PDF) into a job directory of
normalized per-page PNG images ready for vision transcription, plus a
manifest. Per-page image conversion is the ratified default for PDFs
(docs/m3-0-eval-note.md §3) — native PDF input is an M3.4 batch question.

No network. Never persists anything outside tmp/<job_id>/.
"""

import hashlib
import io
import json
import uuid
from pathlib import Path

from PIL import Image

# M3.0 eval decision (docs/m3-0-eval-note.md §1): normalize transcription
# input to 1568px long edge — Haiku 4.5 reaches Sonnet parity at 1092px;
# 1568 buys margin for real newsprint at ~$0.0007/call extra. Never upscale.
TRANSCRIPTION_LONG_EDGE = 1568

# PDF pages render at scale 2 (144 dpi): a US-Letter page comes out at
# 1584px long edge, just above TRANSCRIPTION_LONG_EDGE before normalization.
PDF_RENDER_SCALE = 2

ACCEPTED_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".pdf"}

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
# Cost guard: every page fires a segmentation-probe vision call. Batches of
# large PDFs are M3.4 scope; the single-scan path caps page count.
MAX_PDF_PAGES = 20

MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".pdf": "application/pdf",
}


class IngestError(Exception):
    """Raised when an upload is rejected or cannot be normalized."""


def _normalize_page(img: Image.Image) -> Image.Image:
    """RGB-convert and downscale to TRANSCRIPTION_LONG_EDGE (never upscale)."""
    img = img.convert("RGB")
    if max(img.size) > TRANSCRIPTION_LONG_EDGE:
        scale = TRANSCRIPTION_LONG_EDGE / max(img.size)
        img = img.resize(
            (round(img.size[0] * scale), round(img.size[1] * scale)),
            Image.LANCZOS,
        )
    return img


def _pdf_pages(data: bytes) -> list[Image.Image]:
    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(data)
    except Exception as exc:
        raise IngestError(f"Could not open PDF: {exc}") from exc
    if len(pdf) > MAX_PDF_PAGES:
        raise IngestError(
            f"PDF has {len(pdf)} pages — the single-scan path accepts at most "
            f"{MAX_PDF_PAGES}. Batch intake arrives in M3.4."
        )
    return [page.render(scale=PDF_RENDER_SCALE).to_pil() for page in pdf]


def ingest_upload(filename: str, data: bytes, tmp_dir: Path) -> dict:
    """
    Validate and normalize an uploaded scan into a job directory.

    Writes tmp/<job_id>/ containing the original upload, one normalized
    page_<n>.png per page, and manifest.json.

    Returns the manifest dict:
        {job_id, sha256, original_filename, media, pages, page_files}

    Raises:
        IngestError: On unsupported type, oversize, empty, or unreadable input.
    """
    suffix = Path(filename).suffix.lower()
    if suffix not in ACCEPTED_SUFFIXES:
        raise IngestError(
            f"Unsupported file type '{suffix or filename}'. "
            "Accepted: JPEG, PNG, TIFF, PDF."
        )
    if not data:
        raise IngestError("Uploaded file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise IngestError(
            f"Upload is {len(data) / 1024 / 1024:.1f} MB — "
            f"limit is {MAX_UPLOAD_BYTES // 1024 // 1024} MB."
        )

    if suffix == ".pdf":
        pages = _pdf_pages(data)
    else:
        try:
            img = Image.open(io.BytesIO(data))
            img.load()
        except Exception as exc:
            raise IngestError(f"Could not read image: {exc}") from exc
        pages = [img]

    job_id = str(uuid.uuid4())
    job_dir = tmp_dir / job_id
    job_dir.mkdir(parents=True)
    (job_dir / f"original{suffix}").write_bytes(data)

    page_files = []
    for n, page in enumerate(pages, 1):
        name = f"page_{n}.png"
        _normalize_page(page).save(job_dir / name, "PNG")
        page_files.append(name)

    manifest = {
        "job_id": job_id,
        "sha256": hashlib.sha256(data).hexdigest(),
        "original_filename": filename,
        "media": MEDIA_TYPES[suffix],
        "pages": len(page_files),
        "page_files": page_files,
    }
    (job_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    return manifest
