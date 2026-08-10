"""sources/stith_source.py — ObituarySource adapter for Stith Family Funeral Home
(stithfamilyfunerals.com), a Consolidated Funeral Services (CFS) site.

Real pagination and the real search form both `POST /pax/obitsrch` (confirmed 2026-08-09
by reading the listings page's inline JS: the "Next" button and the search form both call
`doObitPage()`, which posts there) — and `robots.txt` disallows exactly that path
(`Disallow: /pax/`). So this adapter never calls it. `list_recent()` and `search()` are
built instead from `sth/obituary_sitemap.xml`, which `robots.txt` explicitly lists as a
`Sitemap:` entry (500 URLs with `lastmod` dates) and which carries no access restriction
at all. Detail pages (`/obituary/<slug>`) are fetched directly — not under `/pax/`, no
login wall, full text confirmed present in `div.obit-text-container`.

Correction to the brief this adapter was built against: `robots.txt` as fetched live
2026-08-09 carries no `Crawl-delay` directive for this site (only `Disallow: /pax/` plus
two `Sitemap:` lines) — the brief's premise that one exists does not hold, checked
directly rather than assumed. `REQUEST_DELAY_SECONDS` below is a self-imposed courtesy
delay, not a directive-derived value.

Search is real (matches are returned, not SearchUnavailable) but is local filtering over
the sitemap's slugs rather than the site's own AJAX search — a display name is derived
from each slug (CamelCase-split) for matching and listing; fetch_detail() re-derives the
authoritative name from the obituary page's own title, which is always more accurate than
the slug guess.
"""

import re
import time
import urllib.robotparser
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup

from src.fetch import _clean_whitespace
from src.obituary_source import AccessLevel, ObituaryDetail, ObituaryStub, SearchUnavailable

BASE_URL = "https://www.stithfamilyfunerals.com"
ROBOTS_URL = f"{BASE_URL}/robots.txt"
LISTINGS_URL = f"{BASE_URL}/listings"
SITEMAP_URL = f"{BASE_URL}/sth/obituary_sitemap.xml"

USER_AGENT = "far-west-legacy/0.1"

# Self-imposed courtesy delay between real HTTP requests — see module docstring.
REQUEST_DELAY_SECONDS = 1.0

_SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
_CAMEL_SPLIT_RE = re.compile(r"(?<!^)(?=[A-Z])")


class StithSourceError(Exception):
    """Raised on HTTP errors, parse failures, or a robots.txt access check failing."""


class StithSource:
    """ObituarySource for stithfamilyfunerals.com. See module docstring for why this
    does not use the site's own pagination/search endpoint."""

    def __init__(self, session: requests.Session | None = None):
        self._session = session or requests.Session()
        self._sitemap_cache: list[ObituaryStub] | None = None
        self._last_request_at: float | None = None

    def check_access(self) -> AccessLevel:
        """Real per-source probe (module docstring's AccessLevel note): confirms the
        operations this adapter actually uses (listings, sitemap) are robots-permitted.
        Does not claim anything about /pax/, which this adapter never calls."""
        resp = self._get(ROBOTS_URL)
        rp = urllib.robotparser.RobotFileParser()
        rp.parse(resp.text.splitlines())
        if rp.can_fetch(USER_AGENT, LISTINGS_URL) and rp.can_fetch(USER_AGENT, SITEMAP_URL):
            return AccessLevel.OPEN
        return AccessLevel.ROBOTS_RESTRICTED

    def list_recent(self, page: int = 1, page_size: int = 20) -> list[ObituaryStub]:
        """Newest-first slice of the sitemap (already sorted by lastmod at load time),
        not a call to the site's own /pax/-backed "Next" button."""
        stubs = self._load_sitemap()
        start = (page - 1) * page_size
        return stubs[start : start + page_size]

    def search(self, query: str) -> list[ObituaryStub] | SearchUnavailable:
        """Local case-insensitive substring match against sitemap-derived display names.
        Real results, not SearchUnavailable — Stith's own search endpoint is off-limits
        (module docstring), but the sitemap gives enough to search without it."""
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
        resp = self._get(SITEMAP_URL)
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
            raise StithSourceError(f"Request failed for {url}: {exc}") from exc
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
    slugs (8 of 500 checked 2026-08-09) carry an extra hyphenated segment (maiden/married
    surname pairs, or a "-1" disambiguator on a duplicate slug) — non-numeric segments
    after the first hyphen are each CamelCase-split and appended. fetch_detail() replaces
    this guess with the obituary page's own title, which is always more accurate."""
    given_part, _, rest = slug.partition("-")
    surname_parts = [part for part in rest.split("-") if part and not part.isdigit()]
    parts = [given_part] + surname_parts
    words = [_CAMEL_SPLIT_RE.sub(" ", part) for part in parts]
    return " ".join(words).strip()


def _parse_detail(html: str, source_url: str) -> ObituaryDetail:
    soup = BeautifulSoup(html, "lxml")

    container = soup.find("div", class_="obit-text-container")
    if container is None:
        raise StithSourceError(f"Could not locate obituary text on page: {source_url}")
    text = _clean_whitespace(container.get_text(separator=" "))
    if not text:
        raise StithSourceError(f"Extracted text is empty for URL: {source_url}")

    obtext = soup.find("div", id="obtext")
    title_el = obtext.find("h2") if obtext else None
    if title_el is not None:
        name = re.sub(r"\s*Obituary\s*$", "", title_el.get_text()).strip()
    else:
        slug = source_url.rstrip("/").rsplit("/", 1)[-1]
        name = _display_name_from_slug(slug)

    return ObituaryDetail(name=name, text=text, source_url=source_url)
