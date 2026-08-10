"""obituary_source.py — Pluggable interface for name-searchable obituary sources.

Shape confirmed against two real sources during recon (2026-08-08, see
cannonops-vault/Handoff-Status/2026-08-08-FWL-011-H1-TriCounty-Recon.md), then adjusted
once more against the real Stith adapter (2026-08-09, see
cannonops-vault/Handoff-Status/2026-08-09-FWL-011-H2-Merge-and-Stith-Adapter.md): access
level, pagination, and search availability vary per source and must never be assumed
from a site's surface appearance or from how the recon read it before real code was
written against it.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class AccessLevel(Enum):
    """Result of check_access() — a real per-source probe, not a config flag. A source
    can be OPEN for some operations and ROBOTS_RESTRICTED for others (the Stith adapter
    is exactly this case: browsing and detail fetch are open, but the site's own
    pagination/search endpoint is robots-disallowed — see StithSource's module docstring).
    ObituarySource implementations report the level that applies to what this interface
    actually calls, not to the site as a whole."""

    OPEN = "open"
    ROBOTS_RESTRICTED = "robots_restricted"
    LOGIN_REQUIRED = "login_required"


@dataclass
class ObituaryStub:
    """One entry in a listing or search result. `date` is source-specific — a lastmod
    or posted date, not necessarily the death date, which lives in the free text on the
    detail page and is Claude's job (via extract.py) to pull out, not this layer's."""

    name: str
    detail_url: str
    date: str | None = None
    snippet: str | None = None


@dataclass
class ObituaryDetail:
    """Full text of one obituary, already in the same shape fetch.py hands to
    extract_from_text(): clean plain text, no HTML tags."""

    name: str
    text: str
    source_url: str


class SearchUnavailable:
    """Sentinel returned by search() instead of an exception when a source's real
    search mechanism is gated (login, robots.txt, or simply doesn't exist) and no
    equivalent can be built from what the source's own crawl policy actually permits.
    The fallback (list_recent + local filtering) is a first-class outcome to design
    for, not an error path each call site has to improvise around."""

    def __init__(self, reason: str):
        self.reason = reason


class ObituarySource(Protocol):
    def check_access(self) -> AccessLevel:
        """Probe robots.txt and an unauthenticated fetch. Never assume from the site's
        surface appearance — see AccessLevel's docstring for why this can't be a single
        boolean."""

    def list_recent(self, page: int = 1) -> list[ObituaryStub]:
        """Paginated listing. Source-specific pagination mechanics are hidden behind
        this call — they do not have to be the source's own pagination mechanism if
        that mechanism turns out to be off-limits (see StithSource)."""

    def fetch_detail(self, url: str) -> ObituaryDetail:
        """Full text for one obituary at a detail_url returned by list_recent/search."""

    def search(self, query: str) -> list[ObituaryStub] | SearchUnavailable:
        """Name search. Returns SearchUnavailable when the source has no way to search
        that respects its own access rules — never a login flow, never a bypass."""
