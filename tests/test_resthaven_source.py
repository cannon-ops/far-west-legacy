"""tests/test_resthaven_source.py — src/sources/resthaven_source.py.

Generic CFS-platform parsing/behavior is tested once in tests/test_cfs_source.py — this
file only confirms Resthaven's own tenant config is wired correctly and, gated behind
RUN_NETWORK_TESTS=1, that the real live site still matches the shape this adapter
depends on. Same pattern as tests/test_stith_source.py, deliberately not merged with it —
each site's live-network confirmation is a distinct real-world fact, not shared logic.
"""

import os

import pytest
from src.obituary_source import AccessLevel, SearchUnavailable
from src.sources.cfs_source import CFSObituarySource, CFSSourceError
from src.sources.resthaven_source import (
    BASE_URL,
    LISTINGS_URL,
    SITEMAP_URL,
    ResthavenSource,
    ResthavenSourceError,
)


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


# ---------------------------------------------------------------------------
# Integration test — real network call (skipped by default)
#
# Expected to fail even when run: Cloudflare 403s every real endpoint for both
# `requests` and `httpx` while `curl` with the identical UA succeeds (see
# resthaven_source.py's module docstring for the full finding, confirmed
# reproducible 2026-08-10). xfail rather than skip/delete, so this stays visible
# and self-documenting instead of silently vanishing if RUN_NETWORK_TESTS=1 runs.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not bool(os.getenv("RUN_NETWORK_TESTS")),
    reason="Set RUN_NETWORK_TESTS=1 to run live network tests",
)
@pytest.mark.xfail(
    reason="Cloudflare 403s requests/httpx here, curl succeeds — see module docstring",
    strict=False,
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
