# Far West Legacy — Changelog

All notable changes to this project are documented here.
Format: session number, date, milestone label, summary of changes.

---

## Session 005 (2026-04-27) — Version Banner + Release Notes + Logs Modal

**Version: 0.5.0**

- Added `APP_VERSION` constant and `src/version.py` module
- Footer now shows clickable `v0.5.0` and `Logs` buttons (replaces plain "Powered by Cannon Ops" line)
- Clicking version opens release notes modal with bundled `CHANGELOG.md` (read once at startup)
- Clicking Logs opens tabbed modal: **App** (last 200 log records via in-memory ring buffer) and **Activity** (last 50 user actions: extract_ok / extract_error)
- Activity hooks wired into `/extract` route across all four branches (success, FetchError, ExtractionError, ValidationError on empty input)
- New routes: `GET /changelog`, `GET /logs`
- Logging: ring-buffer handler attached to `werkzeug` and `src` loggers with `propagate=False` to avoid duplicate emissions
- UI fix: tightened whitespace between caption box and Extract button (`.form-actions` margin reduced from 1.5rem + 1rem padding + border to 0.75rem clean)

---

## Session 004 (2026-04-27) — Demo Polish

- Sample obituary dropdown moved to textarea label-right, removed auto-submit (commit `ec6f48a`)
- Extract button shows loading state on submit (commit `642bd3c`)
- "Start Over" and "Extract Another Obituary" routes corrected to `/tool` (commit `8fd25ab`)
- Disabled-button repaint via double `requestAnimationFrame`; sample dropdown locked during extraction (commit `ab14969`)
- `cursor: not-allowed` on disabled primary button (commit `002b421`)
- Tricounty URL fetch bug (name extracted but no facts) DEFERRED to future session

---

## Session 003 (2026-04-27) — Website Wording + Render Auto-Deploy Fix

**Note on session numbering:** this is FWL 003 dated 2026-04-27.
There is a prior "Session 003 / 003a" dated 2026-04-18 (MacBook demo
scripts, now deprecated). Going forward, session entries carry dates.

### Template edits
- `templates/home.html`: softened hero subhead — production FS write
  described as planned once approval is granted, not as current capability.
- `templates/home.html`: same softening applied to "What it does" paragraph.
- `templates/base.html`: added FS API attribution + Intellectual Reserve
  trademark notice to shared footer (visible site-wide on all pages).

### Verification
- Tests: 30 passed, 3 skipped. No regressions.
- Live site UAT confirmed all changes visible at `farwestlegacy.com/`
  and `farwestlegacy.com/tool` after Render deploy.

### Render auto-deploy fix
- Root cause: Render GitHub App installed on personal `joelcannon`
  account, not on `cannon-ops` org that owns the repo.
- Fix: installed Render GitHub App on `cannon-ops` org, single-repo
  scope (`far-west-legacy` only). Validated by next push auto-deploying.

### Operational additions
- UptimeRobot monitor ID 802933445: 5-min HTTP(s) ping on
  `https://farwestlegacy.com/`, alerting `joelcannon@mac.com`.
  Prevents Render free-tier cold starts between visits.

### Workflow formalized
- Three-pass discipline (recon → diff → execute) adopted for all
  Cannon Ops projects. Matches existing Sykes Power workflow.

### LICENSE update
- Copyright line updated: `Joel Cannon` → `Joel Cannon (Cannon Digital LLC)`
  for alignment with legal entity declared in CLAUDE.md.

### Commit
`00f033f` + close commit (repo-memory, CHANGELOG, LICENSE)

---

## Session 002b — 2026-04-26 — Tech Debt + Render-First Pivot

**Goal:** Burn down the Known Issues surfaced in 002, prepare reproduction material for the URL-fetching P0, and pivot deployment docs to Render-as-demo (Dell-as-dev).

