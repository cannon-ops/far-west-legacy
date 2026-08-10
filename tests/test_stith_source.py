"""tests/test_stith_source.py — src/sources/stith_source.py.

Unit tests parse small fixtures directly (no network), matching tests/test_fetch.py's
pattern. A network-gated integration class at the bottom hits the real site, same
RUN_NETWORK_TESTS convention as TestFetchRealPage in tests/test_fetch.py.
"""

import os

import pytest
import requests
from src.obituary_source import AccessLevel, ObituaryStub, SearchUnavailable
from src.sources.stith_source import (
    LISTINGS_URL,
    SITEMAP_URL,
    StithSource,
    StithSourceError,
    _display_name_from_slug,
    _parse_detail,
    _parse_sitemap,
)

SITEMAP_XML = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url>
<loc>https://www.stithfamilyfunerals.com/obituary/DanielB-Hughes</loc>
<priority>1</priority>
<changefreq>monthly</changefreq>
<lastmod>2026-08-07</lastmod>
</url>
<url>
<loc>https://www.stithfamilyfunerals.com/obituary/JamesJimMichael-Ranes</loc>
<priority>1</priority>
<changefreq>monthly</changefreq>
<lastmod>2026-08-03</lastmod>
</url>
<url>
<loc>https://www.stithfamilyfunerals.com/obituary/LisaLeeLottman-Wilson</loc>
<priority>1</priority>
<changefreq>monthly</changefreq>
<lastmod>2026-07-31</lastmod>
</url>
</urlset>
"""

DETAIL_HTML = """
<html><body>
<div id="obtext" style="padding:10px">
  <h2 style="text-transform: none !important">Daniel B. Hughes Obituary</h2>
  <div class="obit-text-container">
    <p><b>Daniel B. Hughes - age 81 of Jameson, Missouri passed away Friday morning,
    August 7, 2026, at his home.</b></p>
    <p><b>He was preceded in death by his parents.</b></p>
  </div>
