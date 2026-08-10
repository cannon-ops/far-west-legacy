"""tests/test_app_resthaven_search.py — POST /search/resthaven and
/search/resthaven/extract.

ResthavenSource and extract_from_text are both monkeypatched to fakes so these stay
offline like every other app-route test (see tests/test_app_upload.py's FakeMatchClient
pattern) — no real network call, no real Claude API call. Mirrors
tests/test_app_stith_search.py exactly; ResthavenSource's curl_cffi transport is not
exercised here (that's tests/test_resthaven_source.py's job), only the route wiring.
"""

import pytest
from src import app as app_module
from src.obituary_source import ObituaryDetail, ObituaryStub, SearchUnavailable
from src.sources.resthaven_source import ResthavenSourceError

MATCH = ObituaryStub(
    name="Victor Barb",
    detail_url="https://www.resthavenmort.com/obituary/Victor-Barb",
    date="2026-08-09",
)


class FakeResthavenSource:
    SEARCH_RESULT = [MATCH]
    RAISE_ON_SEARCH = None
    RAISE_ON_FETCH = None

    def __init__(self, *args, **kwargs):
        pass

    def search(self, query):
        if FakeResthavenSource.RAISE_ON_SEARCH:
            raise FakeResthavenSource.RAISE_ON_SEARCH
        if query.lower() == "nomatch":
            return []
        return FakeResthavenSource.SEARCH_RESULT

    def fetch_detail(self, url):
        if FakeResthavenSource.RAISE_ON_FETCH:
            raise FakeResthavenSource.RAISE_ON_FETCH
        return ObituaryDetail(
            name="Victor Barb", text="Victor Barb obituary text.", source_url=url,
        )


def _fake_extract_from_text(text, source_url=""):
    return {
        "deceased": {
            "given_names": "Victor", "surname": "Barb", "maiden_name": "", "suffix": "",
            "gender": "Male", "birth_date": "1933-05-28", "birth_place": "",
            "death_date": "2026-08-08", "death_place": "", "burial_place": "",
        },
        "relationships": {"spouses": [], "parents": [], "children": [], "siblings": []},
        "eulogy_text": "",
        "service_details": "",
        "source_url": source_url,
        "raw_text": text,
    }


@pytest.fixture(autouse=True)
def _reset_fake_source():
    FakeResthavenSource.SEARCH_RESULT = [MATCH]
    FakeResthavenSource.RAISE_ON_SEARCH = None
    FakeResthavenSource.RAISE_ON_FETCH = None
    yield
    FakeResthavenSource.SEARCH_RESULT = [MATCH]
    FakeResthavenSource.RAISE_ON_SEARCH = None
    FakeResthavenSource.RAISE_ON_FETCH = None


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "TMP_DIR", tmp_path)
    monkeypatch.setattr(app_module, "ResthavenSource", FakeResthavenSource)
    monkeypatch.setattr(app_module, "extract_from_text", _fake_extract_from_text)
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()


class TestSearchResthaven:
    def test_blank_query_shows_error(self, client):
        resp = client.post("/search/resthaven", data={"resthaven_query": "  "})
        assert resp.status_code == 200
        assert "Enter a name" in resp.get_data(as_text=True)

    def test_matches_render_results_page(self, client):
        resp = client.post("/search/resthaven", data={"resthaven_query": "Barb"})
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "Victor Barb" in body
        assert "2026-08-09" in body
        assert "/search/resthaven/extract" in body

    def test_no_matches_shows_no_results_message(self, client):
        resp = client.post("/search/resthaven", data={"resthaven_query": "nomatch"})
        assert resp.status_code == 200
        assert "No matches found" in resp.get_data(as_text=True)

    def test_search_unavailable_shows_error(self, client, monkeypatch):
        def raise_unavailable(self, query):
            return SearchUnavailable(reason="login required")

        monkeypatch.setattr(FakeResthavenSource, "search", raise_unavailable)
        resp = client.post("/search/resthaven", data={"resthaven_query": "Barb"})
        assert resp.status_code == 200
        assert "unavailable" in resp.get_data(as_text=True).lower()

    def test_source_error_shows_error(self, client):
        FakeResthavenSource.RAISE_ON_SEARCH = ResthavenSourceError("boom")
        resp = client.post("/search/resthaven", data={"resthaven_query": "Barb"})
        assert resp.status_code == 200
        assert "Resthaven search failed" in resp.get_data(as_text=True)


class TestSearchResthavenExtract:
    def test_valid_pick_redirects_to_review(self, client):
        resp = client.post("/search/resthaven/extract", data={"detail_url": MATCH.detail_url})
        assert resp.status_code == 302
        assert "/review/" in resp.headers["Location"]

    def test_rejects_url_outside_resthaven_domain(self, client):
        resp = client.post("/search/resthaven/extract", data={"detail_url": "https://evil.example.com/x"})
        assert resp.status_code == 200
        assert "Invalid obituary link" in resp.get_data(as_text=True)

    def test_fetch_error_shows_error(self, client):
        FakeResthavenSource.RAISE_ON_FETCH = ResthavenSourceError("network down")
        resp = client.post("/search/resthaven/extract", data={"detail_url": MATCH.detail_url})
        assert resp.status_code == 200
        assert "Could not fetch obituary" in resp.get_data(as_text=True)

    def test_extraction_error_shows_error(self, client, monkeypatch):
        from src.extract import ExtractionError

        def raise_extraction_error(text, source_url=""):
            raise ExtractionError("bad response")

        monkeypatch.setattr(app_module, "extract_from_text", raise_extraction_error)
        resp = client.post("/search/resthaven/extract", data={"detail_url": MATCH.detail_url})
        assert resp.status_code == 200
        assert "Extraction failed" in resp.get_data(as_text=True)

    def test_extracted_job_readable_from_review(self, client):
        resp = client.post("/search/resthaven/extract", data={"detail_url": MATCH.detail_url})
        job_url = resp.headers["Location"]
        review_resp = client.get(job_url)
        assert review_resp.status_code == 200
        assert "Barb" in review_resp.get_data(as_text=True)
