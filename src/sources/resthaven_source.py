"""sources/resthaven_source.py — CFS adapter for Resthaven Mortuary / Slater Neal
Funeral Home (resthavenmort.com, Trenton).

Thin per-tenant config over `cfs_source.CFSObituarySource` — confirmed live 2026-08-10
to be the same CFS/TributeArchive platform as Stith Family Funeral Home, byte-for-byte
on markup, JS, and robots.txt shape (only the domain and the sitemap subdirectory prefix
differ — `/wtd/` here vs. Stith's `/sth/`).

**Transport is `curl_cffi`, not `requests`, and only for this tenant.** Every real
Resthaven endpoint 403s both `requests` and `httpx` behind Cloudflare's "Attention
Required!" challenge — confirmed 2026-08-10 to be a TLS/HTTP-client-fingerprint block,
not headers/UA (both were tried with full browser-like values and still 403'd), not
rate-limiting (reproducible across multiple attempts over several minutes). `curl` with
the identical User-Agent passes every time, which is what pointed at the fingerprint
itself as the trigger. `curl_cffi.requests.Session(impersonate="chrome")` presents a real
Chrome TLS fingerprint and gets a clean 200 on every endpoint this adapter uses —
confirmed live 2026-08-10, not assumed from the library's README. Full original finding:
`cannonops-vault/Handoff-Status/2026-08-10-FWL-011-H3-Stith-UI-Resthaven-Render.md`.

`_get()` is overridden here rather than in the shared `cfs_source.py` base class,
specifically so `StithSource` and any other future CFS tenant keep using plain
`requests` unchanged — curl_cffi's exception hierarchy does not subclass `requests`'s
(`curl_cffi.requests.exceptions.RequestException` inherits from `curl_cffi.curl.CurlError`
/ `OSError`, not `requests.RequestException`), so the parent class's except clause would
not have caught it. Everything else (sitemap parsing, detail parsing, search, pagination,
throttling) is inherited unchanged from `CFSObituarySource`.
"""

import time

from curl_cffi import requests as cffi_requests
from curl_cffi.requests.exceptions import RequestException as CFFIRequestException

from src.sources.cfs_source import USER_AGENT, CFSObituarySource, CFSSourceError

BASE_URL = "https://www.resthavenmort.com"
SITEMAP_PATH = "/wtd/obituary_sitemap.xml"
LISTINGS_URL = f"{BASE_URL}/listings"
SITEMAP_URL = f"{BASE_URL}{SITEMAP_PATH}"

ResthavenSourceError = CFSSourceError


class ResthavenSource(CFSObituarySource):
    def __init__(self, session: cffi_requests.Session | None = None):
        session = session or cffi_requests.Session(impersonate="chrome")
        super().__init__(base_url=BASE_URL, sitemap_path=SITEMAP_PATH, session=session)

    def _get(self, url: str) -> cffi_requests.Response:
        self._throttle()
        try:
            resp = self._session.get(url, timeout=15, headers={"User-Agent": USER_AGENT})
            resp.raise_for_status()
        except CFFIRequestException as exc:
            raise CFSSourceError(f"Request failed for {url}: {exc}") from exc
        self._last_request_at = time.monotonic()
        return resp
