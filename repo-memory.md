# repo-memory.md — Far West Legacy

Single source of truth for current state. Update before every session close.

---

## Current State

- **Branch:** `main`
- **Last commit:** `27a0389` — Session 004: launchd deploy + dev-mode coexistence
- **Tests:** 30 passed, 3 skipped (network integration tests, gated by `RUN_NETWORK_TESTS=1`)
- **Milestone:** 1 complete (extract + fetch + CLI + Flask review UI). Milestone 2 not yet started.
- **What works right now:**
  - Paste obituary text or supply a `.txt` file → Claude Haiku extracts structured JSON (deceased + relationships + eulogy + service details).
  - Flask review UI on port 8081: paste/URL → extract → editable review form → approve → JSON saved to `output/`.
  - macOS demo deployment running as launchd service `com.farwestlegacy.app` on the MacBook Air.
  - Production site live at `farwestlegacy.com`.
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

### Demo / Local — MacBook Air (`Joels-MacBook-Air`)
- **Tailscale IP:** `100.68.44.127:8081`
- **Path:** `~/projects/far-west-legacy` (lowercase `projects` on disk)
- **Service:** launchd user-agent `com.farwestlegacy.app`
  - Plist: `~/Library/LaunchAgents/com.farwestlegacy.app.plist`
  - Logs: `~/Library/Logs/far-west-legacy/flask.{log,err}`
  - Install: `./deploy/install_mac.sh` — Uninstall: `./deploy/uninstall_mac.sh`
- **Dev mode:** `./start_mac.sh` stops launchd, runs Flask in foreground with `debug=True`, restores launchd on exit.
- **Demo samples:** `demo/sample_*.txt` (synthetic / anonymized).

### Production — farwestlegacy.com
- **Domain registrar:** Cloudflare (TODO: confirm — may also be DNS-only and proxied)
- **Hosting platform:** TODO — confirm where the live site is hosted (Render? Fly.io? a VPS? Cloudflare Pages?)
- **Deployment process:** TODO — document the actual push-to-deploy or manual-deploy workflow used during conference setup
- **Access / credentials:** TODO — record who has push/deploy access and where credentials live
- **Status:** Live and demoed publicly at the AI+Genealogy seminar. No longer a "personal project" — graduating to product with stakeholders.

---

## Active Stakeholders

| Party | Role | Status / Notes |
| --- | --- | --- |
| Joel Cannon (Cannon Digital LLC) | Owner / Managing Member | Builds, sets direction. Email: chiefcannon26@gmail.com |
| Daviess County Historical Society | Sponsor | Trudi Burton has **not yet been briefed** on FWL. Pending decision. |
| Mid-West Genealogy Center (Independence, MO) | Collaboration interest | Director **Katie Smith** raised possible collaboration with potential **rebrand for library patrons**. Scope undefined. |
| Matthew Johnson | Potential paying customer | Meeting **2026-04-27** about paid acceleration so his archivist team can process obituaries + other family-history docs (pedigree charts, trees). |
| FamilySearch / Gordon Clarke | API partner | Beta AppKey issued: `b00T623K88QL2ZON6BEF`. `FAMILYSEARCH_ENV=beta`. Compatibility Review still required before production writes. Contact: clarkegj@churchofjesuschrist.org |

---

## Recent Sessions

- **001 (2026-04-13)** — Project setup, scaffolding (CLAUDE.md, README, pyproject, .env.example, smoke test).
- **002a–002d (2026-04-13)** — Milestone 1 build-out: extractor (Claude Haiku), URL fetcher, CLI, Flask review UI, ARCHITECTURE.md + CHANGELOG.md.
- **003 / 003a (2026-04-18)** — MacBook demo scripts (`start_mac.sh`, `copy_sample_mac.sh`); Flask bound to `0.0.0.0` for Tailnet access; demo samples added.
- **004 (2026-04-18)** — launchd deployment for the MacBook (`com.farwestlegacy.app`); dev-mode coexistence; deploy/install_mac.sh + uninstall_mac.sh + README.
- **001 close (rolled into FWL 001 wrap-up)** — Conference deployment to `farwestlegacy.com`; live demo at the Mid-West Genealogy Center AI+Genealogy seminar; Katie Smith collaboration interest captured; Matthew Johnson lead captured.
- **002 (2026-04-26)** — Session handoff infrastructure: this `repo-memory.md`; `scripts/begin.{sh,ps1}` and `scripts/close.{sh,ps1}`; CLAUDE.md cleanup (port 8081, beta env, deployment + stakeholders + handoff sections); production awareness.

---

## Active Bugs

- **[P0] URL fetching broken for online obituaries.**
  - Symptom: paste a real obituary URL into the Flask UI → fetch fails (or returns garbage).
  - Reproduction case: TODO — capture a specific failing URL in the next session.
  - Suspected cause: site changed structure / blocks `requests` user-agent / JS-rendered content. Needs investigation.
  - Owner: next session.

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

(none recorded — populate as discovered)

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
| `FLASK_PORT` | Flask bind port. Defaults to `8081`. |

---

## External Dependencies

- **Anthropic API** — Claude Haiku 4.5 (`claude-haiku-4-5-20251001`) for text extraction. `max_tokens=4096`. Sonnet planned for vision/photo OCR (Milestone 3).
- **FamilySearch beta** — AppKey `b00T623K88QL2ZON6BEF`. Registered redirect URI `http://localhost:8081/callback`. `farwestlegacy.com` realm registered. Compatibility Review required before production.
- **Domain registrar** — Cloudflare (`farwestlegacy.com`).
- **Hosting platform (production)** — TODO: confirm where farwestlegacy.com is actually hosted and document the deploy path.
- **Tailscale** — used for MacBook demo access from Dell (`100.68.44.127:8081`).
