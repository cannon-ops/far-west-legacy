# repo-memory.md — Far West Legacy

Single source of truth for current state. Update before every session close.

---

## Current State

- **Branch:** `fwl-010-fs-upload-m2` (not yet merged to `main`; pushed, do not merge without flagging back first per H-020 handoff)
- **Last commit at session close:** see `git log` — FWL-010: M2.0 FamilySearch OAuth login + M2.1 fs_map/fs_client dry-run layer
- **Tests:** 81 passed, 3 skipped (network integration tests, gated by `RUN_NETWORK_TESTS=1`)
- **Milestone:** 1 complete (extract + fetch + CLI + Flask review UI). Milestone 2: **M2.0 (auth) and M2.1 (mapping/client/dry-run) built this session** — see below. M2.2 (match-check + confirm-gate UI) and M2.3 (live writes) not started. Milestone 3.0 (fixtures + model eval) complete; M3.1 not started.
- **Other unmerged branches (do not tangle with fwl-010):** `fwl-007-design-drafts` (awaiting Joel's own review), `fwl-009-m3-1-scan-path` (no CHANGELOG entry yet).
- **What works right now:**
  - Paste obituary text or supply a `.txt` file → Claude Haiku extracts structured JSON (deceased + relationships + eulogy + service details).
  - Flask review UI on port 8081: paste/URL → extract → editable review form → approve → JSON saved to `output/`.
  - Production site live at `farwestlegacy.com` (Render auto-deploy on push to main).
  - UptimeRobot keep-alive monitor (ID 802933445, 5min ping) prevents Render free-tier cold starts.
  - M3.0 eval tooling: `make fixtures` (synthetic scan generator), `scripts/m3_eval.py` (Sonnet-vs-Haiku vision transcription eval) — measured, not yet wired into the app.
  - **FamilySearch OAuth2 + PKCE (`src/fs_auth.py`), routes `GET /auth/login` / `GET /callback` in `src/app.py`, signed-in badge in `templates/base.html`.** `/auth/login` verified locally: produces a correctly-formed authorize redirect to `https://identbeta.familysearch.org/cis-web/oauth2/v3/authorization` with PKCE S256 `code_challenge`, `state`, `redirect_uri`. **Not yet verified end-to-end** — completing a real sign-in requires a human with FamilySearch beta credentials in an actual browser (Incapsula bot-protection blocks headless verification); A13 cannot do this step. See Pending Decisions.
  - **`src/fs_map.py`** — pure GEDCOM X field mapping (obituary JSON → FS person/relationship/source structures), 24 unit tests covering partial dates, maiden names, suffixes, sibling gating, living-relative default-exclusion/opt-in, "don't guess the other parent" for children.
  - **`src/fs_client.py`** — `FamilySearchClient` with the dry-run boundary (POST/PUT/DELETE short-circuited, GETs always real), upload journal (`tmp/<id>.upload.json`) for idempotent resume, 429/503 `Retry-After` backoff, 401 → `FSAuthExpiredError`, 4xx halts without retry. 9 tests against `httpx.MockTransport`. `run_upload_sequence()` orchestrates persons → relationships → source → attach per plan §4.2.
  - Dry-run golden test (`tests/test_fs_upload_dry_run.py`) against the Neese fixture (`tests/fixtures/sample_obituary_01_extracted.json`, hand-authored to match `test_extract.py`'s live-API assertions) produces the expected 20-entry intended-writes journal with zero network calls.
- **What does not work yet:**
  - URL fetching for online obituaries (see Active Bugs).
  - FamilySearch live writes and match-check/confirm-gate UI (M2.2/M2.3 — no `/upload` route exists yet; not in scope this session).
  - Photo / OCR ingestion pipeline itself (M3.1–M3.5 — M3.0 eval done, `src/ingest.py`/`src/transcribe.py` not built yet).

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
- **FWL-006 / FWL-007 (2026-07-10) — Design drafts:** `planning/familysearch-upload-plan.md` (FamilySearch profile-upload integration plan) and `docs/obituary-pipeline-DRAFT.md` + `docs/familysearch-integration-DRAFT.md` (scanned-obituary pipeline design, Milestone 3 phased plan M3.0–M3.5). No code. Commits `f47e454`, `906f331`.
- **FWL-010 / Session 010 (2026-08-08) — M2.0 FamilySearch OAuth + M2.1 mapping/client/dry-run:** Built against `planning/familysearch-upload-plan.md` §1, 2, 4, 5 without waiting on the Gordon Clarke orientation call (used the plan's documented fallbacks: PKCE S256 by default, scope names deferred to first live handshake). `src/fs_auth.py` (authorize-URL builder, PKCE, token exchange, current-user fetch, server-side session store keyed off a Flask-session id — never the raw token in the cookie), `/auth/login` + `/callback` routes and signed-in badge in `src/app.py` / `templates/base.html`. `src/fs_map.py` (pure GEDCOM X mapping) and `src/fs_client.py` (journal + dry-run boundary + write-sequence orchestration), both with unit tests, plus an offline golden-file dry-run test against the Neese fixture. Confirmed live via WebFetch/WebSearch against developers.familysearch.org (not from memory): authorize endpoint `https://identbeta.familysearch.org/cis-web/oauth2/v3/authorization` (GET/POST, params `client_id`/`response_type`/`redirect_uri`/`state`/`scope`/`code_challenge`/`code_challenge_method`), token endpoint `https://identbeta.familysearch.org/cis-web/oauth2/v3/token` (`grant_type=authorization_code`, `code_verifier`, no secret), current-user resource `https://apibeta.familysearch.org/platform/users/current`. Removed the vestigial `FAMILYSEARCH_CLIENT_SECRET` from `.env`/`.env.example` and the commented-out `authlib` line from `requirements.txt` per the plan's M2.0 decision to hand-roll OAuth. Tests: 81 passed, 3 skipped (up from 45+3; all 36 new tests are FS-layer, offline). **Live handshake / scope capture still open** — see Pending Decisions.
- **FWL-008 / Session 008 (2026-07-11) — M3.0 fixture generator + vision transcription eval:** Built `scripts/gen_fixtures.py` (synthetic scan fixtures, `make fixtures`), `scripts/m3_eval.py` (Sonnet 5 vs. Haiku 4.5 transcription eval: resolution-knee matrix, segmentation-probe accuracy, PDF-vs-per-page comparison), `scripts/eval_metrics.py` (CER/WER/IoU), `prompts/obituary_transcribe.md`. Ran the full eval against synthetic fixtures (~$0.39 API spend). Results and chosen defaults in `docs/m3-0-eval-note.md`: Haiku 4.5 @ 1568px long edge is the default transcription tier (reaches CER parity with Sonnet at ≥1092px, ~2–3× cheaper); crop-then-transcribe confirmed over full-page name-targeted for segmentation; per-page image ingest kept over native PDF input (PDF tested only the trivial one-obit-per-page case). Tests: 45 passed, 3 skipped. Open questions needing real (non-synthetic) samples remain: pre-1930 newsprint (open q.6), handwritten material (open q.3, out of scope).

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
- **FamilySearch OAuth scope names + live sign-in verification (M2.0)** — `/auth/login` produces a correctly-formed authorize redirect (verified 2026-08-08), but completing the handshake requires a human signing into a real FamilySearch beta account in an actual browser (identbeta.familysearch.org is behind Incapsula bot-protection, so this can't be verified headlessly). **Action needed from Joel:** run `.venv\Scripts\activate` then `python -m src.app`, open `http://localhost:8081/auth/login`, sign in with a FamilySearch beta-environment account, confirm the header badge shows the FS display name, then check the app log (footer "Logs" button, or `python -m src.app` console) for the `FamilySearch OAuth token granted. scope=...` line and paste the `scope=` value back so it can be recorded here and in `.env` as `FAMILYSEARCH_SCOPE`. Also still pending from the Gordon Clarke call (not scheduled yet): PKCE-required confirmation, beta tree reset cadence, production redirect URI process, sibling-modeling confirmation, living-person private-space behavior, Compatibility Review criteria, refresh-token behavior.

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
| `FAMILYSEARCH_REDIRECT_URI` | OAuth callback URL. Beta is registered for `http://localhost:8081/callback` and the `farwestlegacy.com` realm. |
| `FAMILYSEARCH_ENV` | `beta` for development (was `integration` in template — beta is the correct value now). |
| `FAMILYSEARCH_SCOPE` | Space-delimited OAuth scope names for `/auth/login`. Blank until confirmed via a live sign-in (see Pending Decisions) — FamilySearch grants the AppKey's default scope when omitted. Added FWL-010 (2026-08-08); `FAMILYSEARCH_CLIENT_SECRET` removed the same session (vestigial — public client, no secret). |
| `FLASK_PORT` | Flask bind port. Defaults to `8081`. (Production on Render uses `$PORT` from gunicorn, not `FLASK_PORT`.) |
| `FLASK_ENV` | Set to `production` on Render. Unset locally. |
| `PYTHON_VERSION` | Render-only: `3.12.4`. |
| `FLASK_SECRET_KEY` | Wired in `src/app.py` (FWL 002b). REQUIRED on Render in production; falls back to `"dev-secret-change-in-prod"` if unset (fine locally; fine in prod until sessions/flash are introduced). |

---

## External Dependencies

- **Anthropic API** — Claude Haiku 4.5 (`claude-haiku-4-5-20251001`) for text extraction. `max_tokens=4096`. Sonnet planned for vision/photo OCR (Milestone 3).
- **FamilySearch beta** — AppKey `b00T623K88QL2ZON6BEF`. Registered redirect URI `http://localhost:8081/callback`. `farwestlegacy.com` realm registered. Compatibility Review required before production. Confirmed hosts (2026-08-08): identity `identbeta.familysearch.org` (authorize + token, `/cis-web/oauth2/v3/{authorization,token}`), API `apibeta.familysearch.org` (current-user `/platform/users/current`; tree/source paths per `src/fs_client.py`, unverified against live docs until M2.3). Public client — no client secret.
- **Domain registrar** — Cloudflare (`farwestlegacy.com`).
- **Hosting platform (production)** — **Render** (free plan, Oregon). Blueprint: `render.yaml`. Auto-deploys on push to `main`. Service: `far-west-legacy`. Runs `gunicorn -w 2 -b 0.0.0.0:$PORT src.app:app`.
- **Tailscale** — _(formerly used for MacBook demo access; see deprecated section under Deployment Topology)_.
