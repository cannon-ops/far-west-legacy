"""tests/test_app_search.py — POST /search and POST /search/extract.

Both routes fan out across src.sources.registry.SOURCES (Tier 2 of the Obituary
Discovery Roadmap: one search box instead of one per source, see
cannonops-vault/Projects/FWL/Obituary-Discovery-Roadmap.md). app_module.SOURCES is
monkeypatched to two fake entries so this stays offline like every other app-route test
(see tests/test_app_upload.py's FakeMatchClient pattern) — no real network call, no real
Claude API call — while still exercising the registry fan-out/merge/tag logic and the
per-source SSRF base_url guard on /search/extract.
"""

import pytest
from src import app as app_module
from src.obituary_source import ObituaryDetail, ObituaryStub, SearchUnavailable
from src.sources.registry import SourceEntry


class SourceError(Exception):
    pass


STITH_MATCH = ObituaryStub(
    name="Daniel B Hughes",
    detail_url="https://www.stithfamilyfunerals.com/obituary/DanielB-Hughes",
    date="2026-08-07",
)
RESTHAVEN_MATCH = ObituaryStub(
    name="Victor Barb",
    detail_url="https://www.resthavenmort.com/obituary/Victor-Barb",
    date="2026-08-09",
)


class FakeStithSource:
    SEARCH_RESULT = [STITH_MATCH]
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


class FakeResthavenSource:
    SEARCH_RESULT = [RESTHAVEN_MATCH]
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
            "given_names": "", "surname": "", "maiden_name": "", "suffix": "",
            "gender": "", "birth_date": "", "birth_place": "",
            "death_date": "", "death_place": "", "burial_place": "",
        },
        "relationships": {"spouses": [], "parents": [], "children": [], "siblings": []},
        "eulogy_text": "",
        "service_details": "",
        "source_url": source_url,
        "raw_text": text,
    }


FAKE_SOURCES = [
    SourceEntry(
        key="stith", label="Stith Family Funeral Home",
        base_url="https://www.stithfamilyfunerals.com",
        factory=FakeStithSource, error_cls=SourceError,
    ),
    SourceEntry(
        key="resthaven", label="Resthaven Mortuary",
        base_url="https://www.resthavenmort.com",
        factory=FakeResthavenSource, error_cls=SourceError,
    ),
]


@pytest.fixture(autouse=True)
def _reset_fake_sources():
    def reset():
        FakeStithSource.SEARCH_RESULT = [STITH_MATCH]
        FakeStithSource.RAISE_ON_SEARCH = None
        FakeStithSource.RAISE_ON_FETCH = None
        FakeResthavenSource.SEARCH_RESULT = [RESTHAVEN_MATCH]
        FakeResthavenSource.RAISE_ON_SEARCH = None
        FakeResthavenSource.RAISE_ON_FETCH = None

    reset()
    yield
    reset()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "TMP_DIR", tmp_path)
    monkeypatch.setattr(app_module, "SOURCES", FAKE_SOURCES)
    monkeypatch.setattr(app_module, "extract_from_text", _fake_extract_from_text)
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()


class TestSearch:
    def test_blank_query_shows_error(self, client):
        resp = client.post("/search", data={"query": "  "})
        assert resp.status_code == 200
        assert "Enter a name" in resp.get_data(as_text=True)

    def test_merges_and_tags_results_from_every_source(self, client):
        resp = client.post("/search", data={"query": "anything"})
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "Daniel B Hughes" in body
        assert "Victor Barb" in body
        assert "Stith Family Funeral Home" in body
        assert "Resthaven Mortuary" in body
        assert "/search/extract" in body

    def test_no_matches_from_any_source_shows_no_results_message(self, client):
        resp = client.post("/search", data={"query": "nomatch"})
        assert resp.status_code == 200
        assert "No matches found" in resp.get_data(as_text=True)

    def test_one_source_unavailable_does_not_block_the_other(self, client, monkeypatch):
        def unavailable(self, query):
            return SearchUnavailable(reason="login required")

        monkeypatch.setattr(FakeStithSource, "search", unavailable)
        resp = client.post("/search", data={"query": "anything"})
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "unavailable" in body.lower()
        assert "Victor Barb" in body

    def test_one_source_error_does_not_block_the_other(self, client):
        FakeStithSource.RAISE_ON_SEARCH = SourceError("boom")
        resp = client.post("/search", data={"query": "anything"})
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "Stith Family Funeral Home search failed" in body
        assert "Victor Barb" in body


class TestSearchExtract:
    def test_valid_pick_redirects_to_review(self, client):
        resp = client.post(
            "/search/extract", data={"source": "stith", "detail_url": STITH_MATCH.detail_url},
        )
        assert resp.status_code == 302
        assert "/review/" in resp.headers["Location"]

    def test_valid_pick_from_second_source_redirects_to_review(self, client):
        resp = client.post(
            "/search/extract", data={"source": "resthaven", "detail_url": RESTHAVEN_MATCH.detail_url},
        )
        assert resp.status_code == 302
        assert "/review/" in resp.headers["Location"]

    def test_rejects_unknown_source_key(self, client):
        resp = client.post(
            "/search/extract", data={"source": "nope", "detail_url": STITH_MATCH.detail_url},
        )
        assert resp.status_code == 200
        assert "Invalid obituary link" in resp.get_data(as_text=True)

    def test_rejects_url_that_does_not_match_the_named_source(self, client):
        # A Resthaven URL submitted with source=stith must not validate against Stith's
        # own base_url, even though Resthaven itself is a registered source.
        resp = client.post(
            "/search/extract", data={"source": "stith", "detail_url": RESTHAVEN_MATCH.detail_url},
        )
        assert resp.status_code == 200
        assert "Invalid obituary link" in resp.get_data(as_text=True)

    def test_rejects_url_outside_any_registered_domain(self, client):
        resp = client.post(
            "/search/extract", data={"source": "stith", "detail_url": "https://evil.example.com/x"},
        )
        assert resp.status_code == 200
        assert "Invalid obituary link" in resp.get_data(as_text=True)

    def test_fetch_error_shows_error(self, client):
        FakeStithSource.RAISE_ON_FETCH = SourceError("network down")
        resp = client.post(
            "/search/extract", data={"source": "stith", "detail_url": STITH_MATCH.detail_url},
        )
        assert resp.status_code == 200
        assert "Could not fetch obituary" in resp.get_data(as_text=True)

    def test_extraction_error_shows_error(self, client, monkeypatch):
        from src.extract import ExtractionError

        def raise_extraction_error(text, source_url=""):
            raise ExtractionError("bad response")

        monkeypatch.setattr(app_module, "extract_from_text", raise_extraction_error)
        resp = client.post(
            "/search/extract", data={"source": "stith", "detail_url": STITH_MATCH.detail_url},
        )
        assert resp.status_code == 200
        assert "Extraction failed" in resp.get_data(as_text=True)

    def test_extracted_job_readable_from_review(self, client):
        resp = client.post(
            "/search/extract", data={"source": "stith", "detail_url": STITH_MATCH.detail_url},
        )
        job_url = resp.headers["Location"]
        review_resp = client.get(job_url)
        assert review_resp.status_code == 200
        assert "Hughes" in review_resp.get_data(as_text=True)
