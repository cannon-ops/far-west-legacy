# repo-memory.md — Far West Legacy

Single source of truth for current state. Update before every session close.

---

## Current State

- **Branch:** `main`
- **Last commit at session close:** `00f033f` — FWL 003: soften hero claim; add FS API attribution to footer
- **Tests:** 30 passed, 3 skipped (network integration tests, gated by `RUN_NETWORK_TESTS=1`)
- **Milestone:** 1 complete (extract + fetch + CLI + Flask review UI). Milestone 2 not yet started.
- **What works right now:**
  - Paste obituary text or supply a `.txt` file → Claude Haiku extracts structured JSON (deceased + relationships + eulogy + service details).
  - Flask review UI on port 8081: paste/URL → extract → editable review form → approve → JSON saved to `output/`.
  - Production site live at `farwestlegacy.com` (Render auto-deploy on push to main).
  - UptimeRobot keep-alive monitor (ID 802933445, 5min ping) prevents Render free-tier cold starts.
- **What does not work yet:**
  - URL fetching for online obituaries (see Active Bugs).
  - FamilySearch OAuth + writes (Milestone 2 — not started).
  - Photo / OCR ingestion (Milestone 3 — not started).

---

## Deployment Topology

Three-tier setup:

### Dev — Dell Optiplex 3060 (Windows)
- **Path:** `c:\Users\joelc\Projects\far-west-legacy`
- **Python:** 3.12+ in `.venv` (activate with `.venv\Scripts\activate`)
- **Run Flask:** `python -m src.app` (binds to `0.0.0.0:8081`)
- **Primary code-editing environment.** All commits originate here.

### ~~Demo / Local — MacBook Air~~ (DEPRECATED 2026-04-26)
- **Status:** No longer the demo platform. Production demo is at `farwestlegacy.com` (Render). Dev and tests run on the Dell.
- **Files kept for reference, not deleted:** `start_mac.sh`, `copy_sample_mac.sh`, `deploy/install_mac.sh`, `deploy/uninstall_mac.sh`, `deploy/com.farwestlegacy.app.plist`, `deploy/README.md`. These are unmaintained — do not assume they reflect current behavior.
- **Historical notes (in case the MacBook is revived):**
  - Tailscale IP `100.68.44.127:8081`, path `~/projects/far-west-legacy` (lowercase), launchd service `com.farwestlegacy.app`, logs `~/Library/Logs/far-west-legacy/flask.{log,err}`.
- **Demo samples:** `demo/sample_*.txt` (synthetic / anonymized) — still used for local dev on the Dell and as paste-in fodder for production demos.

### Production — farwestlegacy.com
- **Domain registrar:** Cloudflare (TODO: confirm — likely DNS-only proxied to Render)
- **Hosting platform:** **Render** (free plan, Oregon region). Configured via `render.yaml` Blueprint at repo root.
  - Service name: `far-west-legacy`, runtime `python`, branch `main`, `autoDeploy: true`
  - Start command: `gunicorn -w 2 -b 0.0.0.0:$PORT src.app:app`
  - Build command: `pip install -r requirements.txt`
  - Health check: `/`
- **Deployment process:** push to `main` → Render auto-deploys. Marketing homepage at `/`, tool at `/tool`.
- **Env vars on Render:** `PYTHON_VERSION=3.12.4`, `FLASK_ENV=production`, `ANTHROPIC_API_KEY` (set in Render dashboard, never committed). TODO: confirm `FAMILYSEARCH_*` vars and `FLASK_SECRET_KEY` are set when needed.
- **Access / credentials:** TODO — record who has Render dashboard access and where the account credentials live.
- **Status:** Live and demoed publicly at the AI+Genealogy seminar. No longer a "personal project" — graduating to product with stakeholders.
- **Filesystem caveat:** Render free-tier filesystem is ephemeral — `tmp/` and `output/` reset on every container restart. Fine for single-user demos; needs durable storage (S3 / Render Disk) before any library-partner rollout.

---

## Active Stakeholders

