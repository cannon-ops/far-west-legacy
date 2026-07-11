# FWL ↔ FamilySearch API Integration — DRAFT

**Status:** DESIGN DRAFT — for Joel's review. No integration code exists.
**Scope:** the full FWL ↔ FamilySearch integration: auth, environments, API surface, data
mapping, rate limits, certification path, and how both input channels (text today, scans per
the companion doc) land in the Family Tree.
**Author:** A13 (Claude Code, Fable), 2026-07-10 overnight design session. API facts below
were re-verified against official docs this session (sources in §12); no live API calls made.
**Relationship to FWL-006** (`planning/familysearch-upload-plan.md`, 2026-07-07): FWL-006
remains the implementation plan of record for the upload UX, write sequence, journal, and
milestones. This doc is the layer under it — verified API ground truth plus system
architecture — and **resolves several items FWL-006 §8 deferred to "verify at implementation
time."** Deltas from FWL-006's assumptions are marked **[Δ]**. Where the two disagree, this
doc is newer.
**Companion doc:** `docs/obituary-pipeline-DRAFT.md` (scanned input; its M3.5 lands here §9).

---

## 1. System architecture

```
                       FWL (Flask, Dev machine first)
┌──────────────────────────────────────────────────────────────────┐
│  INPUT CHANNELS                 EXTRACTION          REVIEW       │
│  paste / URL  ──────────────► extract.py ──► /review UI ──┐      │
│  scan (M3, companion doc) ──► transcribe → extract ───────┤      │
│                                                           ▼      │
│                                            approved JSON (v2)    │
│                                                           │      │
│  FAMILYSEARCH LAYER (Milestone 2)                         ▼      │
│  ┌─────────────┐  ┌────────────┐  ┌───────────────────────────┐  │
│  │ fs_auth.py  │  │ fs_map.py  │  │ /upload/<job> match+confirm│ │
│  │ OAuth2+PKCE │  │ JSON→GEDX  │  │ screen (FWL-006 §3)        │ │
│  └──────┬──────┘  └─────┬──────┘  └────────────┬──────────────┘  │
│         └───────────────┴──────────────────────┘                 │
│                         │ fs_client.py                           │
│                         │ (single network boundary: journal,     │
│                         │  dry-run, retry/backoff, media types)  │
└─────────────────────────┼────────────────────────────────────────┘
                          ▼
        identbeta.familysearch.org   (OAuth authorize + token)
        apibeta.familysearch.org     (all /platform/* calls)
```

Module layout is FWL-006 §4–5 unchanged: `fs_auth.py`, `fs_map.py` (pure), `fs_client.py`
(only network owner), plus the match/confirm screen. This doc pins down what those modules
talk to and say.

## 2. Environments & credentials

| | Identity server | API base | FWL status |
| --- | --- | --- | --- |
| Integration | `identint.familysearch.org` *(unverified — confirm)* | `api-integ.familysearch.org` | Not our track — we were granted beta directly. |
| **Beta (ours)** | `identbeta.familysearch.org` | **`apibeta.familysearch.org`** **[Δ resolved]** | AppKey issued via Gordon Clarke; purpose on the grant: building special trees from FWL-extracted people/relationships. Redirect `http://localhost:8081/callback`; realm `farwestlegacy.com`. |
| Production | `ident.familysearch.org` | `api.familysearch.org` | Gated on Compatible Solution Program + Compatibility Review (§10). Per-environment app keys — beta key does not carry over. |

Constraints from the grant (vault `FWL/FamilySearch-API-Notes.md`, unchanged):
**public client** (AppKey = client_id, no secret — the `FAMILYSEARCH_CLIENT_SECRET` env var
is vestigial, remove in M2.0); **authorization-code flow only** — no unauthenticated
sessions, no client credentials for our use. User-in-the-loop is architectural.

Publicity clause applies to this doc's descendants: "uses the FamilySearch API," never
"partnered with."

## 3. Auth design

Flow (per FWL-006 §5, now with verified endpoints and lifetimes):

1. `GET /auth/login` → redirect to
   `https://identbeta.familysearch.org/cis-web/oauth2/v3/authorization`
   with `response_type=code`, `client_id`, `redirect_uri`, `state`, PKCE S256
   `code_challenge`, `scope=openid` (+ `offline_access` if we adopt refresh tokens, below).
2. `GET /callback` → verify `state`, then `POST …/cis-web/oauth2/v3/token` with
   `grant_type=authorization_code`, `code`, `redirect_uri`, `client_id`, `code_verifier`.
   **[Δ resolved] PKCE is supported** — the token endpoint documents `code_verifier`.
   Response `token_type` is the nonstandard `"family_search"`; send the token as
   `Authorization: Bearer <token>` regardless.
