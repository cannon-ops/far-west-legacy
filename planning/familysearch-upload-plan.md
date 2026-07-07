# FamilySearch Profile-Upload Integration Plan (FWL-006)

**Status:** DESIGN — no code exists for any of this yet.
**Scope:** the path from FWL's approved extraction JSON to person profiles in the FamilySearch
Family Tree (beta), with the obituary attached as a source citation.
**Author:** A13 (Claude Code, Fable), 2026-07-07. Analysis-only session; this doc is the sole output.

---

## 0. Where we actually are today

Verified against the repo this session:

| Piece | State |
| --- | --- |
| Extraction | Working. `src/extract.py` (Claude Haiku) → JSON per `docs/data_schema.md`. |
| Review UI | Working. `/tool` → `POST /extract` → `tmp/<uuid>.json` → `GET /review/<id>` (editable form) → `POST /approve/<id>` → `output/<Surname_Given>.json`. |
| FamilySearch auth | **Not started.** Zero FS code in `src/`. What exists: `.env` values (`FAMILYSEARCH_CLIENT_ID` beta AppKey, `FAMILYSEARCH_REDIRECT_URI=http://localhost:8081/callback`, `FAMILYSEARCH_ENV=beta`) and a commented-out `authlib` line in `requirements.txt`. |
| FS relationship | Beta AppKey issued via Gordon Clarke (clarkegj@churchofjesuschrist.org). Realm `farwestlegacy.com` registered. Compatibility Review still required before production writes. |

Confirmed constraints (vault `Projects/FWL/FamilySearch-API-Notes.md` + `.env`):

- **OAuth2 authorization-code flow only.** No client-credentials, no unauthenticated sessions.
- **Public client.** AppKey = `client_id`; there is no secret. (The `FAMILYSEARCH_CLIENT_SECRET`
  line in `.env`/`.env.example` is vestigial — token exchange for a public client omits it.
  Leave it unused; remove in the auth-spike session.)
- **Beta identity server:** `identbeta.familysearch.org`. Registered redirect:
  `http://localhost:8081/callback`.
- **User-in-the-loop is architectural, not a preference.** A human signs into FamilySearch;
  FWL acts on that person's session. A headless batch daemon is impossible under this grant.
- **The beta key's granted purpose IS building trees from extracted people/relationships.**
  Design freely against beta; production Family Tree is a certification gate (Milestone 3
  boundary), not a design input here.
- **Exact OAuth scope names: unknown.** Capture them during the first live auth handshake
  (log the authorization request and token response). Do not block design on them.

Stale-doc note (do not fix this session): `ARCHITECTURE.md` still says the write target is
`integration.familysearch.org` (sandbox). The actual grant is **beta**. Correct it when
Milestone 2 code lands.

Deployment reality check: the registered redirect is `localhost:8081`, so the upload feature
initially runs **on the Dev machine only**. The Render production site cannot complete OAuth
until a `https://farwestlegacy.com/callback` redirect is registered with FamilySearch (ask
Gordon — agenda item below). Until then, gate all upload routes behind
`FWL_FS_UPLOAD_ENABLED` (default off) so prod deploys are unaffected. Also note Render runs
gunicorn with 2 workers — an in-memory token store won't survive worker routing there; that's
fine locally (single Flask process) and is a solved problem (server-side session store) by the
time prod matters.

---

## 1. Step 0 — Gordon Clarke orientation call

FamilySearch offered an API orientation call. **Schedule it before writing auth code** — it's
the warm channel and will collapse several "verify in docs" items below into direct answers.

Agenda (bring this list):

