"""sources/resthaven_source.py — CFS adapter for Resthaven Mortuary / Slater Neal
Funeral Home (resthavenmort.com, Trenton).

Thin per-tenant config over `cfs_source.CFSObituarySource` — confirmed live 2026-08-10
to be the same CFS/TributeArchive platform as Stith Family Funeral Home, byte-for-byte
on markup, JS, and robots.txt shape (only the domain and the sitemap subdirectory prefix
differ — `/wtd/` here vs. Stith's `/sth/`).

**NOT currently usable from this app — do not wire this into app.py or cli.py.**
Every real endpoint (`robots.txt`, `/listings`, the sitemap, `/obituary/<slug>` detail
pages) returns Cloudflare's "Attention Required!" 403 challenge page to both `requests`
and `httpx` — the two HTTP clients already used elsewhere in this codebase — even with
full browser-like headers. `curl` with the identical User-Agent succeeds on every one of
those same URLs, every time, from the same machine and network. Headers and UA are ruled
out as the cause (both attempts used the same values in both clients); what's left is a
Cloudflare bot-management rule keyed off something curl's TLS/HTTP client fingerprint
doesn't trip and Python's networking stack does (`requests` and `httpx` share the same
underlying `ssl`/`urllib3`-style stack; curl's is a different implementation). Confirmed
reproducible across multiple attempts over several minutes, not a transient rate limit.

Fixing this would mean a Python HTTP client with a browser-like TLS fingerprint (e.g.
`curl_cffi`) or shelling out to the system `curl` — both are real architecture changes
(a new dependency, or a subprocess boundary) that this session's scope didn't call for
and didn't add unilaterally. Left for a deliberate decision, not guessed at here. See
`cannonops-vault/Handoff-Status/2026-08-10-FWL-011-H3-Stith-UI-Resthaven-Render.md`.

The generic CFS adapter itself (this file's parent class, `cfs_source.py`) is unaffected
and is why this class exists at all: if the transport problem is ever solved, the
markup-parsing side needs zero changes.
"""

import requests

from src.sources.cfs_source import CFSObituarySource, CFSSourceError

BASE_URL = "https://www.resthavenmort.com"
SITEMAP_PATH = "/wtd/obituary_sitemap.xml"
LISTINGS_URL = f"{BASE_URL}/listings"
SITEMAP_URL = f"{BASE_URL}{SITEMAP_PATH}"

ResthavenSourceError = CFSSourceError


class ResthavenSource(CFSObituarySource):
    def __init__(self, session: requests.Session | None = None):
        super().__init__(base_url=BASE_URL, sitemap_path=SITEMAP_PATH, session=session)
