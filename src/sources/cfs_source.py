"""sources/cfs_source.py — Generic ObituarySource for funeral-home sites built on the
Consolidated Funeral Services (CFS) / TributeArchive platform.

Two independent CFS sites were checked live against real markup, not assumed from one
and applied to the other: Stith Family Funeral Home (2026-08-09) and Resthaven Mortuary
/ Slater Neal Funeral Home (2026-08-10). Both match byte-for-byte on everything this
module depends on:

- `/obituary/<slug>` detail pages: `<div id="obtext">` containing `<h2>{Name}
  Obituary</h2>` followed by a `<h2>Services</h2>` sibling, and `div.obit-text-container`
  holding the full obituary body.
- `/listings`: real pagination and the real search form both call the same inline JS
  function, `doObitPage()`, which `POST`s to `/pax/obitsrch` — and `robots.txt` on both
  sites disallows exactly that path (`Disallow: /pax/`). So this adapter never calls it.
- A `<subdir>/obituary_sitemap.xml` sitemap, listed in `robots.txt` as an explicit
  `Sitemap:` entry with no access restriction, giving every obituary URL + `lastmod`.
  `list_recent()` and `search()` are built from this instead of the disallowed endpoint —
  `search()` returns real results (local filtering over a slug-derived display name), not
  `SearchUnavailable`, because a working non-`/pax/` search exists.
- Neither site's `robots.txt` carries a `Crawl-delay` directive. `REQUEST_DELAY_SECONDS`
  below is a self-imposed courtesy delay, not directive-derived.

Only the domain and the sitemap's per-tenant subdirectory prefix (`/sth/` for Stith,
`/wtd/` for Resthaven) vary between tenants — everything else is identical, which is why
this is one parameterized class rather than two near-duplicate adapters. A third CFS
tenant that turns out to deviate from this shape should not be force-fit here; report the
difference instead (see `Obituary-Source-Candidates.md`).
"""

import re
import time
import urllib.robotparser
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup

from src.fetch import _clean_whitespace
from src.obituary_source import AccessLevel, ObituaryDetail, ObituaryStub, SearchUnavailable

USER_AGENT = "far-west-legacy/0.1"

# Self-imposed courtesy delay between real HTTP requests — see module docstring.
REQUEST_DELAY_SECONDS = 1.0

_SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
_CAMEL_SPLIT_RE = re.compile(r"(?<!^)(?=[A-Z])")


class CFSSourceError(Exception):
    """Raised on HTTP errors, parse failures, or a robots.txt access check failing."""


class CFSObituarySource:
    """ObituarySource for a CFS/TributeArchive funeral-home site. See module docstring
    for why this does not use the site's own pagination/search endpoint."""

    def __init__(self, base_url: str, sitemap_path: str, session: requests.Session | None = None):
        self.base_url = base_url.rstrip("/")
        self.robots_url = f"{self.base_url}/robots.txt"
        self.listings_url = f"{self.base_url}/listings"
        self.sitemap_url = f"{self.base_url}{sitemap_path}"
        self._session = session or requests.Session()
        self._sitemap_cache: list[ObituaryStub] | None = None
        self._last_request_at: float | None = None

    def check_access(self) -> AccessLevel:
        """Real per-source probe (module docstring's AccessLevel note): confirms the
        operations this adapter actually uses (listings, sitemap) are robots-permitted.
        Does not claim anything about /pax/, which this adapter never calls."""
        resp = self._get(self.robots_url)
        rp = urllib.robotparser.RobotFileParser()
        rp.parse(resp.text.splitlines())
        listings_ok = rp.can_fetch(USER_AGENT, self.listings_url)
        sitemap_ok = rp.can_fetch(USER_AGENT, self.sitemap_url)
        return AccessLevel.OPEN if (listings_ok and sitemap_ok) else AccessLevel.ROBOTS_RESTRICTED

    def list_recent(self, page: int = 1, page_size: int = 20) -> list[ObituaryStub]:
        """Newest-first slice of the sitemap (already sorted by lastmod at load time),
        not a call to the site's own /pax/-backed "Next" button."""
        stubs = self._load_sitemap()
        start = (page - 1) * page_size
        return stubs[start : start + page_size]

    def search(self, query: str) -> list[ObituaryStub] | SearchUnavailable:
        """Local case-insensitive substring match against sitemap-derived display names.
        Real results, not SearchUnavailable — this site's own search endpoint is
        off-limits (module docstring), but the sitemap gives enough to search without it."""
        query = query.strip().lower()
        if not query:
            return []
        stubs = self._load_sitemap()
        return [stub for stub in stubs if query in stub.name.lower()]

    def fetch_detail(self, url: str) -> ObituaryDetail:
        resp = self._get(url)
        return _parse_detail(resp.text, url)

    def _load_sitemap(self) -> list[ObituaryStub]:
        if self._sitemap_cache is not None:
            return self._sitemap_cache
        resp = self._get(self.sitemap_url)
        stubs = _parse_sitemap(resp.text)
        stubs.sort(key=lambda stub: stub.date or "", reverse=True)
        self._sitemap_cache = stubs
        return stubs

    def _get(self, url: str) -> requests.Response:
        self._throttle()
        try:
            resp = self._session.get(url, timeout=15, headers={"User-Agent": USER_AGENT})
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise CFSSourceError(f"Request failed for {url}: {exc}") from exc
        self._last_request_at = time.monotonic()
        return resp

    def _throttle(self) -> None:
        if self._last_request_at is None:
            return
        remaining = REQUEST_DELAY_SECONDS - (time.monotonic() - self._last_request_at)
        if remaining > 0:
            time.sleep(remaining)


