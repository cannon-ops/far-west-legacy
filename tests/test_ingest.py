"""
tests/test_ingest.py — Scan upload validation + normalization (M3.1). Pure, no network.
"""

import hashlib
import io
import json

import pytest
import src.ingest as ingest
from PIL import Image
from src.ingest import TRANSCRIPTION_LONG_EDGE, IngestError, ingest_upload


def _image_bytes(fmt: str, size=(800, 600)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, "white").save(buf, fmt)
    return buf.getvalue()


def _pdf_bytes(n_pages: int) -> bytes:
    pages = [Image.new("RGB", (612, 792), "white") for _ in range(n_pages)]
    buf = io.BytesIO()
    pages[0].save(buf, "PDF", save_all=True, append_images=pages[1:])
    return buf.getvalue()


class TestValidation:
    def test_rejects_unsupported_suffix(self, tmp_path):
        with pytest.raises(IngestError, match="Unsupported file type"):
            ingest_upload("scan.bmp", _image_bytes("BMP"), tmp_path)

    def test_rejects_empty_data(self, tmp_path):
        with pytest.raises(IngestError, match="empty"):
            ingest_upload("scan.png", b"", tmp_path)

    def test_rejects_oversize(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ingest, "MAX_UPLOAD_BYTES", 10)
        with pytest.raises(IngestError, match="limit"):
            ingest_upload("scan.png", b"x" * 11, tmp_path)

    def test_rejects_corrupt_image(self, tmp_path):
        with pytest.raises(IngestError, match="Could not read image"):
            ingest_upload("scan.png", b"not an image at all", tmp_path)

    def test_rejects_corrupt_pdf(self, tmp_path):
        with pytest.raises(IngestError, match="Could not open PDF"):
            ingest_upload("scan.pdf", b"not a pdf", tmp_path)

    def test_rejects_pdf_over_page_cap(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ingest, "MAX_PDF_PAGES", 1)
        with pytest.raises(IngestError, match="at most 1"):
            ingest_upload("scan.pdf", _pdf_bytes(2), tmp_path)


class TestSinglePageImages:
    def test_png_manifest_and_files(self, tmp_path):
        data = _image_bytes("PNG")
        manifest = ingest_upload("clip.png", data, tmp_path)

        assert manifest["pages"] == 1
        assert manifest["page_files"] == ["page_1.png"]
        assert manifest["original_filename"] == "clip.png"
        assert manifest["media"] == "image/png"
        assert manifest["sha256"] == hashlib.sha256(data).hexdigest()

        job_dir = tmp_path / manifest["job_id"]
        assert (job_dir / "page_1.png").exists()
        assert (job_dir / "original.png").read_bytes() == data
        on_disk = json.loads((job_dir / "manifest.json").read_text(encoding="utf-8"))
        assert on_disk == manifest

    def test_small_image_not_upscaled(self, tmp_path):
        manifest = ingest_upload("clip.png", _image_bytes("PNG", (800, 600)), tmp_path)
        page = Image.open(tmp_path / manifest["job_id"] / "page_1.png")
        assert page.size == (800, 600)

    def test_large_image_downscaled_to_long_edge(self, tmp_path):
        manifest = ingest_upload("clip.png", _image_bytes("PNG", (4000, 2000)), tmp_path)
        page = Image.open(tmp_path / manifest["job_id"] / "page_1.png")
        assert max(page.size) == TRANSCRIPTION_LONG_EDGE
        assert page.size == (1568, 784)

    def test_tiff_converted_to_png(self, tmp_path):
        manifest = ingest_upload("clip.tiff", _image_bytes("TIFF"), tmp_path)
        assert manifest["media"] == "image/tiff"
        page = Image.open(tmp_path / manifest["job_id"] / "page_1.png")
        assert page.format == "PNG"

    def test_jpeg_accepted(self, tmp_path):
        manifest = ingest_upload("photo.JPG", _image_bytes("JPEG"), tmp_path)
        assert manifest["media"] == "image/jpeg"
        assert (tmp_path / manifest["job_id"] / "original.jpg").exists()


class TestPdf:
    def test_pdf_split_to_per_page_images(self, tmp_path):
        manifest = ingest_upload("scans.pdf", _pdf_bytes(2), tmp_path)
        assert manifest["pages"] == 2
        assert manifest["page_files"] == ["page_1.png", "page_2.png"]
        job_dir = tmp_path / manifest["job_id"]
        for name in manifest["page_files"]:
            page = Image.open(job_dir / name)
            # 612x792pt page at scale 2 = 1224x1584, then normalized to <=1568
            assert max(page.size) <= TRANSCRIPTION_LONG_EDGE
