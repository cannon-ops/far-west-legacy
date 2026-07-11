"""
tests/test_app_scan.py — Flask scan-upload flow (M3.1): upload → review →
approve, with vision + extraction mocked. No network.
"""

import io
import json

import pytest
import src.app as app_module
from PIL import Image

TRANSCRIPT = {
    "text": "NEESE — Donna Sue Neese, age 86.\nBorn July 25, 1939, in [illegible:1].",
    "illegible_spans": [
        {"marker": "[illegible:1]", "guess": "Jamesport", "reason": "ink blot"}
    ],
    "layout_notes": "single column clipping",
    "portrait": {"present": True, "region": "top-right", "caption": None},
    "header_context": {"newspaper": "Gallatin North Missourian",
                       "page_date": None, "page_number": None},
}

EXTRACTION = {
    "deceased": {
        "given_names": "Donna Sue", "surname": "Neese", "maiden_name": "",
        "suffix": "", "gender": "Female", "birth_date": "1939-07-25",
        "birth_place": "", "death_date": "2025-12-10", "death_place": "",
        "burial_place": "",
    },
    "relationships": {"spouses": [], "parents": [], "children": [], "siblings": []},
    "eulogy_text": "A gentle soul.",
    "service_details": "",
    "source_url": "",
    "raw_text": "",
}


def _png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (400, 300), "white").save(buf, "PNG")
    return buf.getvalue()