def _parse_sitemap(xml_text: str) -> list[ObituaryStub]:
    root = ET.fromstring(xml_text)
    stubs = []
    for url_el in root.findall("sm:url", _SITEMAP_NS):
        loc_el = url_el.find("sm:loc", _SITEMAP_NS)
        if loc_el is None or not loc_el.text:
            continue
        loc = loc_el.text.strip()
        lastmod_el = url_el.find("sm:lastmod", _SITEMAP_NS)
        lastmod = lastmod_el.text.strip() if lastmod_el is not None and lastmod_el.text else None
        slug = loc.rstrip("/").rsplit("/", 1)[-1]
        stubs.append(ObituaryStub(name=_display_name_from_slug(slug), detail_url=loc, date=lastmod))
    return stubs


def _display_name_from_slug(slug: str) -> str:
    """Best-effort only, for search matching and listing display. Slugs are
    <CamelCaseGivenMiddle>-<CamelCaseSurname[Suffix]>, e.g. "DanielB-Hughes" ->
    "Daniel B Hughes", "RobertBobDale-HallSr" -> "Robert Bob Dale Hall Sr". A handful of
    slugs carry an extra hyphenated segment (maiden/married surname pairs, or a "-1"
    disambiguator on a duplicate slug) — non-numeric segments after the first hyphen are
    each CamelCase-split and appended. fetch_detail() replaces this guess with the
    obituary page's own title, which is always more accurate."""
    given_part, _, rest = slug.partition("-")
    surname_parts = [part for part in rest.split("-") if part and not part.isdigit()]
    parts = [given_part] + surname_parts
    words = [_CAMEL_SPLIT_RE.sub(" ", part) for part in parts]
    return " ".join(words).strip()


def _parse_detail(html: str, source_url: str) -> ObituaryDetail:
    soup = BeautifulSoup(html, "lxml")

    container = soup.find("div", class_="obit-text-container")
    if container is None:
        raise CFSSourceError(f"Could not locate obituary text on page: {source_url}")
    text = _clean_whitespace(container.get_text(separator=" "))
    if not text:
        raise CFSSourceError(f"Extracted text is empty for URL: {source_url}")

    obtext = soup.find("div", id="obtext")
    title_el = obtext.find("h2") if obtext else None
    if title_el is not None:
        name = re.sub(r"\s*Obituary\s*$", "", title_el.get_text()).strip()
    else:
        slug = source_url.rstrip("/").rsplit("/", 1)[-1]
        name = _display_name_from_slug(slug)

    return ObituaryDetail(name=name, text=text, source_url=source_url)