</div>
<h2>Services</h2>
</body></html>
"""

ROBOTS_OPEN = """User-agent: *
Disallow: /pax/
Sitemap: https://www.stithfamilyfunerals.com/sth/sitemap.xml
Sitemap: https://www.stithfamilyfunerals.com/sth/obituary_sitemap.xml
"""


class _FakeResponse:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    monkeypatch.setattr("src.sources.stith_source.time.sleep", lambda seconds: None)


def _fake_session(url_to_text: dict) -> requests.Session:
    session = requests.Session()

    def fake_get(url, timeout, headers):
        if url not in url_to_text:
            raise AssertionError(f"unexpected URL fetched: {url}")
        return _FakeResponse(url_to_text[url])

    session.get = fake_get
    return session


# ---------------------------------------------------------------------------
# Parsing helpers — no network
# ---------------------------------------------------------------------------


class TestDisplayNameFromSlug:
    def test_simple_given_middle_initial(self):
        assert _display_name_from_slug("DanielB-Hughes") == "Daniel B Hughes"

    def test_multiple_given_names(self):
        assert _display_name_from_slug("JamesJimMichael-Ranes") == "James Jim Michael Ranes"

    def test_suffix_attached_to_surname(self):
        assert _display_name_from_slug("RobertBobDale-HallSr") == "Robert Bob Dale Hall Sr"

    def test_extra_hyphenated_segment_ignored_if_numeric(self):
        assert _display_name_from_slug("AmeliaJean-DianeDecker-1") == "Amelia Jean Diane Decker"

    def test_initials_only(self):
        assert _display_name_from_slug("AJ-Houston") == "A J Houston"


class TestParseSitemap:
    def test_returns_all_entries(self):
        stubs = _parse_sitemap(SITEMAP_XML)
        assert len(stubs) == 3
        assert all(isinstance(s, ObituaryStub) for s in stubs)

    def test_fields_populated(self):
        stubs = _parse_sitemap(SITEMAP_XML)
        first = stubs[0]
        assert first.name == "Daniel B Hughes"
        assert first.detail_url == "https://www.stithfamilyfunerals.com/obituary/DanielB-Hughes"
        assert first.date == "2026-08-07"


class TestParseDetail:
    def test_extracts_name_without_obituary_suffix(self):
        detail = _parse_detail(DETAIL_HTML, "https://www.stithfamilyfunerals.com/obituary/DanielB-Hughes")
        assert detail.name == "Daniel B. Hughes"

    def test_extracts_full_text(self):
        detail = _parse_detail(DETAIL_HTML, "https://www.stithfamilyfunerals.com/obituary/DanielB-Hughes")
        assert "Jameson, Missouri" in detail.text
        assert "preceded in death" in detail.text

    def test_text_has_no_html_tags(self):
        detail = _parse_detail(DETAIL_HTML, "https://www.stithfamilyfunerals.com/obituary/DanielB-Hughes")
        assert "<" not in detail.text and ">" not in detail.text

    def test_ignores_services_h2_outside_obtext(self):
        detail = _parse_detail(DETAIL_HTML, "https://www.stithfamilyfunerals.com/obituary/DanielB-Hughes")
        assert detail.name != "Services"

    def test_missing_container_raises(self):
        with pytest.raises(StithSourceError, match="Could not locate"):
            _parse_detail("<html><body>no obituary here</body></html>", "https://example.com/x")


# ---------------------------------------------------------------------------
# StithSource — fake HTTP session, no network
# ---------------------------------------------------------------------------


class TestCheckAccess:
    def test_open_when_robots_permits_listings_and_sitemap(self):
        source = StithSource(session=_fake_session({
            "https://www.stithfamilyfunerals.com/robots.txt": ROBOTS_OPEN,
        }))
        assert source.check_access() == AccessLevel.OPEN

    def test_robots_restricted_when_disallowed(self):
        restrictive = "User-agent: *\nDisallow: /\n"
        source = StithSource(session=_fake_session({
            "https://www.stithfamilyfunerals.com/robots.txt": restrictive,
        }))
        assert source.check_access() == AccessLevel.ROBOTS_RESTRICTED


class TestListRecent:
    def test_returns_newest_first(self):
        source = StithSource(session=_fake_session({SITEMAP_URL: SITEMAP_XML}))
        stubs = source.list_recent(page=1)
        assert [s.date for s in stubs] == ["2026-08-07", "2026-08-03", "2026-07-31"]

    def test_pagination_slices(self):
        source = StithSource(session=_fake_session({SITEMAP_URL: SITEMAP_XML}))
        page1 = source.list_recent(page=1, page_size=2)
        page2 = source.list_recent(page=2, page_size=2)
        assert len(page1) == 2
        assert len(page2) == 1
        assert page1[0].detail_url != page2[0].detail_url

    def test_sitemap_fetched_only_once_across_calls(self):
        calls = []
        session = requests.Session()

        def fake_get(url, timeout, headers):
            calls.append(url)
            return _FakeResponse(SITEMAP_XML)

        session.get = fake_get
        source = StithSource(session=session)
        source.list_recent(page=1)
        source.list_recent(page=2)
        assert calls == [SITEMAP_URL]


class TestSearch:
    def test_matches_case_insensitive_substring(self):
        source = StithSource(session=_fake_session({SITEMAP_URL: SITEMAP_XML}))
        results = source.search("hughes")
        assert len(results) == 1
        assert results[0].name == "Daniel B Hughes"

    def test_no_match_returns_empty_list_not_unavailable(self):
        source = StithSource(session=_fake_session({SITEMAP_URL: SITEMAP_XML}))
        results = source.search("Nonexistent Name Zzz")
        assert results == []

    def test_blank_query_returns_empty_list(self):
        source = StithSource(session=_fake_session({SITEMAP_URL: SITEMAP_XML}))
        assert source.search("   ") == []

    def test_never_returns_search_unavailable(self):
        # Stith's search is real (local filtering over the sitemap), unlike a
        # login-gated source — SearchUnavailable should never come back here.
        source = StithSource(session=_fake_session({SITEMAP_URL: SITEMAP_XML}))
        assert not isinstance(source.search("hughes"), SearchUnavailable)


class TestFetchDetail:
    def test_returns_obituary_detail(self):
        url = "https://www.stithfamilyfunerals.com/obituary/DanielB-Hughes"
        source = StithSource(session=_fake_session({url: DETAIL_HTML}))
        detail = source.fetch_detail(url)
        assert detail.name == "Daniel B. Hughes"
        assert detail.source_url == url
        assert "Jameson, Missouri" in detail.text


class TestRequestFailure:
    def test_network_error_raises_stith_source_error(self):
        session = requests.Session()

        def fake_get(url, timeout, headers):
            raise requests.ConnectionError("boom")

        session.get = fake_get
        source = StithSource(session=session)
        with pytest.raises(StithSourceError, match="Request failed"):
            source.list_recent()


# ---------------------------------------------------------------------------
# Integration test — real network call (skipped by default)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not bool(os.getenv("RUN_NETWORK_TESTS")),
    reason="Set RUN_NETWORK_TESTS=1 to run live network tests",
)
class TestLiveStithSite:
    def test_check_access_is_open(self):
        source = StithSource()
        assert source.check_access() == AccessLevel.OPEN

    def test_list_recent_returns_stubs(self):
        source = StithSource()
        stubs = source.list_recent(page=1, page_size=5)
        assert len(stubs) == 5
        assert all(s.detail_url.startswith(LISTINGS_URL.rsplit("/listings", 1)[0]) for s in stubs)

    def test_search_and_fetch_detail_round_trip(self):
        source = StithSource()
        stubs = source.list_recent(page=1, page_size=1)
        target = stubs[0]
        results = source.search(target.name.split()[0])
        assert any(r.detail_url == target.detail_url for r in results)

        detail = source.fetch_detail(target.detail_url)
        assert len(detail.text) > 100
