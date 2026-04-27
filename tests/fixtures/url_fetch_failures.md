# URL Fetch Failures — Reproduction Cases for FWL 003

**Bug:** `src/fetch.py::fetch_obituary_text(url)` is unreliable on real-world obituary sites. Failure modes range from silent garbage-return to outright HTTP 403 to JS-only pages where the article never exists in static HTML.

This file is **reproduction material for FWL 003** — do not attempt to fix in 002b. Each case below was captured locally on **2026-04-26** against `src/fetch.py` at commit `17859ce`.

How to reproduce a single case:

```bash
source .venv/Scripts/activate   # Windows; on Mac use .venv/bin/activate
python -c "from src.fetch import fetch_obituary_text; print(repr(fetch_obituary_text('<URL>')[:300]))"
```

---

## Case 1 — Silent garbage-return on Tri-County Weekly (highest priority)

- **URL:** `https://jamesporttricountyweekly.com/category/obituaries/`
- **What happens:** `fetch_obituary_text()` returns successfully with **4842 chars** of plain text. No `FetchError` raised.
- **What the returned text actually contains:** the site's nav/header/sidebar chrome — `Subscribe`, `Loading...`, `JAMESPORT WEATHER`, `News`, `Church News`, `Court News`, etc. The leading byte is `�` (encoding replacement char).
- **What should happen:** either return the text of an obituary article, or raise `FetchError("could not locate obituary content on …")`. Returning sidebar text as if it were an obituary causes Claude Haiku to extract bogus structured data downstream.
- **Why it happens:** The page contains zero `<article>` tags and no `div.entry-content`. The "largest `<div>` by text length" fallback in `_largest_div()` matches the global page wrapper, which contains the entire sidebar and nav. The function has no signal that it picked the wrong container.
- **Adjacent observation:** `https://jamesporttricountyweekly.com/` (homepage) exhibits the same failure mode and returns ~20 800 chars of chrome.

## Case 2 — `legacy.com` returns 404 / no article in static HTML

- **URL:** `https://www.legacy.com/us/obituaries/local/missouri`
- **What happens:** `requests.HTTPError` → `FetchError: HTTP error fetching … 404 Client Error: Not Found …`
- **What should happen:** Either return article text (if a real obituary URL is supplied) or raise a clear `FetchError` explaining the page doesn't contain an obituary. The 404 here is technically correct for *this* URL, but the broader pattern is that legacy.com routes most obituary content through JS-rendered pages — even valid article URLs return a static HTML shell that does not contain the obituary body.
- **Why it matters:** legacy.com is a top destination users will paste from. The bug is partly "we don't render JS" and partly "we don't degrade gracefully when we can't find content."

## Case 3 — `dignitymemorial.com` blocks our User-Agent (403)

- **URL:** `https://www.dignitymemorial.com/obituaries`
- **What happens:** `requests.HTTPError` → `FetchError: HTTP error fetching … 403 Client Error: Forbidden …`
- **What should happen:** Set a real browser-like `User-Agent` so we don't trigger naïve bot filters. The current header is `"User-Agent": "far-west-legacy/0.1"` ([src/fetch.py:34](src/fetch.py#L34)), which many sites reject.
- **Why it matters:** A non-trivial fraction of funeral-home and memorial sites apply UA-based bot filters. A realistic Chrome/Firefox UA string would unblock the majority of these.

## Case 4 — `findagrave.com` returns generic chrome text

- **URL:** `https://www.findagrave.com`
- **What happens:** Returns successfully with **3890 chars** beginning `'Unlock Ad-Free Browsing & Support Find a Grave …'` — generic site copy, not an obituary.
- **What should happen:** Raise `FetchError` (or surface a "this doesn't look like an obituary" warning) when the largest-`<div>` fallback fires *and* the resulting text contains none of the expected obituary signals (date patterns, "born", "died", "survived by", funeral home name, etc.).
- **Why it matters:** Same root cause as Case 1 — silent fallback masks the real problem.

---

## Pattern summary (for FWL 003 design)

Three classes of bug, in priority order:

1. **Silent garbage-return when no article container is found.** The `_largest_div()` fallback should be guarded by an obituary-shape sanity check, or removed entirely in favor of an explicit `FetchError`. (Cases 1, 4.)
2. **Bot-hostile User-Agent.** Hardcoded `far-west-legacy/0.1` triggers UA filters. Move to a realistic browser UA string. (Case 3.)
3. **JS-rendered pages.** Out of scope for `requests`+`BeautifulSoup`. Either document the limitation in the UI ("this site requires JS — paste the obituary text instead") or invest in a headless-browser path (Playwright). (Case 2 and many others.)

Suggested test approach for FWL 003:

- Add `tests/test_fetch_real_sites.py` — guarded by `RUN_NETWORK_TESTS=1`, asserts both *positive* (real obituary URL → recognizable obituary text) and *negative* (chrome-only URL → `FetchError`) behavior against the four cases above.
- Capture HTML responses from each case as fixtures so the parser logic can be unit-tested without network.