### Fixed
- **`src/app.py` deduplication** — file went from 341 lines (two end-to-end copies) to 176 lines (single canonical copy). Marketing routes (`/` → `home.html`, `/tool` → `index.html`) preserved; the duplicated `__main__` block and stale `index()` returning `index.html` are gone. Verified via `app.url_map`: 6 routes register, marketing homepage renders, `/tool` serves the extractor UI. (commit `4388a99`)
- **`app.secret_key` wired to env var** — `os.getenv("FLASK_SECRET_KEY", "dev-secret-change-in-prod")`. Production action still pending: set `FLASK_SECRET_KEY` in the Render dashboard. (commit `17859ce`)
- **`.env.example` corrected** — `FAMILYSEARCH_REDIRECT_URI` now `:8081`, `FAMILYSEARCH_ENV=beta` (was `:8080` and `integration`), `FLASK_SECRET_KEY` documented as production-required.

### Added
- `tests/fixtures/url_fetch_failures.md` — four real-world reproduction cases for the URL-fetching P0 (Tri-County Weekly silent garbage-return, legacy.com 404/JS-only, dignitymemorial.com 403 UA-block, findagrave.com generic chrome). Includes pattern summary and FWL 003 design notes. **Not fixed in 002b — handoff to FWL 003.** (commit `787d0e9`)

### Changed
- **MacBook demo workflow deprecated.** `repo-memory.md` Deployment Topology now lists Dev=Dell + Production=Render only; MacBook section moved under a "DEPRECATED" heading with historical notes preserved. `CLAUDE.md` Demo Environment + Deployment sections rewritten to reflect Render as the demo platform. `start_mac.sh`, `copy_sample_mac.sh`, `deploy/*` retained on disk for reference but flagged unmaintained. (commit `32809c5`)
- **`repo-memory.md` Known Issues** — first two items (app.py dedupe, hardcoded secret) struck through and tagged as fixed in 002b; ephemeral filesystem still flagged for pre-rollout work.

### Tests
- 30 passed, 3 skipped (no regressions across all four commits)

### Flagged TODOs / Handoffs to FWL 003
- **URL-fetching P0** — reproduction cases captured in `tests/fixtures/url_fetch_failures.md`. Three classes of bug to address: (1) silent garbage-return when no article container is found, (2) bot-hostile User-Agent, (3) JS-rendered pages.
- **Production env var on Render** — `FLASK_SECRET_KEY` needs to be set in the Render dashboard; until then prod falls back to the dev placeholder (fine because no sessions/flash yet).
- **Render free-tier ephemeral filesystem** — `tmp/` and `output/` reset on container restart. Persist before any library-partner rollout.

---

## Session 002 close — 2026-04-26 — Session Handoff Infrastructure

**Goal:** Stand up production-aware session-handoff infrastructure now that FWL is graduating from a personal project to a product with stakeholders (live site, library collaboration interest, paid-customer lead).

### Added
- `repo-memory.md` — single source of truth for current state: deployment topology (Dev / Demo / Production), stakeholders, recent sessions, active bugs (URL fetching P0), deferred bugs, pending decisions, env vars, external dependencies. To be updated before every session close.
- `scripts/begin.sh` and `scripts/begin.ps1` — session-start helpers (git status, last commit, test summary, last 30 lines of `repo-memory.md`).
- `scripts/close.sh` and `scripts/close.ps1` — session-end helpers (re-run tests, print git status, remind to update `repo-memory.md` and `CHANGELOG.md`).

### Changed
- `CLAUDE.md`:
  - Port `8080` → `8081` everywhere (stack, milestone notes, file manifest, architecture diagram).
  - FamilySearch API rule #1 now reads "Beta first" (was "Sandbox / integration"); env var is `FAMILYSEARCH_ENV=beta`.
  - Secrets section notes the beta `FAMILYSEARCH_CLIENT_ID` (AppKey) lives in `.env`.
  - New **Deployment** section (Dev → MacBook Demo → Production at farwestlegacy.com).
  - New **Session Handoff** section (`repo-memory.md` is single source of truth; `scripts/begin.*` and `scripts/close.*` workflow).
  - New **Stakeholders** section (high-level; details in `repo-memory.md`).
  - File manifest extended with `repo-memory.md`, the four session scripts, `start_mac.sh`, and `copy_sample_mac.sh`.

### Tests
- 30 passed, 3 skipped (no regressions)