1. Exact OAuth scope names for tree read/write + source create under our beta key.
2. Does the beta identity server support/require **PKCE** for public clients? (We'll implement
   PKCE S256 by default; confirm it's accepted.)
3. Beta tree hygiene: is the beta Family Tree periodically reset? Is there a designated
   sandbox/special tree we should target for repeated test writes?
4. Adding a production redirect URI (`https://farwestlegacy.com/callback`) to the key — what's
   the process, and does it require the Compatibility Review first?
5. Sibling modeling: confirm siblings are expressed only via shared child-and-parents
   relationships (no direct sibling relationship type).
6. Living-person writes: confirm private-space behavior for living relatives created via API.
7. Compatibility Review / certification path and criteria for production (Milestone 3 scoping).
8. Token/session lifetime for the auth-code flow; is a refresh token issued or is re-login the
   expected pattern?

---

## 2. Field mapping: FWL extraction JSON → FamilySearch structures

FamilySearch's tree API speaks **GEDCOM X JSON** (`application/x-gedcomx-v1+json`) with FS
extensions (`application/x-fs-v1+json`) for child-and-parents relationships. All mappings
below live in one pure, unit-testable module (`src/fs_map.py` — no network, no Flask), so the
whole layer is testable offline.

### 2.1 `deceased` → Person

| FWL field | GEDCOM X target | Notes |
| --- | --- | --- |
| `given_names` | `names[0].nameForms[0].parts[]` type `Given` | Preferred name. |
| `surname` | same nameForm, part type `Surname` | Surname at death (married name for women). |
| `suffix` | same nameForm, part type `Suffix` | Omit part if empty. |
| `maiden_name` | **second** `Name` of type `BirthName` (given names + maiden surname) | Only when non-empty. Preferred name keeps the married surname; the birth name carries the maiden surname. Verify FS's preferred convention on the Gordon call. |
| `gender` | `gender.type` = `http://gedcomx.org/Male` / `Female` / `Unknown` | |
| `birth_date` + `birth_place` | Fact type `http://gedcomx.org/Birth` | Date: `original` = human string, `formal` = `+YYYY[-MM[-DD]]` (GEDCOM X formal dates accept partials — our `YYYY-MM` / `YYYY` map directly). Place: `original` string only; let FS's place authority normalize. Omit the fact entirely if both date and place are empty. |
| `death_date` + `death_place` | Fact type `http://gedcomx.org/Death` | Same date/place rules. An obituary virtually always yields at least a death fact. |
| `burial_place` | Fact type `http://gedcomx.org/Burial` | Place only; obits rarely give burial dates. |
| — | `living: false` | Always false for the subject — it's an obituary. |

### 2.2 `relationships` → relatives + relationship resources

Each named relative becomes (at most) a **minimal person** — name parts, gender if inferable
from relationship type, `deceased` flag — plus one relationship resource:

| FWL array | FamilySearch structure |
| --- | --- |
| `spouses[]` | **Couple Relationship** (subject ↔ spouse). |
| `parents[]` | **Child-and-Parents Relationship** with the subject as child, extracted parents as parent1/parent2. One CPR holding both parents when both are named. |
| `children[]` | One **Child-and-Parents Relationship per child**, subject as a parent. **Do not guess the other parent.** An obit listing a spouse and children does not prove that spouse is those children's parent. v1: create the CPR with the subject as sole listed parent; the reviewing human can complete it on FamilySearch.org. (Revisit as a UI option — "other parent" dropdown — in a later milestone.) |
| `siblings[]` | **No direct sibling relationship type exists in the FS tree** — siblings share a child-and-parents relationship. Create a sibling in the tree **only if the subject's parents were also created/matched**: add the sibling as another child of the same CPR parents. If no parents are in the data, siblings are *not* written to the tree — they remain in the source citation text only. The review UI must say this explicitly so the user isn't surprised. |

**Living-relative policy (privacy call — make it deliberately):** obituaries name living
spouses/children/siblings. FamilySearch stores living persons in the contributing user's
private space (invisible to others), so writing them isn't a public leak — but it still
creates records the user may not expect. **Default: only the subject and relatives flagged
`deceased: true` are written.** Each living relative gets an opt-in checkbox in the upload
UI, default unchecked, labeled with the private-space explanation. Confirm private-space
behavior on the Gordon call (agenda item 6).

### 2.3 The obituary as a Source — first-class requirement

Every upload creates **one Source Description** and attaches references to **everything the
obituary supports**: the subject person, every created/matched relative, and every created
relationship. A person landed in the tree by FWL must never be citation-less.

Source Description fields:

- `about`: `source_url` (when present).
- `titles`: `"Obituary of {Given} {Surname} ({birth_year}–{death_year}), {site or 'newspaper clipping'}"`.
- `citations`: human-readable citation string — site/paper name, URL, access date, "obituary".
- `notes`: the obituary text itself (`raw_text`), so the evidence survives link rot. Verify
  length limits; truncate with ellipsis + URL if needed.

**Pasted-text case (no `source_url`):** still create the Source Description — citation +
`raw_text` note, no `about` URL. Ask the user for a one-line provenance string ("Where is this
obituary from?") on the upload screen; don't invent one. A later milestone can upload the text
(or clipping scan) as a Memories document and point the source at it.

Attachment: create the Source Description once, then POST a source reference to each
person/relationship created. Tag references with what they support (name/birth/death) if the
API supports fact-level tagging — verify in Person Source References docs; person-level
attachment is the acceptable v1 floor.

### 2.4 Fields that do NOT upload

- `eulogy_text` → future Memories "story" attached to the subject (Milestone 2.4). Not a fact.
- `service_details` → stays local. No tree representation; funeral logistics aren't genealogy.
- `raw_text` → uploads only inside the Source Description note (2.3).

---

## 3. Duplicate handling — search before create, human gate always

Hard rule (already in CLAUDE.md, now made concrete): **no person is ever created while a
plausible existing match is on screen unattended. The human picks.**

### 3.1 Mechanics

After the existing `/approve/<id>` step (which stays exactly as-is), a new **match-check
stage** runs before any write:

1. For the **subject**: query **Person Matches by Example** (POST, GEDCOM X body built from
   the mapped person — name, birth, death, plus parent/spouse names as context), which returns
   scored candidates. Fall back to / cross-check with **Tree Person Search** (GET, query params
   `q.givenName`, `q.surname`, `q.birthLikeDate`, `q.deathLikeDate`, `q.fatherSurname`, etc.).
   Verify both request shapes in the docs (§8).
2. For **each relative that will be written** (deceased-only by default): same search, with the
   subject as relational context (e.g. sibling search includes parent names).
3. Candidates are bucketed by the API's returned score into **Strong / Possible / Weak**
   (thresholds tuned empirically against beta during M2.2 — record chosen cutoffs in
   repo-memory.md when set).

### 3.2 Review-UI treatment (new screen: `GET /upload/<job_id>`)

Per person, a match panel showing: candidate name, lifespan, PID, top matching facts summary,
and confidence bucket — **with a link out to the person page on FamilySearch (beta) for
detail**. Per the API terms rule already in CLAUDE.md, full record detail opens on
FamilySearch.org; the in-app panel shows only the search-result summary.

Per-person decision — exactly one must be chosen, no default when a Strong or Possible match
exists:

- **Use existing** — radio-select a candidate PID. FWL will *attach the source* to that person
  (and use the PID in relationships). v1 never edits a matched person's existing facts — no
  overwrites, no merges. Fact-enrichment of matched persons is a later milestone.
- **Create new** — enabled as pre-selected default **only when zero Strong/Possible matches**
  came back; otherwise it requires an explicit click.
- **Skip** — don't write this person at all (relationships involving them are also skipped;
  the UI shows the cascade before commit).

The commit button stays disabled until every person has a decision. This is the human-confirm
gate; there is no code path from extraction to a write that bypasses this screen.

---

## 4. Write sequence, errors, dry-run

### 4.1 Client layer

`src/fs_client.py` — a thin `FamilySearchClient` owning: base URLs by env
(`FAMILYSEARCH_ENV=beta` → identity `identbeta.familysearch.org`, API base for beta per the
environments doc — verify exact host, §8), auth token, GEDCOM X media types, retry/backoff,
and the **dry-run boundary**. All FS traffic goes through `client.send(req)`; nothing else in
the codebase touches the network. Use `httpx` (already pinned in requirements); `authlib`
optional — the auth-code flow is small enough to hand-roll, decide in M2.0.

### 4.2 Sequence per upload job

```
0. Auth check      — valid session? else redirect to /auth/login (§5)
1. Match check     — reads only (§3); user decisions recorded in the journal
2. Create persons  — subject first, then relatives (create-or-use-PID per decision)
3. Relationships   — couple, then child-and-parents (needs PIDs from step 2)
4. Source          — create Source Description once
5. Attach          — source reference to every person + relationship from steps 2–3
6. Summary screen  — table of everything written, each row linking to the
                     person/relationship on FamilySearch beta
```

**Upload journal (idempotency):** `tmp/<uuid>.upload.json`, written before and after every
intended call: `{step, method, url, body_digest, status, resulting_pid, ts}`. Every write step
checks the journal first — a re-run after a mid-sequence failure **resumes** (skips completed
steps, reuses PIDs) instead of double-creating people. This is the difference between "retry
safely" and "created Grandma twice."

### 4.3 Error / rate-limit handling

Confirmed live from the FS throttling doc (developers.familysearch.org/main/docs/throttling):
throttling is **per-user** (processing-time budget per window, shared across all products the
user has open), signaled by **HTTP 429 + `Retry-After` seconds**; every response carries
`X-PROCESSING-TIME` (ms); 503 may also carry `Retry-After`; test endpoint
`GET /platform/throttled?processingTime=N` exists for exercising the path.

| Condition | Behavior |
| --- | --- |
| 429 / 503 | Honor `Retry-After`, exponential backoff on repeats, cap ~3 retries, then surface "FamilySearch is throttling us — resume in a minute" with the journal intact for resume. |
| 401 | Session expired → save journal, bounce to `/auth/login`, resume after re-auth. |
| 4xx validation | No retry. Log full response body, show the user which step/person failed, halt sequence. |
| Network error | Retry GETs (search) with backoff; **never blind-retry a POST** — on reconnect, check the journal / query whether the write landed before resending. |
| Always | Log `X-PROCESSING-TIME` per call into the journal (cost visibility). House rule applies: no bare `except`; every handler logs `exc_info=True` or re-raises. |

Pace bulk-ish sequences (a big obit can be ~15 writes) with a small inter-write delay and
budget awareness from `X-PROCESSING-TIME` — the per-user budget is shared with the user's own
FamilySearch browser tabs.

### 4.4 DRY-RUN mode

`FWL_FS_DRY_RUN=1` (env) **or** a visible toggle on the upload screen — either engages it;
env var wins and cannot be overridden from the UI.

- Implemented **at the `client.send()` boundary**: read-only calls (auth, search/match GETs)
  execute for real so match results are realistic; any POST/PUT/DELETE is **not sent** —
  instead the full method + URL + serialized body is written to the journal and app log, and a
  synthetic PID (`DRYRUN-P001`, …) is returned so the rest of the sequence proceeds normally.
- Output: the same summary screen, banner "DRY RUN — nothing was written", plus the journal as
  a reviewable "intended writes" record. This is also the demo mode for stakeholders and the
  acceptance artifact for M2.1 (which ships before any live write exists).

---

## 5. Auth design (user-in-the-loop, public client)

Flask additions, all in a new `src/fs_auth.py` + two routes:

1. `GET /auth/login` — build authorization URL on `https://identbeta.familysearch.org`
   (endpoint path per Authorization resource docs — verify, §8) with `response_type=code`,
   `client_id` (AppKey), `redirect_uri=http://localhost:8081/callback`, random `state`, and
   PKCE S256 `code_challenge` (pending confirmation FS accepts PKCE; if not, plain auth-code —
   still no secret). Redirect the browser there; the human signs in with their FamilySearch
   (beta) account.
2. `GET /callback` — verify `state`, exchange `code` at the token endpoint
   (`grant_type=authorization_code`, `client_id`, `redirect_uri`, `code_verifier`; **no
   secret** — public client). Store the access token **server-side** (module-level store keyed
   by Flask session id — fine for single-process local dev; never in the cookie). Fetch
   current-user display name (Current Tree Person / current-user resource — verify, §8) and
   show a signed-in badge in the header.
3. **Scope names:** log the exact scopes requested/granted during this first handshake and
   record them in repo-memory.md — this is the confirmation point promised in the constraints.
4. Expiry: FS sessions are finite; a 401 mid-sequence triggers journal-save → re-login →
   resume (§4.3). Whether a refresh token is issued is Gordon-call agenda item 8; design
   assumes re-login is the norm.

Because the human's own FamilySearch account is the actor, every write is attributed to them
in FamilySearch's change history — which is exactly the accountability model FamilySearch
wants and why the review/confirm gates above are non-negotiable product behavior, not just
API-terms compliance.

---

## 6. Milestones (fixed-bid-sized, mapped to the Matthew Johnson engagement)

Each chunk is independently demoable and billable; each has a hard acceptance test. Sizes are
relative t-shirt sizes for bid framing, not hour quotes.

| # | Chunk | Contents | Acceptance | Size |
| --- | --- | --- | --- | --- |
| **M2.0** | Orientation + auth spike | Gordon call (§1) done, answers recorded in repo-memory. `/auth/login` + `/callback` working against identbeta; signed-in badge; scope names captured; vestigial secret removed from `.env.example`. | Human signs in on Dev, badge shows their FS name; scopes documented. | S |
| **M2.1** | Mapping + client + dry-run | `fs_map.py` (pure GEDCOM X mapping, full unit tests incl. partial dates, maiden names, sibling gating), `fs_client.py` with journal + dry-run. No live writes exist yet. | Dry-run of the Neese fixture produces a correct, reviewed intended-writes journal; tests green. | M |
| **M2.2** | Match check + confirm gate | Live search/match reads against beta; `GET /upload/<id>` screen with confidence buckets, link-outs, Use-existing/Create-new/Skip decisions; commit disabled until all decided. | Browser test on Dev against beta: a seeded near-duplicate is surfaced and auto-create is impossible. | M |
| **M2.3** | Live writes to beta | Person + relationship creation, source description + attachments, resume-from-journal, throttle handling, summary screen with beta links. | An approved fixture obit lands in the beta tree with source attached to every created node; kill-and-resume mid-upload creates no duplicates. | L |
| **M2.4** *(optional)* | Enrichment | Eulogy → Memories story; pasted-text provenance flow; "other parent" selection for children; fact-level source tagging. | Story visible on beta person; each feature browser-verified. | M |
| **M3** | **Production gate** | FamilySearch Compatibility Review / certification, production redirect URI, key promotion, Render-side auth (multi-worker token store). | FWL writes to the real Family Tree. **Separate engagement — not bid inside M2.** | — |

Dependency chain is linear: M2.0 → M2.1 → M2.2 → M2.3 (→ M2.4). M2.1's dry-run demo is the
natural first Matthew Johnson show-and-tell — it proves the whole pipeline shape with zero
risk to anyone's tree.

---

## 7. Test strategy notes

- `fs_map.py` is pure → exhaustive unit tests, no mocks: partial dates (`1939`, `1939-07`),
  empty facts omitted, maiden-name double-name, suffix handling, sibling gating on
  parents-present, living-relative default exclusion.
- `fs_client.py` → tests with a fake transport (httpx `MockTransport`): journal write/resume,
  429 + `Retry-After` honored, 401 triggers re-auth path, dry-run blocks non-GETs.
- Fixture-driven end-to-end dry-run using `tests/fixtures/sample_obituary_01.txt` (Neese) —
  golden-file assert on the intended-writes journal.
- Live-against-beta checks stay manual browser smoke tests per house rules (Bug
  Reproduce-First / browser-verify), documented per milestone acceptance above.

---

## 8. FamilySearch docs to verify at implementation time (do not code from memory)

Docs base: **https://developers.familysearch.org/** (the old
`familysearch.org/developers/docs/...` URLs 301-redirect there — verified this session).
Resource names below verified against the live API-resources index this session; request/
response shapes must be read at build time:

