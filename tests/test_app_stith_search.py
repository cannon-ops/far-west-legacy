"""tests/test_app_stith_search.py — POST /search/stith and /search/stith/extract.

StithSource and extract_from_text are both monkeypatched to fakes so these stay
offline like every other app-route test (see tests/test_app_upload.py's FakeMatchClient
pattern) — no real network call, no real Claude API call.
"""

import pytest
from src import app as app_module
from src.obituary_source import ObituaryDetail, ObituaryStub, SearchUnavailable
from src.sources.stith_source import StithSourceError

MATCH = ObituaryStub(
    name="Daniel B Hughes",
    detail_url="https://www.stithfamilyfunerals.com/obituary/DanielB-Hughes",
    date="2026-08-07",
)


class FakeStithSource:
    SEARCH_RESULT = [MATCH]
    RAISE_ON_SEARCH = None
    RAISE_ON_FETCH = None

    def __init__(self, *args, **kwargs):
        pass

    def search(self, query):
        if FakeStithSource.RAISE_ON_SEARCH:
            raise FakeStithSource.RAISE_ON_SEARCH
        if query.lower() == "nomatch":
            return []
        return FakeStithSource.SEARCH_RESULT

    def fetch_detail(self, url):
        if FakeStithSource.RAISE_ON_FETCH:
            raise FakeStithSource.RAISE_ON_FETCH
        return ObituaryDetail(
            name="Daniel B. Hughes", text="Daniel B. Hughes obituary text.", source_url=url,
        )


def _fake_extract_from_text(text, source_url=""):
    return {
        "deceased": {
            "given_names": "Daniel", "surname": "Hughes", "maiden_name": "", "suffix": "",
            "gender": "Male", "birth_date": "1944-11-30", "birth_place": "",
            "death_date": "2026-08-07", "death_place": "", "burial_place": "",
        },
        "relationships": {"spouses": [], "parents": [], "children": [], "siblings": []},
        "eulogy_text": "",
        "service_details": "",
        "source_url": source_url,
        "raw_text": text,
    }


@pytest.fixture(autouse=True)
def _reset_fake_source():
    FakeStithSource.SEARCH_RESULT = [MATCH]
    FakeStithSource.RAISE_ON_SEARCH = None
    FakeStithSource.RAISE_ON_FETCH = None
    yield
    FakeStithSource.SEARCH_RESULT = [MATCH]
    FakeStithSource.RAISE_ON_SEARCH = None
    FakeStithSource.RAISE_ON_FETCH = None


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "TMP_DIR", tmp_path)
    monkeypatch.setattr(app_module, "StithSource", FakeStithSource)
    monkeypatch.setattr(app_module, "extract_from_text", _fake_extract_from_text)
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()


class TestSearchStith:
    def test_blank_query_shows_error(self, client):
        resp = client.post("/search/stith", data={"stith_query": "  "})
        assert resp.status_code == 200
        assert "Enter a name" in resp.get_data(as_text=True)

    def test_matches_render_results_page(self, client):
        resp = client.post("/search/stith", data={"stith_query": "Hughes"})
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "Daniel B Hughes" in body
        assert "2026-08-07" in body
        assert "/search/stith/extract" in body

    def test_no_matches_shows_no_results_message(self, client):
        resp = client.post("/search/stith", data={"stith_query": "nomatch"})
        assert resp.status_code == 200
        assert "No matches found" in resp.get_data(as_text=True)

    def test_search_unavailable_shows_error(self, client, monkeypatch):
        def raise_unavailable(self, query):
            return SearchUnavailable(reason="login required")

        monkeypatch.setattr(FakeStithSource, "search", raise_unavailable)
        resp = client.post("/search/stith", data={"stith_query": "Hughes"})
        assert resp.status_code == 200
        assert "unavailable" in resp.get_data(as_text=True).lower()

    def test_source_error_shows_error(self, client):
        FakeStithSource.RAISE_ON_SEARCH = StithSourceError("boom")
        resp = client.post("/search/stith", data={"stith_query": "Hughes"})
        assert resp.status_code == 200
        assert "Stith search failed" in resp.get_data(as_text=True)


class TestSearchStithExtract:
    def test_valid_pick_redirects_to_review(self, client):
        resp = client.post("/search/stith/extract", data={"detail_url": MATCH.detail_url})
        assert resp.status_code == 302
        assert "/review/" in resp.headers["Location"]

    def test_rejects_url_outside_stith_domain(self, client):
        resp = client.post("/search/stith/extract", data={"detail_url": "https://evil.example.com/x"})
        assert resp.status_code == 200
        assert "Invalid obituary link" in resp.get_data(as_text=True)

    def test_fetch_error_shows_error(self, client):
        FakeStithSource.RAISE_ON_FETCH = StithSourceError("network down")
        resp = client.post("/search/stith/extract", data={"detail_url": MATCH.detail_url})
        assert resp.status_code == 200
        assert "Could not fetch obituary" in resp.get_data(as_text=True)

    def test_extraction_error_shows_error(self, client, monkeypatch):
        from src.extract import ExtractionError

        def raise_extraction_error(text, source_url=""):
            raise ExtractionError("bad response")

        monkeypatch.setattr(app_module, "extract_from_text", raise_extraction_error)
        resp = client.post("/search/stith/extract", data={"detail_url": MATCH.detail_url})
        assert resp.status_code == 200
        assert "Extraction failed" in resp.get_data(as_text=True)

    def test_extracted_job_readable_from_review(self, client):
        resp = client.post("/search/stith/extract", data={"detail_url": MATCH.detail_url})
        job_url = resp.headers["Location"]
        review_resp = client.get(job_url)
        assert review_resp.status_code == 200
        assert "Hughes" in review_resp.get_data(as_text=True)