3. Store token server-side only (FWL-006 §5 unchanged); fetch current user for the
   signed-in badge; log granted scopes to repo-memory (still the confirmation point).

**Token lifecycle [Δ new facts]:** access tokens expire after **24 hours**, or after
**60 minutes of inactivity**; expiry surfaces as 401. This makes FWL-006's 401 → save
journal → re-login → resume path (§4.3 there) the *common* path for a user who walks away
mid-review, not an edge case — the upload screen should survive re-auth without losing
decisions (it does, via the journal; just noting it's mainline).

**Refresh tokens [Δ changes a Gordon-call question]:** they exist — 90-day validity — but
require (a) the app key to be *enabled* for refresh tokens by devsupport, and (b)
`offline_access` in scope. FWL-006 assumed "re-login is the norm"; the design still works
that way by default, but the Gordon call should now *ask for refresh-token enablement*
rather than ask whether refresh exists. v1 recommendation: ship without refresh (re-login is
acceptable for an interactive tool); request enablement if archivist-batch sessions (M3.4)
prove longer than 24 h.

## 4. API surface (verified endpoint map)

All paths relative to `https://apibeta.familysearch.org`. This resolves most of FWL-006 §8.

| Step | Call | Notes |
| --- | --- | --- |
| Create person | `POST /platform/tree/persons` | 201 + `Location` header with PID. `X-Reason` header optional on create, **required on update/delete** — send it always (one code path). |
| Read person(s) | `GET /platform/tree/persons/{pid}`; batch `?pids=` up to 200 | 301 = merged (follow), 410 = deleted. |
| Duplicate search | `GET /platform/tree/search` | `q.givenName`, `q.surname`, `q.birthLikeDate`, `q.deathLikeDate`, `q.spouseSurname`, `q.fatherGivenName`, … `count` 1–100 (default 20). Response is GEDCOM X **Atom** (`application/x-gedcomx-atom+json`) — result-feed parser needed, distinct from entity parsing. |
| Duplicate match-by-example | `POST /platform/tree/matches` | Body: GEDCOM X doc, primary person with `id`, main SourceDescription `about="#<that id>"`. Params: `confidence` **1–5** [Δ: FWL-006's Strong/Possible/Weak buckets map onto this 5-level scale rather than a raw float — calibrate in M2.2], `count` (default 5). |
| Couple relationship | `POST /platform/tree/relationships` | `"type": "http://gedcomx.org/Couple"`, `person1`/`person2` refs. 201 + `X-entity-id`. Managed later at `/platform/tree/couple-relationships/{id}`. |
| Child-and-parents | `POST /platform/tree/child-and-parents-relationships` | FS extension — **must** use `application/x-fs-v1+json`. `parent1`/`parent2`/`child`; parent slots are generic (not gendered) per the generic-relationships update. `X-Reason` on POST/DELETE. |
| Source description | `POST /platform/sources/descriptions` | 201 + Location → SDID. Fields per FWL-006 §2.3. |
| Attach source | `POST /platform/tree/persons/{pid}` with a `sources` array | Each source reference needs **`attribution.changeMessage`** [Δ new requirement — add to `fs_map.py` output] and supports tags for which conclusions it backs (Name/Birth/Death) — fact-level tagging confirmed available. Same pattern on relationships via their `/source-references` sub-resource. |
| Memory upload | `POST /platform/memories/memories` | `multipart/form-data`; `type` param — **an explicit `Obituary` artifact type exists** [Δ — use it; companion doc §8.2]. Also `Photo`/`Document`/`Story`. |
| Attach memory | `POST /platform/memories/memories/{mid}/personas` | Persona references the tree PID. |
| Record hints | `GET /platform/tree/persons/{pid}/matches?collection=records` | Requires certification in production; beta behavior TBD. Keep to the existing rule: link out to FamilySearch.org, never render full records. |
| Throttle test | `GET /platform/throttled?processingTime=60001` | Exhausts our window on purpose — M2.3's 429-path test. |

**Media types:** send `Accept` explicitly on every call (suffix negotiation is deprecated);
writes use `application/x-fs-v1+json` (safe superset — required for child-and-parents,
accepted everywhere); search/match reads return `application/x-gedcomx-atom+json`. Send a
versioned `User-Agent` (`FarWestLegacy/<version>`), which FamilySearch asks for and which
`src/version.py` already supplies.

**Supporting authorities:** `GET /platform/places/search?q=name:…` (standardized
PlaceDescriptions) and the Dates authority (original → formal + normalized). Optional for
v1 (§5), useful later.

## 5. Data mapping notes (delta over FWL-006 §2)

FWL-006 §2's field mapping stands. Verified additions:

- **Name forms:** one `Name` may have multiple `nameForms` only for renderings of the *same*
  name (scripts/transliterations) — never nickname/maiden variants. FWL-006's choice of a
  **second `Name` of type `BirthName`** for maiden names is the correct structure.
- **Dates — searchability wrinkle [Δ, matters]:** FamilySearch stores original + formal +
  *normalized* date values, and **dates without normalized values are not searchable**.
  Sending `original` alone triggers server-side auto-normalization (unless
  `skipNormalization=true`), while sending `formal` without normalization can leave a
  less-searchable fact. v1 policy: send `original` (human string) **and** `formal`
  (`+YYYY[-MM[-DD]]` from our ISO partials), let FS normalize, and in the post-create
  verification read (FWL-006 M2.3) assert the normalized value came back. If it didn't,
  that's a lint on the upload summary. The Dates authority is the fallback if
  auto-normalization proves unreliable on beta.
- **Places:** `original` string only in v1 (FWL-006's call, confirmed sound). A M2.4+
  enhancement: `GET /platform/places/search` to offer the reviewer a standardized place
  pick; never auto-select.
- **Change messages:** every source reference carries `attribution.changeMessage`; every
  person write carries `X-Reason`. Template: `"Obituary of {name}, {citation}; extracted by
  Far West Legacy, reviewed by user."` These are FamilySearch's audit-trail hooks — cheap
  for us, heavily weighted in review (§10).
- **Living relatives:** private-space behavior confirmed in the docs (living persons visible
  only to the contributing user; duplicated across users' spaces by design; merged after
  death). FWL-006's deceased-only default with opt-in checkboxes stands. **One caution
  [Δ]:** web apps "may not store any living-person data" *read from the API* — FWL never
  reads living persons, but M2.2's match results could theoretically surface some; don't
  persist match-result payloads beyond the session, which the current design already
  satisfies (journal stores decisions + PIDs, not candidate records).

## 6. Rate limits & resilience

Confirms and slightly extends FWL-006 §4.3 (which is already correct):

- Throttling is **per-user processing-time budget per window** — no published numeric
  quotas exist, so there is nothing to hardcode; behavior-based handling is the only design.
- **429 + `Retry-After` (seconds)** is the throttle signal; 503 + `Retry-After` still
  possible — honor identically. `X-PROCESSING-TIME` on every response feeds the journal's
  cost column.
- The budget is shared across everything the user has open, *including their own
  familysearch.org browser tabs* — which the match/confirm screen actively encourages
  opening (link-outs). Expect mid-upload throttling as normal; the journal-resume design is
  the mitigation, not an edge case.
- One obituary ≈ 10–20 writes (persons + relationships + source + attachments): pace with
  a small inter-write delay; never parallelize writes for one job. This is nowhere near
  bulk-scale and is the usage pattern the review process expects (§10).

## 7. Duplicate detection & the certification lens

FWL-006 §3's search → bucketed candidates → mandatory human decision design is not just
good practice — **a programmatic duplicate check with a user-facing "possible duplicates"
affordance is an explicit Compatibility Review checklist item** for write apps. Two
refinements from the verified API:

1. Use **both** probes as designed: `POST /tree/matches` (by-example, scored, `confidence`
   1–5) as primary; `GET /tree/search` as the wide net. Bucket mapping: confidence 4–5 →
   Strong, 3 → Possible, ≤2 → Weak. Calibrate on beta in M2.2 and record cutoffs.
2. Match *resolution* (accept/not-a-match POST) requires certification — FWL v1 never
   resolves matches; it only attaches to an existing PID or creates new. That keeps us
   outside the certified-operations surface. Redirecting users to FamilySearch's own
   Possible Duplicates page satisfies the checklist's merge-handling expectation.

## 8. What FWL never does (design invariants)

Consolidated from the grant, API terms, and CLAUDE.md — these bound every milestone:

1. No write without an authenticated human's explicit per-person confirmation.
2. No unattended/batch writes — even in M3.4 batch *processing*, writes stay per-job,
   human-gated. (No written FS rule prohibits it, but the entire review framework assumes a
   user drives each change; treat as hard.)
3. No display of full record details in-app (link out); no persistence of API-read data
   beyond the session.
4. No Ordinance access, requested or referenced.
5. Beta only, until Compatibility Review passes — enforced by hostname config, and
   `FWL_FS_UPLOAD_ENABLED` (default off) keeps prod deploys inert meanwhile.
6. Every created node carries a source reference with changeMessage — nothing citation-less.

## 9. Scanned-obituary channel (joins here from the companion doc)

The scan pipeline changes *nothing* in §1–§8 — it produces the same approved JSON. It adds
one upgrade at the source step (companion doc §8.2, sequenced as M3.5 after M2.3):

1. `POST /platform/memories/memories` (multipart) with **`type=Obituary`**, the normalized
   scan image, title from citation data.
2. Persona-attach the memory to the subject PID.
3. Source Description `about` → the memory URI; citation string from `capture_meta`
   (newspaper, date, page, collection).
4. Portrait crop (when detected) → second memory, `type=Photo`.

Result: the tree person's source is the *actual clipping image*, inspectable by anyone —
materially stronger genealogy than a URL that will rot.

## 10. Production path (Milestone 3 gate — scoping only)

Verified sequence: Compatible Solution Program enrollment → **Compatibility Review**
(business + engineering review and a security check; write-access apps get periodically
re-audited) → production key (separate from beta) + production redirect URI + prod identity
host. Review criteria favor apps that grow Family Tree data through a meaningful
user-reviewed experience — FWL's whole design (§7, §8) is aimed down that fairway.
Engineering items the review will look at that we should build correctly the first time:
`User-Agent` versioning, `Accept` headers, Retry-After compliance, duplicate-check
affordance, changeMessage discipline, no living-data storage, cache purge behavior.
Still not bid inside M2 (FWL-006's call stands).

## 11. Phased plan (delta view over FWL-006 §6)

Milestone structure M2.0 → M2.4 is unchanged. Research-driven adjustments:

| Milestone | Adjustment from this session |
| --- | --- |
| **M2.0** Gordon call + auth spike | Agenda updates: ~~"does beta support PKCE?"~~ → confirmed, just implement S256. ~~"is a refresh token issued?"~~ → ask instead: *"enable refresh tokens on our key + `offline_access`?"* (decide lazily; not needed for v1). Add: *"is `type=Obituary` memories upload available on beta?"* and *"beta behavior of record-hint matches?"*. Endpoint archaeology items from FWL-006 §8 (authorize path, token params, API base) are resolved — strike them. |
| **M2.1** Mapping + client + dry-run | `fs_map.py` additionally emits `X-Reason` values and `attribution.changeMessage` on all source references (§5). Client sends `Accept` + versioned `User-Agent` on every call. Formal-date emission per §5. |
| **M2.2** Match + confirm gate | Bucket thresholds now defined against the documented 1–5 confidence scale (§7); calibrate, don't invent. Atom-feed parser for search results is a real (small) work item — it's a different media type than entity reads. |
| **M2.3** Live writes | Post-create verification read also asserts normalized dates came back (§5). Throttle-path test uses `/platform/throttled`. |
| **M2.4** Enrichment | Eulogy → `type=Story` memory; standardized-place picker (§5) joins this bucket as optional. |
| **M3.5** *(companion doc)* | Scan → `type=Obituary` memory + source `about` (§9). Depends on M2.3. |

## 12. Open questions

Carried forward (still open): beta tree reset cadence / designated test tree; production
redirect-URI process; exact scope names on our key (first-handshake capture); Source
Description notes length limit vs. full transcript text.

New from this session's research:
1. **Integration identity hostname** — irrelevant to us (we're on beta) unless FamilySearch
   ever asks us to demo on integration; confirm only if needed.
2. **Refresh-token enablement** — ask Gordon; adopt only if M3.4 batch sessions demand it.
3. **`type=Obituary` memory on beta** — confirm availability + any size limits (Gordon call
   or first M3.5 spike).
4. **Match-resolution certification on beta** — v1 avoids it entirely; only matters if a
   future milestone wants in-app "not a match" marking.

## 13. Key sources

- Auth guide: developers.familysearch.org/main/docs/authentication (+ initiate-authorization,
  getaccesstoken reference, oauth-20-for-native-apps)
- Throttling: developers.familysearch.org/main/docs/throttling
- Getting started / approval: developers.familysearch.org/main/docs/getting-started,
  …/app-approval-considerations, …/compatibility-checklist
- Resource reference (canonical per-endpoint): familysearch.org/en/developers/docs/api/…
  (Persons, Person, Tree Person Search, Person Matches by Example, Source Descriptions,
  Memories, Couple/Child-and-Parents Relationship resources)
- GEDCOM X JSON spec: github.com/FamilySearch/gedcomx …/json-format-specification.md;
  FS extensions: github.com/FamilySearch/gedcomx-familysearch-extensions
- Private spaces: developers.familysearch.org/main/docs/private-spaces-and-data-access-control
- SDK survey conclusion: no maintained Python write-capable client exists (python-fs-stack
  deprecated; getmyancestors scrapes login, unusable) → **raw REST via httpx** with plain-dict
  GEDCOM X payloads, using the JSON spec + gedcomx-java models as schema authority.
