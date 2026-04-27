# CLAUDE.md — Far West Legacy

Standing rules for Claude Code (Agent 13) sessions.

## Project

- **App:** Far West Legacy — open-source obituary → FamilySearch Family Tree tool
- **Entity:** Cannon Digital LLC (Managing Member: Joel Cannon)
- **License:** MIT
- **Domain:** farwestlegacy.com
- **Repo:** github.com/joelcannon/far-west-legacy

## Stack

- Python 3.12+
- Flask (web UI, port 8081)
- Anthropic Claude API (Haiku for text extraction, Sonnet for vision/photos)
- FamilySearch REST API (OAuth 2.0)
- pytest for testing

## Dev Environment

- **Primary dev:** Dell Optiplex 3060 (Windows)
- **Python:** 3.12+ with venv
- **Activate venv before running anything:** `.venv\Scripts\activate` (Windows)

## Demo Environment

- **Demo machine:** MacBook Air (Joels-MacBook-Air)
- **Path:** `~/projects/far-west-legacy` (note: lowercase `projects` on disk)
- **Port:** 8081 (set via `FLASK_PORT` env var)
- **Browser:** Chrome (system default)
- **Deployment:** launchd user-level service (see `deploy/README.md`)
  - Service label: `com.farwestlegacy.app`
  - Plist: `~/Library/LaunchAgents/com.farwestlegacy.app.plist`
  - Logs: `~/Library/Logs/far-west-legacy/flask.{log,err}`
  - Install:   `./deploy/install_mac.sh`
  - Uninstall: `./deploy/uninstall_mac.sh`
- **Scripts (macOS only):**
  - `./start_mac.sh` — dev mode: foreground Flask with debug=True, auto-restores launchd on exit
  - `./copy_sample_mac.sh [name]` — list samples or copy one to clipboard
- **Demo samples:** `demo/sample_*.txt` — synthetic, anonymized obituaries

## Session Protocol

1. Every session begins with `make test` (or `pytest`) — green before touching code.
2. Prompts are complete, ready-to-paste, capped at ~7 steps per sub-pass.
3. Lettered sub-passes for large sessions (e.g., 001a, 001b).
4. Always include explicit file verification steps — files may be reported as created but not actually written.
5. Surface all errors immediately. Never silently swallow exceptions.
6. Wally (Claude Chat) handles planning/architecture. Agent 13 (Claude Code) handles execution only.

## FamilySearch API Rules

1. **Beta first.** All development targets the FamilySearch **beta** environment (`FAMILYSEARCH_ENV=beta`). Never production until Compatibility Review is passed.
2. **User review is mandatory.** No FamilySearch write without explicit user confirmation of extracted data.
3. **Duplicate check before every write.** Search the tree before creating any person.
4. **Record hints open FamilySearch.org.** Never display full record details in the app (API terms requirement).
5. **No Ordinance access.** Not requested, not used, never referenced.

## Publicity Clause

The FamilySearch Solutions Agreement includes a publicity restriction:
- **DO:** Say "uses the FamilySearch API" or "contributes to the FamilySearch Family Tree"
- **DON'T:** Say "partnered with FamilySearch" or imply endorsement
- This applies to README, docs, website, emails, and all public-facing text.

## Secrets & Security

- `.env` at repo root holds all secrets: `ANTHROPIC_API_KEY`, `FAMILYSEARCH_CLIENT_ID`, `FAMILYSEARCH_CLIENT_SECRET`, `FAMILYSEARCH_REDIRECT_URI`, `FAMILYSEARCH_ENV`
- The current beta `FAMILYSEARCH_CLIENT_ID` (AppKey) lives in `.env`. Treat it like a credential — never paste into commits, prompts, or PRs.
- Never commit `.env`, `service_account.json`, or any credentials.
- `.env.example` is the template — committed, no real values.
- BYOK model: users supply their own Anthropic API key.

## File & Data Rules

- Never store real obituary data or personal information in the repo.
- Test fixtures use synthetic/anonymized data only.
- Never commit customer documents.

## Milestone Status

- **Milestone 1 (complete):** Extraction + CLI + Flask Review UI — 30 tests passing
  - `src/extract.py` — Claude Haiku extraction (`max_tokens=4096`)
  - `src/fetch.py` — URL fetch + BeautifulSoup parse
  - `src/cli.py` — `--text / --file / --url` CLI
  - `src/app.py` — Flask UI on port 8081 (paste → extract → review → approve)
  - `demo/` — synthetic demo obituaries (neese, veteran, amish)
  - `start_mac.sh`, `copy_sample_mac.sh` — macOS demo scripts
- **Milestone 2 (next):** FamilySearch OAuth + sandbox writes
- **Milestone 3 (future):** Photo/portrait handling, Sonnet vision OCR, production release

## Current File Manifest