| Party | Role | Status / Notes |
| --- | --- | --- |
| Joel Cannon (Cannon Digital LLC) | Owner / Managing Member | Builds, sets direction. Email: chiefcannon26@gmail.com |
| Daviess County Historical Society | Sponsor | Trudi Burton has **not yet been briefed** on FWL. Pending decision. |
| Mid-West Genealogy Center (Independence, MO) | Collaboration interest | Director **Katie Smith** raised possible collaboration with potential **rebrand for library patrons**. Scope undefined. |
| Matthew Johnson | Potential paying customer (Perfection Image, perfectionimage.com) | Meeting scheduled **2026-04-27** — pre-meeting overview + internal briefing drafted. Outcome to be recorded post-meeting. Tenant ID: `perfection-image`. Possible RootsTech 2027 booth co-host. |
| FamilySearch / Gordon Clarke | API partner | Beta AppKey issued: `b00T623K88QL2ZON6BEF`. `FAMILYSEARCH_ENV=beta`. Compatibility Review still required before production writes. Contact: clarkegj@churchofjesuschrist.org |

---

## Recent Sessions

- **001 (2026-04-13)** — Project setup, scaffolding (CLAUDE.md, README, pyproject, .env.example, smoke test).
- **002a–002d (2026-04-13)** — Milestone 1 build-out: extractor (Claude Haiku), URL fetcher, CLI, Flask review UI, ARCHITECTURE.md + CHANGELOG.md.
- **003 / 003a (2026-04-18)** — MacBook demo scripts (`start_mac.sh`, `copy_sample_mac.sh`); Flask bound to `0.0.0.0` for Tailnet access; demo samples added.
- **004 (2026-04-18)** — launchd deployment for the MacBook (`com.farwestlegacy.app`); dev-mode coexistence; deploy/install_mac.sh + uninstall_mac.sh + README.
- **001 close (rolled into FWL 001 wrap-up)** — Conference deployment to `farwestlegacy.com`; live demo at the Mid-West Genealogy Center AI+Genealogy seminar; Katie Smith collaboration interest captured; Matthew Johnson lead captured.
- **Render deploy + marketing homepage (origin commits 4788264, 636a774, f1ffb76, 0401c6b — landed 2026-04-26 by parallel cowork stream):** added `render.yaml` Blueprint, marketing `templates/home.html` at `/` with `/tool` for the app, "Powered by Cannon Ops" footer in `templates/base.html`, `requirements.txt` gained `gunicorn`, and `NOTES.md` documents tech debt (see Known Issues).
- **002 (2026-04-26)** — Session handoff infrastructure: this `repo-memory.md`; `scripts/begin.{sh,ps1}` and `scripts/close.{sh,ps1}`; CLAUDE.md cleanup (port 8081, beta env, deployment + stakeholders + handoff sections); production awareness. Rebased onto the parallel Render-deploy commits before push.
- **002b (2026-04-26)** — Tech debt + Render-first pivot: `src/app.py` deduplication, `FLASK_SECRET_KEY` env-var support, MacBook demo deprecation, URL fetch failures captured in `tests/fixtures/url_fetch_failures.md`. Commit `f0ce280`.
- **003 (2026-04-27) — Website Wording + Render Auto-Deploy Fix:** Softened hero claim and "What it does" paragraph in `templates/home.html` to reflect sandbox-only status. Added FS API attribution + Intellectual Reserve trademark notice to footer in `templates/base.html`. Tests: 30 passed, 3 skipped. Diagnosed and fixed Render auto-deploy: GitHub App was installed on personal `joelcannon` account but not on `cannon-ops` org — installed at org level (single-repo scope). UptimeRobot monitor 802933445 created (5-min keep-alive). Three-pass workflow discipline (recon → diff → execute) formalized across all Cannon Ops projects. Note: this FWL 003 (2026-04-27) is distinct from the earlier Session 003/003a (2026-04-18, MacBook demo scripts, now deprecated). Future sessions adopt date-disambiguating labels. Commit `00f033f`.

---

## Active Bugs