def _single_obit_probe(image_path):
    return {"count": 1, "obituaries": [{"name": "Donna Sue Neese",
            "bbox": {"left": 0.0, "top": 0.0, "right": 1.0, "bottom": 1.0}}]}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "TMP_DIR", tmp_path / "tmp")
    monkeypatch.setattr(app_module, "OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setattr(app_module, "segment_probe", _single_obit_probe)
    monkeypatch.setattr(app_module, "transcribe_page", lambda p: dict(TRANSCRIPT))
    monkeypatch.setattr(app_module, "extract_from_text",
                        lambda text, source_url="": json.loads(json.dumps(EXTRACTION)))
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()


def _upload(client, newspaper="", date=""):
    return client.post("/upload", data={
        "scan_file": (io.BytesIO(_png_bytes()), "neese_clip.png"),
        "capture_newspaper": newspaper,
        "capture_date": date,
    }, content_type="multipart/form-data")


class TestUpload:
    def test_happy_path_redirects_to_review(self, client, tmp_path):
        resp = _upload(client)
        assert resp.status_code == 302
        job_id = resp.headers["Location"].rsplit("/", 1)[-1]

        saved = json.loads((tmp_path / "tmp" / f"{job_id}.json").read_text(encoding="utf-8"))
        assert saved["raw_text"] == TRANSCRIPT["text"]
        assert saved["source_url"] == ""
        assert saved["scan"]["job_id"] == job_id
        assert saved["scan"]["illegible_spans"] == TRANSCRIPT["illegible_spans"]
        assert saved["capture_meta"]["newspaper"] == "unknown"
        assert saved["capture_meta"]["publication_date"] == "unknown"
        assert saved["capture_meta"]["auto_detected"]["newspaper"] == "Gallatin North Missourian"

    def test_capture_fields_recorded(self, client, tmp_path):
        resp = _upload(client, newspaper="Tri-County Weekly", date="2025-12-18")
        job_id = resp.headers["Location"].rsplit("/", 1)[-1]
        saved = json.loads((tmp_path / "tmp" / f"{job_id}.json").read_text(encoding="utf-8"))
        assert saved["capture_meta"]["newspaper"] == "Tri-County Weekly"
        assert saved["capture_meta"]["publication_date"] == "2025-12-18"

    def test_missing_file_is_error(self, client):
        resp = client.post("/upload", data={}, content_type="multipart/form-data")
        assert resp.status_code == 200
        assert b"choose a scan file" in resp.data

    def test_zero_obituaries_is_error(self, client, monkeypatch):
        monkeypatch.setattr(app_module, "segment_probe",
                            lambda p: {"count": 0, "obituaries": []})
        resp = _upload(client)
        assert b"No obituary was found" in resp.data

    def test_multi_obituary_deferred_to_m33(self, client, monkeypatch):
        monkeypatch.setattr(app_module, "segment_probe", lambda p: {
            "count": 2, "obituaries": [{"name": "A B"}, {"name": "C D"}]})
        resp = _upload(client)
        assert b"M3.3" in resp.data
        assert b"2 obituaries" in resp.data

    def test_transcription_error_surfaces(self, client, monkeypatch):
        from src.transcribe import TranscriptionError

        def boom(p):
            raise TranscriptionError("still truncated at max_tokens=8192")

        monkeypatch.setattr(app_module, "transcribe_page", boom)
        resp = _upload(client)
        assert b"Transcription failed" in resp.data
        assert b"still truncated" in resp.data


class TestReviewAndImage:
    def test_review_shows_transcript_and_weak_citation(self, client):
        resp = _upload(client)
        review = client.get(resp.headers["Location"])
        assert review.status_code == 200
        assert b"Transcript (verbatim)" in review.data
        assert b"unidentified newspaper" in review.data
        assert b"date unknown" in review.data
        assert b"Weak citation" in review.data
        assert b"[illegible:1]" in review.data
        assert b"Portrait photo detected" in review.data

    def test_strong_citation_when_capture_given(self, client):
        resp = _upload(client, newspaper="Tri-County Weekly", date="2025-12-18")
        review = client.get(resp.headers["Location"])
        assert b"Tri-County Weekly" in review.data
        assert b"Weak citation" not in review.data

    def test_scan_image_served(self, client):
        resp = _upload(client)
        job_id = resp.headers["Location"].rsplit("/", 1)[-1]
        img = client.get(f"/scan/{job_id}/1")
        assert img.status_code == 200
        assert img.mimetype == "image/png"

    def test_scan_image_rejects_non_uuid_job_id(self, client):
        assert client.get("/scan/..%2f..%2fsecrets/1").status_code == 404
        assert client.get("/scan/not-a-uuid/1").status_code == 404


class TestApprove:
    def test_approve_carries_scan_and_citation(self, client, tmp_path):
        resp = _upload(client)
        job_id = resp.headers["Location"].rsplit("/", 1)[-1]

        approve = client.post(f"/approve/{job_id}", data={
            "given_names": "Donna Sue",
            "surname": "Neese",
            "eulogy_text": "A gentle soul.",
            "capture_newspaper": "Gallatin North Missourian",
            "capture_date": "2025-12-18",
        })
        assert approve.status_code == 200

        out = json.loads(
            (tmp_path / "output" / "Neese_Donna_Sue.json").read_text(encoding="utf-8"))
        assert out["scan"]["job_id"] == job_id
        assert out["raw_text"] == TRANSCRIPT["text"]
        assert out["capture_meta"]["newspaper"] == "Gallatin North Missourian"
        assert out["citation"] == (
            "Obituary of Donna Sue Neese, Gallatin North Missourian, 2025-12-18."
        )

    def test_approve_blank_capture_stays_unknown(self, client, tmp_path):
        resp = _upload(client, newspaper="Tri-County Weekly")
        job_id = resp.headers["Location"].rsplit("/", 1)[-1]
        client.post(f"/approve/{job_id}", data={
            "given_names": "Donna Sue", "surname": "Neese",
            "capture_newspaper": "", "capture_date": "",
        })
        out = json.loads(
            (tmp_path / "output" / "Neese_Donna_Sue.json").read_text(encoding="utf-8"))
        assert out["capture_meta"]["newspaper"] == "unknown"
        assert "unidentified newspaper" in out["citation"]

    def test_text_path_output_unchanged(self, client, tmp_path):
        """Paste-path approve must not gain scan/capture keys."""
        (tmp_path / "tmp").mkdir(exist_ok=True)
        (tmp_path / "tmp" / "textjob.json").write_text(
            json.dumps(EXTRACTION), encoding="utf-8")
        client.post("/approve/textjob", data={
            "given_names": "Donna Sue", "surname": "Neese",
        })
        out = json.loads(
            (tmp_path / "output" / "Neese_Donna_Sue.json").read_text(encoding="utf-8"))
        assert "scan" not in out
        assert "capture_meta" not in out
        assert "citation" not in out
