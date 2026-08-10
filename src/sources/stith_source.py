"""sources/stith_source.py — CFS adapter for Stith Family Funeral Home
(stithfamilyfunerals.com).

Thin per-tenant config over `cfs_source.CFSObituarySource` — see that module for the
shared implementation, and for why Stith and Resthaven Mortuary are the same platform
underneath (confirmed 2026-08-10, not assumed from one and applied to the other).
"""

import requests

from src.sources.cfs_source import CFSObituarySource, CFSSourceError

BASE_URL = "https://www.stithfamilyfunerals.com"
SITEMAP_PATH = "/sth/obituary_sitemap.xml"
LISTINGS_URL = f"{BASE_URL}/listings"
SITEMAP_URL = f"{BASE_URL}{SITEMAP_PATH}"

# Backward-compat alias — pre-generalization code/tests catch this name.
StithSourceError = CFSSourceError


class StithSource(CFSObituarySource):
    def __init__(self, session: requests.Session | None = None):
        super().__init__(base_url=BASE_URL, sitemap_path=SITEMAP_PATH, session=session)
