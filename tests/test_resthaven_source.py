"""tests/test_resthaven_source.py — src/sources/resthaven_source.py.

Generic CFS-platform parsing/behavior is tested once in tests/test_cfs_source.py — this
file only confirms Resthaven's own tenant config is wired correctly, that its curl_cffi
transport override wraps errors the same way the shared base class does, and, gated
behind RUN_NETWORK_TESTS=1, that the real live site still matches the shape this adapter
depends on and that curl_cffi actually gets past Cloudflare (confirmed 2026-08-10). Same
pattern as tests/test_stith_source.py, deliberately not merged with it — each site's
live-network confirmation is a distinct real-world fact, not shared logic.
"""

import os

import pytest
from curl_cffi import requests as cffi_requests
from curl_cffi.requests.exceptions import RequestException as CFFIRequestException
from src.obituary_source import AccessLevel, SearchUnavailable
from src.sources.cfs_source import CFSObituarySource, CFSSourceError
from src.sources.resthaven_source import (
    BASE_URL,
    LISTINGS_URL,
    SITEMAP_URL,
    ResthavenSource,
    ResthavenSourceError,
)


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    monkeypatch.setattr("src.sources.cfs_source.time.sleep", lambda seconds: None)


class TestResthavenConfig:
    def test_base_url(self):
        assert BASE_URL == "https://www.resthavenmort.com"

    def test_sitemap_url_uses_wtd_prefix(self):
        assert SITEMAP_URL == "https://www.resthavenmort.com/wtd/obituary_sitemap.xml"

    def test_listings_url(self):
        assert LISTINGS_URL == "https://www.resthavenmort.com/listings"

    def test_is_a_cfs_obituary_source(self):
        assert isinstance(ResthavenSource(), CFSObituarySource)

    def test_error_aliases_cfs_source_error(self):
        assert ResthavenSourceError is CFSSourceError

    def test_default_session_is_curl_cffi_not_requests(self):
        # The whole point of this adapter: Cloudflare blocks plain `requests`/`httpx`
        # here (see module docstring), so the default transport must be curl_cffi.
        source = ResthavenSource()
        assert isinstance(source._session, cffi_requests.Session)


class TestGetErrorWrapping:
    """Offline: confirms the curl_cffi-specific _get() override still raises
    CFSSourceError on failure, same contract as the shared base class's _get()."""

    def test_curl_cffi_request_exception_becomes_cfs_source_error(self):
        class _FailingSession:
            def get(self, url, timeout, headers):
                raise CFFIRequestException("boom")

        source = ResthavenSource(session=_FailingSession())
        with pytest.raises(CFSSourceError, match="Request failed"):
            source.list_recent()

    def test_http_error_from_raise_for_status_becomes_cfs_source_error(self):
        class _FakeResponse:
            def raise_for_status(self):
                raise CFFIRequestException("HTTP Error 404")

        class _FourOhFourSession:
            def get(self, url, timeout, headers):
                return _FakeResponse()

        source = ResthavenSource(session=_FourOhFourSession())
        with pytest.raises(CFSSourceError, match="Request failed"):
            source.list_recent()


# ---------------------------------------------------------------------------
# Integration test — real network call (skipped by default)
#
# curl_cffi's impersonate="chrome" was confirmed live 2026-08-10 to get a clean 200 on
# every endpoint this adapter uses, where requests/httpx got Cloudflare's 403. This is
# the test that would catch it if Cloudflare's rule or curl_cffi's fingerprint ever
# drift out of sync again.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not bool(os.getenv("RUN_NETWORK_TESTS")),
    reason="Set RUN_NETWORK_TESTS=1 to run live network tests",
)
class TestLiveResthavenSite:
    def test_check_access_is_open(self):
        source = ResthavenSource()
        assert source.check_access() == AccessLevel.OPEN

    def test_list_recent_returns_stubs(self):
        source = ResthavenSource()
        stubs = source.list_recent(page=1, page_size=5)
        assert len(stubs) == 5
        assert all(s.detail_url.startswith(BASE_URL) for s in stubs)

    def test_search_and_fetch_detail_round_trip(self):
        source = ResthavenSource()
        stubs = source.list_recent(page=1, page_size=1)
        target = stubs[0]
        results = source.search(target.name.split()[0])
        assert not isinstance(results, SearchUnavailable)
        assert any(r.detail_url == target.detail_url for r in results)

        detail = source.fetch_detail(target.detail_url)
        assert len(detail.text) > 100