| File | Purpose |
| --- | --- |
| `src/extract.py` | `extract_from_text()` — Claude Haiku, returns structured dict, raises `ExtractionError` |
| `src/fetch.py` | `fetch_obituary_text()` — HTTP GET + BS4 parse, raises `FetchError` |
| `src/cli.py` | CLI: `--text`, `--file`, `--url`; saves JSON to `output/` |
| `src/app.py` | Flask app port 8081 (configurable via `FLASK_PORT`): `GET /`, `POST /extract`, `GET /review/<id>`, `POST /approve/<id>` |
| `prompts/obituary_extract.md` | System prompt for Haiku; defines schema + field rules |
| `docs/data_schema.md` | Full JSON schema reference |
| `ARCHITECTURE.md` | Data flow diagram, input channels, FamilySearch integration plan |
| `CHANGELOG.md` | Per-session change log |
| `repo-memory.md` | Single source of truth for current state (sessions, bugs, decisions, stakeholders). Updated every session close. |
| `scripts/begin.sh` / `scripts/begin.ps1` | Session-start: prints git status, last commit, test summary, last 30 lines of repo-memory.md |
| `scripts/close.sh` / `scripts/close.ps1` | Session-end: runs tests, prints git status, reminds to update repo-memory.md and CHANGELOG.md |
| `start_mac.sh` | macOS dev-mode Flask launcher (stops launchd, runs foreground with debug, restores launchd on exit) |
| `copy_sample_mac.sh` | macOS demo helper: list `demo/sample_*.txt` or copy one to clipboard via `pbcopy` |

## Architecture

```
INPUTS
  ├── Pasted text         → direct
  ├── Obituary URL        → fetch.py (requests + BeautifulSoup)
  └── Photo/scan          → [future] Claude Sonnet vision

EXTRACTION  (Claude Haiku, max_tokens=4096)
  └── prompts/obituary_extract.md → structured JSON
      {
        "deceased": { given_names, surname, maiden_name, suffix,
                      gender, birth_date, birth_place,
                      death_date, death_place, burial_place },
        "relationships": {
          "spouses":  [{ given_names, surname, deceased }],
          "parents":  [{ given_names, surname, maiden_name, deceased }],
          "children": [{ given_names, surname, deceased }],
          "siblings": [{ given_names, surname, maiden_name, deceased }]
        },
        "eulogy_text": "...",
        "service_details": "...",
        "source_url": "...",
        "raw_text": "..."
      }

REVIEW UI  (Flask port 8081)
  └── User confirms / edits all fields before any FamilySearch write
      tmp/<uuid>.json  →  output/<Surname_Given>.json

FAMILYSEARCH API  [future — sandbox first]
  ├── OAuth 2.0 authentication
  ├── Duplicate check: search before write
  ├── Create person + relationships
  ├── Attach Story Memory (eulogy) and Photo Memory
  ├── Attach Source citation (source_url)
  └── Record hints → open FamilySearch.org (never display full record)
```

## Deployment

Three-tier setup:

- **Dev (Dell Optiplex 3060, Windows)** — primary code-editing environment. All commits originate here. Flask runs on `0.0.0.0:8081` via `python -m src.app`.
- **Demo / Local (MacBook Air)** — launchd service `com.farwestlegacy.app` on port 8081. Reachable over Tailscale at `100.68.44.127:8081`. Used for live demos.
- **Production (farwestlegacy.com)** — public-facing instance. Hosting platform / deploy process recorded in `repo-memory.md` (TODO until confirmed).

Details, deploy commands, and access notes live in `repo-memory.md`.

## Session Handoff

`repo-memory.md` is the **single source of truth** for current state — sessions, deployment topology, stakeholders, active bugs, deferred bugs, pending decisions, env vars, external dependencies. **It must be updated before every session close.**

- **Start of session:** run `scripts/begin.ps1` (Dell) or `scripts/begin.sh` (MacBook/Linux). Prints git status, last commit, test summary, and the last 30 lines of `repo-memory.md`.
- **End of session:** run `scripts/close.ps1` / `scripts/close.sh`. Re-runs tests (must be green), prints git status, reminds you to update `repo-memory.md` and `CHANGELOG.md` before the final commit.

## Stakeholders

High-level only — full names, status, and meeting dates live in `repo-memory.md`.

- **Joel Cannon / Cannon Digital LLC** — owner.
- **Daviess County Historical Society** — sponsor.
- **Mid-West Genealogy Center** — collaboration interest (potential library-patron rebrand).
- **Matthew Johnson** — potential paying customer (archivist team workflow).
- **FamilySearch / Gordon Clarke** — API partner.

## Key Contacts (do not commit to repo)

- FamilySearch Dev Support: devsupport@familysearch.org
- FamilySearch contact: Gordon Clarke (clarkegj@churchofjesuschrist.org)