| Topic | Doc section (resource name) | What to verify |
| --- | --- | --- |
| OAuth authorize | **Authorization resource** (authentication) | Exact authorize path on identbeta, params, PKCE support. |
| OAuth token | **Access Token resource** (authentication) | Token exchange params for public client (no secret), token lifetime, refresh behavior. |
| Auth overview | **Authentication guide** | Environment hostnames (beta API base), session semantics. |
| Create person | **Persons resource** (tree, POST) | GEDCOM X body shape, `living` flag, media type, Location-header PID return. |
| Read person | **Person resource** (tree) | For post-create verification + attach-to-existing reads. |
| Search | **Tree Person Search resource** (tree, GET) | `q.*` params, result feed shape, score field. |
| Match | **Person Matches by Example resource** (tree, POST) | Request body, confidence/score semantics — this drives §3 thresholds. |
| Existing-PID match | **Match by Tree Person Id resource** | Possible post-create duplicate check. |
| Couple | **Couple Relationship resource** (tree) | Create semantics (collection endpoint for POST), body shape. |
| Parent/child | **Child-and-Parents Relationship resource** (tree) | FS extension media type `application/x-fs-v1+json`, parent1/parent2 body. |
| Source create | **Source Descriptions resource** (sources, POST) | `about`/titles/citations/notes fields, length limits. |
| Source attach | **Person Source Reference(s) resource** (tree) | POST shape, fact-level tagging support. |
| Memories (M2.4) | **Memories resource** (memories, POST) | Story/document upload, attach-to-person flow. |
| Throttling | **Throttling doc** (developers.familysearch.org/main/docs/throttling) | ✅ Already verified live this session: per-user budget, 429 + `Retry-After`, `X-PROCESSING-TIME`, `/platform/throttled` test endpoint. |

---

## 9. Open questions (tracked, not blocking)

1. Scope names — resolved at first M2.0 handshake.
2. PKCE support on identbeta — Gordon call; fallback is plain auth-code.
3. Beta tree reset cadence / designated test tree — Gordon call.
4. Production redirect URI process — Gordon call; gates M3 planning only.
5. Match-score thresholds for Strong/Possible/Weak — tuned empirically in M2.2.
6. Source Description notes length limit vs. full `raw_text` — check at M2.3 build.
7. Living-person private-space confirmation — Gordon call agenda item 6.
