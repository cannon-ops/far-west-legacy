"""sources/registry.py — Registry of active ObituarySource adapters for unified name search.

Tier 2 of the Obituary Discovery Roadmap (cannonops-vault/Projects/FWL/Obituary-Discovery-
Roadmap.md): one search box that fans out across every registered source instead of one
button per source. `POST /search` and `POST /search/extract` in app.py iterate SOURCES —
neither route is written against a specific source by name.

To add a source: give it a unique `key` (used in forms/routing, never shown to the user), a
`label` (shown next to each of its results), its `base_url` (used to validate a detail_url
belongs to that source before fetching — the SSRF guard), a zero-arg `factory` that returns
a fresh ObituarySource instance, and the exception type its search()/fetch_detail() raise.
Append one SourceEntry below. Nothing in app.py changes.
"""

from dataclasses import dataclass
from typing import Callable

from src.obituary_source import ObituarySource
from src.sources.resthaven_source import BASE_URL as RESTHAVEN_BASE_URL
from src.sources.resthaven_source import ResthavenSource, ResthavenSourceError
from src.sources.stith_source import BASE_URL as STITH_BASE_URL
from src.sources.stith_source import StithSource, StithSourceError


@dataclass(frozen=True)
class SourceEntry:
    key: str
    label: str
    base_url: str
    factory: Callable[[], ObituarySource]
    error_cls: type[Exception]


SOURCES: list[SourceEntry] = [
    SourceEntry(
        key="stith",
        label="Stith Family Funeral Home",
        base_url=STITH_BASE_URL,
        factory=StithSource,
        error_cls=StithSourceError,
    ),
    SourceEntry(
        key="resthaven",
        label="Resthaven Mortuary",
        base_url=RESTHAVEN_BASE_URL,
        factory=ResthavenSource,
        error_cls=ResthavenSourceError,
    ),
]