- **[P0] URL fetching broken for online obituaries.**
  - Symptom: paste a real obituary URL into the Flask UI → fetch fails (or returns garbage).
  - Reproduction cases: documented in `tests/fixtures/url_fetch_failures.md` — four classes (silent garbage, UA blocking, JS-only rendering, paywalled).
  - Suspected fix: tiered fetcher (plain requests → UA spoofing → headless browser, opt-in only). Architecture discussion deferred to FWL 005.
  - Owner: FWL 005.

---

## Deferred Bugs

(none recorded — populate as discovered)

---

## Pending Decisions

- **Trudi Burton conversation** — when and how to brief the Daviess County Historical Society.
- **Tri-County email send** — outreach email drafted; send pending Joel's go-ahead.
- **Mid-West Genealogy Center collaboration scope** — what does "rebrand for library patrons" actually look like? White-label? Co-branded? Hosted by them?
- **Matthew Johnson scope + pricing** — what's the offering, what's the price, what does the archivist team actually need? Meeting 2026-04-27.

---

## Known Issues

Sourced from `NOTES.md` (committed 2026-04-26 in the Render-deploy stream). Pick up next time we're in this repo — flagged but not fixed in current session.

- **~~`src/app.py` is duplicated end-to-end~~** — **FIXED 2026-04-26 (FWL 002b commit `4388a99`).** File is now a single canonical 176-line module; marketing routes (`/`, `/tool`) preserved.
- **~~`src/app.py` hardcoded `secret_key`~~** — **FIXED 2026-04-26 (FWL 002b).** `app.secret_key` now reads `os.getenv("FLASK_SECRET_KEY", "dev-secret-change-in-prod")`. **Production action still pending:** set `FLASK_SECRET_KEY` in the Render dashboard. Until then, the prod instance falls back to the dev placeholder — fine until sessions/flash messages are introduced.
- **Render free-tier filesystem is ephemeral** — `tmp/` and `output/` reset on every container restart. Not urgent while single-user, but must persist to durable storage (S3 / Render Disk) before any "share with library partners" rollout.

---

## Environment Variables

All set in `.env` at repo root (template: `.env.example`). Never committed.

| Variable | Purpose |
| --- | --- |
| `ANTHROPIC_API_KEY` | Claude API key (BYOK — user-supplied). Used by `src/extract.py`. |
| `FAMILYSEARCH_CLIENT_ID` | FamilySearch beta AppKey (currently `b00T623K88QL2ZON6BEF`). |
| `FAMILYSEARCH_CLIENT_SECRET` | FamilySearch OAuth client secret. |
| `FAMILYSEARCH_REDIRECT_URI` | OAuth callback URL. Beta is registered for `http://localhost:8081/callback` and the `farwestlegacy.com` realm. |
| `FAMILYSEARCH_ENV` | `beta` for development (was `integration` in template — beta is the correct value now). |
| `FLASK_PORT` | Flask bind port. Defaults to `8081`. (Production on Render uses `$PORT` from gunicorn, not `FLASK_PORT`.) |
| `FLASK_ENV` | Set to `production` on Render. Unset locally. |
| `PYTHON_VERSION` | Render-only: `3.12.4`. |
| `FLASK_SECRET_KEY` | Wired in `src/app.py` (FWL 002b). REQUIRED on Render in production; falls back to `"dev-secret-change-in-prod"` if unset (fine locally; fine in prod until sessions/flash are introduced). |

---

## External Dependencies

- **Anthropic API** — Claude Haiku 4.5 (`claude-haiku-4-5-20251001`) for text extraction. `max_tokens=4096`. Sonnet planned for vision/photo OCR (Milestone 3).
- **FamilySearch beta** — AppKey `b00T623K88QL2ZON6BEF`. Registered redirect URI `http://localhost:8081/callback`. `farwestlegacy.com` realm registered. Compatibility Review required before production.
- **Domain registrar** — Cloudflare (`farwestlegacy.com`).
- **Hosting platform (production)** — **Render** (free plan, Oregon). Blueprint: `render.yaml`. Auto-deploys on push to `main`. Service: `far-west-legacy`. Runs `gunicorn -w 2 -b 0.0.0.0:$PORT src.app:app`.
- **Tailscale** — _(formerly used for MacBook demo access; see deprecated section under Deployment Topology)_.
