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

- **Demo platform:** Production at `farwestlegacy.com` (Render). Demo from any browser, share the URL with stakeholders.
- **Demo samples:** `demo/sample_*.txt` — synthetic, anonymized obituaries. Paste into the production tool at `farwestlegacy.com/tool`.
- **MacBook deploy is deprecated** as of 2026-04-26. The launchd service, `start_mac.sh`, `copy_sample_mac.sh`, and `deploy/*` are kept for reference but are no longer maintained. See `repo-memory.md` Deployment Topology for historical detail.

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
| `src/app.py` | Flask app port 8081 (configurable via `FLASK_PORT`): `GET /` (marketing home), `GET /tool`, `POST /extract`, `GET /review/<id>`, `POST /approve/<id>` |
| `templates/home.html` | Marketing homepage at `/` (production landing page) |
| `render.yaml` | Render Blueprint — defines the production web service for `farwestlegacy.com` |
| `NOTES.md` | Tech-debt notes (cross-referenced in `repo-memory.md` Known Issues) |
| `prompts/obituary_extract.md` | System prompt for Haiku; defines schema + field rules |
| `docs/data_schema.md` | Full JSON schema reference |
| `ARCHITECTURE.md` | Data flow diagram, input channels, FamilySearch integration plan |
| `CHANGELOG.md` | Per-session change log |
| `repo-memory.md` | Single source of truth for current state (sessions, bugs, decisions, stakeholders). Updated every session close. |
| `scripts/begin.sh` / `scripts/begin.ps1` | Session-start: prints git status, last commit, test summary, last 30 lines of repo-memory.md |
| `scripts/close.sh` / `scripts/close.ps1` | Session-end: runs tests, prints git status, reminds to update repo-memory.md and CHANGELOG.md |
| `start_mac.sh` | _(deprecated 2026-04-26)_ macOS dev-mode Flask launcher — kept for reference, no longer maintained |
| `copy_sample_mac.sh` | _(deprecated 2026-04-26)_ macOS demo clipboard helper — kept for reference, no longer maintained |
| `deploy/` | _(deprecated 2026-04-26)_ MacBook launchd install/uninstall scripts and plist — kept for reference, no longer maintained |

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

Two-tier setup:

- **Dev (Dell Optiplex 3060, Windows)** — primary code-editing and test environment. All commits originate here. Flask runs on `0.0.0.0:8081` via `python -m src.app`.
- **Production (farwestlegacy.com)** — Render web service, free plan, Oregon. Blueprint at `render.yaml`. Auto-deploys on push to `main`. Marketing homepage at `/`, extraction tool at `/tool`. Also serves as the demo platform — share the URL with stakeholders.

The MacBook demo workflow (`start_mac.sh`, `copy_sample_mac.sh`, `deploy/install_mac.sh`, launchd service `com.farwestlegacy.app`) is **deprecated** as of 2026-04-26. Files kept for reference but unmaintained.

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
