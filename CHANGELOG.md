# Far West Legacy — Changelog

All notable changes to this project are documented here.
Format: session number, date, milestone label, summary of changes.

---

## Session 002d — 2026-04-13 — Documentation

**Goal:** Create ARCHITECTURE.md, CHANGELOG.md, update CLAUDE.md.

### Added
- `ARCHITECTURE.md` — full data flow diagram, file manifest, input channel table, photo/FamilySearch notes
- `CHANGELOG.md` — this file

### Changed
- `CLAUDE.md` — updated Architecture section to match actual schema; added Milestone 1 status, current file manifest, and max_tokens note

### Tests
- 30 passed, 3 skipped (no regressions)

---

## Session 002c — 2026-04-13 — Milestone 1c: Flask Review UI

**Goal:** Build a Flask web UI for the paste → extract → review → approve workflow.

### Added
- `src/app.py` — Flask app on port 8080
  - `GET /` — home page with paste textarea and URL field
  - `POST /extract` — calls `fetch_obituary_text()` (if URL) then `extract_from_text()`; stores result in `tmp/<uuid>.json`; redirects to review
  - `GET /review/<job_id>` — editable form for all fields; sticky raw-text sidebar
  - `POST /approve/<job_id>` — rebuilds JSON from form POST; saves to `output/`; shows confirmation
- `templates/base.html` — shared layout (Georgia-serif, CSS variables, responsive grid, no frameworks)
- `templates/index.html` — paste/URL input with inline error display
- `templates/review.html` — editable deceased fields, relationship arrays with add/remove, deceased checkboxes
- `templates/confirmed.html` — approval confirmation with full data summary

### Changed
- `.gitignore` — added `tmp/` (Flask session temp files)

### Tests
- 30 passed, 3 skipped (no regressions)

---

## Session 002b — 2026-04-13 — Milestone 1b: URL Fetching & CLI

**Goal:** Add URL fetching and a command-line entry point.

### Added
- `src/fetch.py` — `fetch_obituary_text(url)` with three-tier HTML extraction (WordPress `entry-content` → `<article>` → largest `<div>`); strips nav/header/footer noise; raises `FetchError` on HTTP or parse failure
- `src/cli.py` — `python -m src.cli` with `--text`, `--file`, and `--url` modes; saves JSON to `output/<Surname_Given>.json`; creates `output/` if needed
- `tests/test_fetch.py` — 8 unit tests (HTML fixture parsing, whitespace cleanup, error handling); 3 network integration tests (skipped unless `RUN_NETWORK_TESTS=1`)

### Fixed
- `prompts/obituary_extract.md` — added `"deceased": false` to sibling schema entry so Claude returns the field; fixed pre-existing `test_all_siblings_deceased` failure

### Changed
- `.gitignore` — added `output/`

### Tests
- 30 passed, 3 skipped

---

## Session 002a — 2026-04-13 — Milestone 1a: Obituary Extractor

**Goal:** Build the core extraction pipeline — Claude Haiku reads obituary text and returns structured JSON.

### Added
- `prompts/obituary_extract.md` — system prompt defining the output schema, field rules (dates, places, gender inference, relationship deceased flags), and strict JSON-only output requirement
- `src/extract.py` — `extract_from_text(obituary_text, source_url)` calling Claude Haiku (`claude-haiku-4-5-20251001`); `_strip_markdown_fences()` helper; `ExtractionError` exception class
- `docs/data_schema.md` — full JSON schema reference with field descriptions, formats, and examples
- `tests/fixtures/sample_obituary_01.txt` — synthetic obituary for Donna Sue Neese (anonymized)
- `tests/test_extract.py` — 5 unit tests for `_strip_markdown_fences`; 17 integration tests for `extract_from_text` covering all schema fields (skipped without `ANTHROPIC_API_KEY`)

### Fixed
- `load_dotenv()` call added to `tests/test_extract.py` to ensure `.env` is loaded before pytest skip-markers evaluate `ANTHROPIC_API_KEY`

### Tests
- 22 passed (5 unit + 16 integration + 1 placeholder)

---

## Session 001 — 2026-04-13 — Project Scaffold

**Goal:** Initialize repository structure, virtual environment, configuration files, and a passing smoke test.

### Added
- `CLAUDE.md` — standing rules for Agent 13 sessions (stack, dev env, session protocol, FamilySearch API rules, publicity clause, secrets policy)
- `README.md` — project overview
- `pyproject.toml` — project metadata, ruff/black config, pytest config (`testpaths`, `pythonpath`)
- `requirements.txt` — pinned dependencies (Flask, anthropic, requests, beautifulsoup4, lxml, pytest, ruff, black)
- `.env.example` — secrets template (no real values)
- `.gitignore` — Python, venv, IDE, secrets, test fixtures
- `src/__init__.py` — makes `src` a package
- `tests/test_placeholder.py` — smoke test (`assert True`)
- Directory structure: `src/`, `tests/fixtures/`, `docs/`, `prompts/`, `templates/`, `output/`, `tmp/`

### Tests
- 1 passed