### Flagged TODOs
- `repo-memory.md` Production section: hosting platform = Render confirmed; deploy details captured. Remaining TODOs: confirm Cloudflare-vs-Render DNS arrangement, record who has Render dashboard access, confirm `FAMILYSEARCH_*` and `FLASK_SECRET_KEY` are set on Render.
- URL-fetching bug needs a reproduction case captured in the next session.
- Tech debt items inherited from `NOTES.md` (`src/app.py` duplication, hardcoded `secret_key`, ephemeral Render filesystem) are now cross-referenced in `repo-memory.md` Known Issues.

### Integration note
- Rebased onto parallel cowork commits that landed on `origin/main` mid-session: `4788264` (Render deploy + marketing homepage), `636a774` (merge), `f1ffb76` (NOTES.md tech debt), `0401c6b` (Powered-by-Cannon-Ops footer). No conflicts; tests still 30 passed / 3 skipped after rebase. `repo-memory.md` updated to reflect the integrated state.

---

## Session 001 close — 2026-04-19 — Conference Demo & Production Launch

**Goal:** Wrap FWL 001: stand up the MacBook demo via SSH from the Dell, deploy to `farwestlegacy.com`, and demo live at the AI+Genealogy seminar at the Mid-West Genealogy Center.

### Added
- `start_mac.sh`, `copy_sample_mac.sh` — macOS demo helpers (already shipped in sessions 003/003a, finalized for conference use).
- Production deployment to `farwestlegacy.com` (hosting platform / deploy details captured in `repo-memory.md` — TODO to confirm).

### Demoed
- Live at the **Mid-West Genealogy Center** AI+Genealogy seminar.

### Outcomes
- **Mid-West Genealogy Center** — Director **Katie Smith** raised possible collaboration with potential rebrand for library patrons. Scope undefined.
- **Matthew Johnson** — surfaced as a potential paying customer for archivist-team document processing (obituaries + pedigree charts + family trees). Meeting set for 2026-04-27.
- FWL graduates from "personal project" to "product with stakeholders" — motivated the handoff infrastructure work in session 002.

### Tests
- 30 passed, 3 skipped (no regressions)

---

## Session 004 — 2026-04-18 — launchd Deploy

**Goal:** Deploy Flask as a persistent user-level launchd service on the MacBook, so the demo survives SSH disconnect and reboot.

### Added
- `deploy/com.farwestlegacy.app.plist` — launchd service definition
- `deploy/install_mac.sh` — idempotent installer: copies plist, loads service, verifies port, prints URLs
- `deploy/uninstall_mac.sh` — clean removal (preserves logs)
- `deploy/README.md` — stable install vs dev mode workflow

### Changed
- `start_mac.sh` — dev-mode launcher now stops the launchd service before running foreground Flask and restarts it on exit (via `trap`)
- `CLAUDE.md` — Demo Environment section updated with launchd service details and corrected repo path (`~/projects/far-west-legacy`, lowercase)

### Tests
- 30 passed, 3 skipped (no regressions)

---

## Session 003a — 2026-04-18 — Flask bind fix

**Goal:** Make Flask reachable over Tailnet so demo can be viewed from other machines.

### Fixed
- `src/app.py` — `app.run()` now binds to `host="0.0.0.0"` (was implicit `127.0.0.1`, which blocked access from Dell/Tailnet to the MacBook Flask instance)

### Tests
- 30 passed, 3 skipped (no regressions)

---

## Session 003 — 2026-04-18 — MacBook Demo Scripts

**Goal:** Add macOS demo scripts and sample obituaries so the app can be demoed on the MacBook with minimal friction.

### Added
- `demo/sample_neese.txt` — sparse obituary (no spouse/children, all relatives deceased)
- `demo/sample_veteran.txt` — rich obituary (veteran, full family, service details)
- `demo/sample_amish.txt` — large-family obituary (8 children, 42 grandchildren, maiden name)
- `start_mac.sh` — macOS Flask launcher; kills port 8081, cleans tmp/, activates venv, starts Flask
- `copy_sample_mac.sh` — lists demo samples or copies a named sample to macOS clipboard via pbcopy

### Changed
- `CLAUDE.md` — documented macOS demo script workflow and port 8081 for MacBook demo

### Verified
- `FLASK_PORT` env var honored in `src/app.py` (defaults to 8080; set to 8081 on MacBook)

### Tests
- 30 passed, 3 skipped (no regressions)

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
