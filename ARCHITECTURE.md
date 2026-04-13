# Far West Legacy — Architecture

## Overview

Far West Legacy converts obituary text into structured genealogical data and (eventually) writes it to the FamilySearch Family Tree. The pipeline has three stages: **ingest**, **extract**, and **review/write**.

---

## Data Flow

```
┌─────────────────────────────────────────────────────────┐
│                        INPUTS                           │
│                                                         │
│  Pasted text ──────────────────────────────────┐        │
│                                                │        │
│  Obituary URL → fetch.py ──────────────────────┤        │
│    (requests + BeautifulSoup)                  │        │
│    · entry-content div (WordPress)             │        │
│    · <article> tag fallback                    │        │
│    · largest <div> fallback                    │        │
│                                                │        │
│  Photo/scan [future] → Claude Sonnet vision ───┘        │
│    (portrait URL from <img>, page scan upload)          │
└───────────────────────────┬─────────────────────────────┘
                            │ plain text
                            ▼
┌─────────────────────────────────────────────────────────┐
│                      EXTRACTION                         │
│                                                         │
│  extract.py → Claude Haiku (claude-haiku-4-5-20251001)  │
│    System prompt: prompts/obituary_extract.md           │
│    max_tokens: 4096                                     │
│    Output: structured JSON (see Data Schema below)      │
└───────────────────────────┬─────────────────────────────┘
                            │ JSON dict
                            ▼
┌─────────────────────────────────────────────────────────┐
│                      REVIEW UI                          │
│                                                         │
│  Flask app (src/app.py) on port 8080                    │
│                                                         │
│  GET  /              Paste text or enter URL            │
│  POST /extract       Fetch (if URL) + extract           │
│                      → saves tmp/<uuid>.json            │
│                      → redirect to /review/<uuid>       │
│  GET  /review/<id>   Editable form + raw text sidebar   │
│  POST /approve/<id>  User-edited JSON saved to output/  │
│                      → confirmation page                │
└───────────────────────────┬─────────────────────────────┘
                            │ approved JSON
                            ▼
┌─────────────────────────────────────────────────────────┐
│                  LOCAL OUTPUT (current)                 │
│                                                         │
│  output/Surname_GivenNames.json                         │
└───────────────────────────┬─────────────────────────────┘
                            │ [future]
                            ▼
┌─────────────────────────────────────────────────────────┐
│               FAMILYSEARCH API [future]                 │
│                                                         │
│  Target: https://integration.familysearch.org (sandbox) │
│  Auth: OAuth 2.0                                        │
│                                                         │
│  1. Duplicate check — search before any write           │
│  2. Create person (deceased)                            │
│  3. Create relationships (spouses, parents, children,   │
│     siblings)                                           │
│  4. Attach Story Memory (eulogy_text)                   │
│  5. Attach Photo Memory (portrait) [if available]       │
│  6. Attach Source citation (source_url)                 │
│  7. Record hints → open FamilySearch.org for user       │
└─────────────────────────────────────────────────────────┘
```

---

## Data Schema

The extraction pipeline returns a single JSON object. Full field reference: `docs/data_schema.md`.

```json
{
  "deceased": {
    "given_names": "Donna Sue",
    "surname": "Neese",
    "maiden_name": "",
    "suffix": "",
    "gender": "Female",
    "birth_date": "1939-07-25",
    "birth_place": "Jamesport, Missouri",
    "death_date": "2025-12-10",
    "death_place": "Pleasant Valley, Missouri",
    "burial_place": ""
  },
  "relationships": {
    "spouses":  [{ "given_names": "", "surname": "", "deceased": false }],
    "parents":  [{ "given_names": "", "surname": "", "maiden_name": "", "deceased": false }],
    "children": [{ "given_names": "", "surname": "", "deceased": false }],
    "siblings": [{ "given_names": "", "surname": "", "maiden_name": "", "deceased": false }]
  },
  "eulogy_text": "...",
  "service_details": "...",
  "source_url": "https://...",
  "raw_text": "full original text"
}
```

---

## File Manifest

### `src/`

| File | Purpose |
|---|---|
| `__init__.py` | Makes `src` a package |
| `extract.py` | `extract_from_text(text, source_url)` → calls Claude Haiku, returns structured dict. Raises `ExtractionError`. |
| `fetch.py` | `fetch_obituary_text(url)` → HTTP GET + BeautifulSoup parse → plain text. Raises `FetchError`. |
| `cli.py` | CLI entry point. `--text`, `--file`, `--url` modes. Prints JSON, saves to `output/`. |
| `app.py` | Flask web app (port 8080). Four routes: home, extract, review, approve. |

### `templates/`

| File | Purpose |
|---|---|
| `base.html` | Shared layout: Georgia-serif design, CSS variables, responsive grid |
| `index.html` | Home page — paste textarea + URL field |
| `review.html` | Editable review form + raw text sidebar. Add/remove relationship entries. |
| `confirmed.html` | Approval confirmation with data summary |

### `prompts/`

| File | Purpose |
|---|---|
| `obituary_extract.md` | System prompt for Claude Haiku. Defines output schema, field rules, date/place formats. |

### `docs/`

| File | Purpose |
|---|---|
| `data_schema.md` | Full JSON schema reference with field descriptions and examples |

### `tests/`

| File | Purpose |
|---|---|
| `test_extract.py` | Unit tests for `_strip_markdown_fences`; integration tests for `extract_from_text` against sample fixture |
| `test_fetch.py` | Unit tests for HTML parsing (fixture strings, no network); integration tests (skipped unless `RUN_NETWORK_TESTS=1`) |
| `test_placeholder.py` | Smoke test confirming pytest runs |
| `fixtures/sample_obituary_01.txt` | Synthetic obituary for Donna Sue Neese (anonymized test data) |

---

## Input Channels

| Channel | Status | Implementation |
|---|---|---|
| Paste text | Working | `POST /extract` with `obituary_text` field |
| URL fetch | Working | `fetch.py` → WordPress/article/div heuristics |
| Photo/scan (portrait URL) | Future | Claude Sonnet vision on `<img>` from fetched page |
| Photo/scan (file upload) | Future | Multipart upload → Sonnet OCR → extracted text |

---

## Photo Handling (Future)

Two portrait sources are planned:

1. **Portrait URL** — extracted from `<img>` tags on the fetched obituary page. Passed to FamilySearch Photo Memory as a URL attachment.
2. **Page scan / clipping upload** — user uploads an image of a physical newspaper clipping. Claude Sonnet vision extracts the obituary text (replacing the Haiku text pass), and can also crop/identify embedded portrait photos.

---

## FamilySearch Integration Rules

Enforced by policy (`CLAUDE.md`) and not yet implemented in code:

- **Sandbox first** — all writes target `integration.familysearch.org` until Compatibility Review passes
- **No write without review** — user must approve extracted data in the UI before anything is sent
- **Duplicate check** — search the tree before creating any person
- **Record hints** — open FamilySearch.org; never display full record details in-app
- **No Ordinance access** — not requested, not used

---

## Session / Job State

The app uses a lightweight UUID-based session model:

- **`tmp/<uuid>.json`** — written immediately after extraction; read by the review page
- **`output/<Surname_Given>.json`** — written on approval; this is the durable output
- No database. `tmp/` is gitignored and can be cleared freely.
