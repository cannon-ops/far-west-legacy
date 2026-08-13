# Far West Legacy — Changelog

All notable changes to this project are documented here.
Format: session number, date, milestone label, summary of changes.

---

## Session 013-H1 (2026-08-13) — Subject-First Search + Write-Sequence Reorder

Implements the FWL-012-H12 plan decision (`planning/familysearch-upload-plan.md` §3.3/§4.2)
in code for the first time — H11/H12 were recon and plan-doc updates only.

- **`src/app.py` `upload_match_check()`** — the subject is now searched first, on its own,
  before any relative. Parents/spouse are only searched directly (§3.3 step 3's fallback)
  when the subject comes back with no strong/possible match; when the subject IS found,
  their direct search is skipped and the match-check screen shows a `search_note`
  explaining why (record hint resolution is the intended mechanism, not certified yet —
  §3.4) instead of silently showing nothing. Children/siblings are never part of this
  gating and are always searched, as before.
- **`src/fs_client.py` `_run_upload_sequence()`** — reordered per plan §4.2: spouse/parent
  persons are created before the subject (they're the ones a not-found-subject's fallback
  search may already have resolved), and the subject's own couple/parent-CPR relationships
  are POSTed immediately after its creation rather than deferred to the separate
  relationships pass every other relationship still uses. Children/siblings (and their
  CPRs) still come after the subject, since those relationships need its PID.
- **`FamilySearchClient.post_create_relative_fallback()` (new)** — the degraded near-term
  form of plan §4.2 step 4 / §3.4: re-runs the same thin parent/spouse search once a
  subject PID exists, flagged `record_hint_status: "not_certified"` so a caller never
  presents it as a real record-hint result. **Deliberately not called from
  `run_upload_sequence()`** — that function is exercised by a fully offline dry-run golden
  test with no transport mock, and this (like `search_matches()`) is a live read regardless
  of `dry_run`; it also has no way yet to know whether the subject was newly created or
  attached this run, since decisions aren't threaded into the plan until M2.3. Built and
  tested as a standalone unit for M2.3's write route to call once that exists.
- **`run_upload_sequence()`/`_run_upload_sequence()` remain unwired to any route** —
  explicit decision, not an oversight: M2.3 (live writes) hasn't started, the decide route
  still only records decisions, and wiring a write-executing route today would bypass the
  still-unbuilt safety/UX work M2.3 is scoped to build (progress display, confirm-before-
  commit, resume UI). This session's reorder is prep work for that milestone.
- **Tests:** 212 passed, 9 skipped (up from 204+9) — 8 new: 3 covering write-sequence
  order, 3 covering `post_create_relative_fallback()`, 2 covering the subject-first search
  gating. `test_neese_dry_run_resumes_without_duplicate_creates` updated: the subject is
  now the third person created (`DRYRUN-P003`), not the first, since Neese's two parents
  are created before it.
- **Live-tested:** dev server started and confirmed listening (single `python.exe`
  process). Real OAuth sign-in requires a human with real FamilySearch credentials in a
  real browser (established FWL-010-H2) — an agent session cannot complete it, so
  `/upload/<job_id>` was confirmed to correctly redirect to `/auth/login` when
  unauthenticated against the live server, but the actual candidate-rendering re-check is
  handed back to Chief. URL + job ID for that check: `http://localhost:8081/upload/2876f87b-05d2-4654-921a-98e8c59b831d`.

## Session 012-H1 (2026-08-11) — Unified Search: Source Registry Replaces Per-Source Panels

Tier 2 of the Obituary Discovery Roadmap (`cannonops-vault/Projects/FWL/Obituary-Discovery-Roadmap.md`,
decided 2026-08-10): one search box instead of one button per source.

- **`src/sources/registry.py` (new)** — `SOURCES: list[SourceEntry]`, each entry holding a
  `key`, `label`, `base_url`, a zero-arg `factory` returning an `ObituarySource` instance,
  and the source's own exception type. Currently `StithSource` and `ResthavenSource`.
  Nothing else in `app.py` names a source by class — both routes below iterate `SOURCES`.
- **`src/app.py`** — the four hand-wired routes (`/search/stith`, `/search/stith/extract`,
  `/search/resthaven`, `/search/resthaven/extract`) replaced with two generic ones:
  `POST /search` (fans `query` out across every `SOURCES` entry, merges results into one
  list tagged with `source_key`/`source_label`; a per-source error or `SearchUnavailable`
  becomes a warning banner and does not block the other sources' results) and
  `POST /search/extract` (looks up the posted `source` key in `SOURCES`, then validates
  `detail_url` against *that entry's own* `base_url` before fetching — same SSRF guard
  both old routes had, now per-registry-entry instead of duplicated per route). Both reuse
  `_extract_and_save()` and land on the existing `/review/<job_id>` flow unchanged.
- **UI** — `templates/index.html`'s two per-source search panels replaced with one "Search
  obituaries by name" box posting to `/search`. `templates/stith_results.html` and
  `templates/resthaven_results.html` deleted; `templates/search_results.html` (new) renders
  the merged, tagged list with one "Extract" button per result.
- **Tests** — `tests/test_app_stith_search.py` and `tests/test_app_resthaven_search.py`
  deleted (routes no longer exist); `tests/test_app_search.py` (new, 15 tests) covers the
  merge/tag behavior, one-source-fails-doesn't-block-the-other, unknown source key, and the
  cross-source SSRF case (a Resthaven `detail_url` submitted with `source=stith` must be
  rejected, not just any-domain rejected). Full suite: 199 passed, 9 skipped.
- **Live-tested end to end** against the real running dev server (not mocked): searched
  "Hughes" and got real merged, correctly-tagged results from both Stith and Resthaven;
  extracted one result from each source through the real Claude API, both landing on a
  populated `/review/<job_id>`; confirmed the cross-source SSRF guard rejects a
  same-domain-family-but-wrong-entry URL. Browser snapshot of `/tool` confirmed exactly one
  search box, no leftover per-source panels.
- `cfs_source.py`, `resthaven_source.py`, `stith_source.py`, `obituary_source.py`, and
  `cli.py` (which has its own separate `--stith-search`/`--resthaven-search` CLI flags, out
  of scope for this UI-only consolidation) are untouched.
- Adding a third source: implement `ObituarySource`, then append one `SourceEntry` to
  `src/sources/registry.py`'s `SOURCES` list. No route, template, or route-test changes
  needed — `POST /search` and `POST /search/extract` are already generic over the list.
- Report: `cannonops-vault/Handoff-Status/2026-08-11-FWL-012-H1-Unified-Search.md`.

---

## Session 011-H3 Phase 3 (2026-08-10) — Verify Render Free-Tier Assumptions (Report Only)

No code changed — verification and documentation only, per this phase's own instruction.

- Checked `token_store.py`'s two Render assumptions against Render's own docs (not
  assumed, cross-checked across independent fetches): **(a) single instance on the free
  plan — confirmed true.** **(b) "deploys stop the old instance before starting the
  new one" — confirmed false.** Render does zero-downtime rolling deploys: the new
  instance boots and passes its health check while the old instance keeps serving all
  traffic, then traffic switches (a cutover) to the new instance, then the old instance
  gets `SIGTERM` 60 seconds later. No free/paid distinction; the one documented exception
  (a persistent disk disables zero-downtime deploys) doesn't apply here — `render.yaml`
  has no `disk:` section by design.
- **Practical impact:** each Render instance is its own container with its own ephemeral
  filesystem, so the old and new instances don't share a SQLite file during the ~60–90
  second overlap the way two gunicorn workers *inside one instance* do. A user whose
  `/auth/login` lands on the old instance and whose `/callback` lands on the new one
  after cutover hits the same "Unknown or expired OAuth state" failure H3 fixed at the
  gunicorn-worker level — possible again at the instance-transition level. Same for an
  in-progress session or M2.2 decisions file spanning a deploy: lost, not corrupted.
  Requires a deploy to land while a real user is mid-flow and the instance hadn't already
  spun down — narrow, not hypothetical, given `autoDeploy: true`.
- Design not changed per this phase's instruction. H3's own report already scoped the fix
  (Redis, confined to `token_store.py`) if this assumption turned out wrong — it did.
  Chief's call whether the narrow window is worth closing before Chautauqua.
- Full detail: `repo-memory.md` Pending Decisions.

---

## Session 011-H3 Phase 2 (2026-08-10) — Generalize StithSource, Evaluate Resthaven

- Confirmed live (2026-08-10) that Resthaven Mortuary / Slater Neal Funeral Home
  (resthavenmort.com, Trenton) runs the identical CFS/TributeArchive platform as Stith —
  byte-for-byte matching markup, `robots.txt` shape, and `/pax/obitsrch`
  pagination/search mechanics.
- `src/sources/cfs_source.py` — `StithSource`'s implementation generalized into
  `CFSObituarySource(base_url, sitemap_path)`. `src/sources/stith_source.py` is now a
  thin subclass; `src/sources/resthaven_source.py` adds the second tenant.
- **`ResthavenSource` is built but not wired into `app.py` or `cli.py`.** Every real
  endpoint on resthavenmort.com returns Cloudflare's "Attention Required!" 403 to both
  `requests` and `httpx` (already used elsewhere in this app), with full browser-like
  headers, while `curl` with an identical User-Agent succeeds every time — a TLS/client
  fingerprint block, confirmed reproducible across multiple attempts over several
  minutes, ruled out as UA/headers/rate-limiting. A fix needs a different HTTP client
  (e.g. `curl_cffi`) or a `curl` subprocess — a real architecture decision, not made here.
- `tests/test_cfs_source.py` (new) — generic parsing/behavior tests against a fake
  tenant, tested once instead of duplicated per site. `tests/test_stith_source.py`
  slimmed to Stith's own config + live-network confirmation.
  `tests/test_resthaven_source.py` (new) — same thin pattern; its live-network class is
  `xfail` (documents the Cloudflare block, doesn't silently vanish) rather than
  skipped/deleted.
- Full suite: 193 passed, 9 skipped (up from 183+6).
- `cannonops-vault/Projects/FWL/Obituary-Source-Candidates.md` updated: Resthaven moved
  from "new candidate, not yet checked" to "checked, blocked" with the finding.

---

## Session 011-H4 (2026-08-10) — curl_cffi for Resthaven, Wire Into the App, Deploy-Freeze Policy

- **curl_cffi, scoped to Resthaven only.** `src/sources/resthaven_source.py`'s `_get()` is
  now overridden to use `curl_cffi.requests.Session(impersonate="chrome")`, which presents
  a real Chrome TLS fingerprint and gets a clean 200 on every Resthaven endpoint Cloudflare
  had been 403ing (`robots.txt`, `/listings`, the sitemap, `/obituary/<slug>` — confirmed
  live). The override lives entirely in `resthaven_source.py`; `cfs_source.py` and
  `stith_source.py` are untouched, so Stith keeps its plain `requests` transport exactly as
  before. `curl_cffi`'s exception hierarchy doesn't subclass `requests`'s (it inherits from
  `curl_cffi.curl.CurlError`/`OSError` instead), which is why the shared base class's
  `_get()` couldn't have caught it — the override wraps `CFFIRequestException` into
  `CFSSourceError` itself, same contract as the base class. `curl_cffi==0.16.0` pinned in
  `requirements.txt`, used nowhere else.
- **Wired into the app, mirroring Stith exactly.** `app.py` gained `POST /search/resthaven`
  and `POST /search/resthaven/extract` (identical shape to the Stith routes, same
  `_extract_and_save()` reuse, same base-URL validation against a client-controlled
  `detail_url`), and `/tool` gained a "Search Resthaven Mortuary" panel. `cli.py` gained
  `--resthaven-search NAME`, mirroring `--stith-search`.
- **Tests:** `tests/test_resthaven_source.py`'s live-network class is no longer `xfail` — it
  passes for real now — and gained two offline tests confirming the curl_cffi-specific
  error wrapping. New `tests/test_app_resthaven_search.py` (10 route tests, mirrors
  `test_app_stith_search.py`). Full suite: 206 passed, 9 skipped (up from 193+9).
- Live-tested end to end in a real browser session against a running dev server: searched
  "Barb," picked Victor Barb, real curl_cffi fetch through Cloudflare, real Claude API
  extraction, review page showed correctly extracted fields.
- **Deploy-freeze policy documented.** Chief's decision: no deploys during live booth/demo
  hours, resolving the deploy-cutover race `token_store.py` doesn't cover (found FWL-011-H3
  Phase 3). Recorded in `docs/prod-hardening.md` §2.1 (assumption 2's writeup) and its F-09
  failure-mode row, and closed out in `repo-memory.md` Pending Decisions as "resolved via
  operational policy, not code, for now."

---

## Session 011-H3 Phase 1 (2026-08-10) — Wire Stith Name Search into the Flask UI

- `templates/index.html` gained a third input channel on `/tool`: a "Search Stith
  Family Funeral Home (by name)" panel below the existing paste/URL form.
- `POST /search/stith` — searches via `StithSource.search()`, renders
  `templates/stith_results.html` (new) listing matches (name + date, one "Extract →"
  button each) or a "no matches" message.
- `POST /search/stith/extract` — fetches the picked match's full text and lands on the
  same `/review/<job_id>` page the paste/URL path already produces. Validates the posted
  `detail_url` starts with Stith's own base URL before fetching it server-side (the
  hidden form field is client-controlled — an unvalidated fetch there would be an SSRF
  path).
- `src/app.py` — factored `/extract`'s save-and-redirect tail into `_extract_and_save()`,
  shared by all three input channels now (paste, URL, Stith) instead of duplicated.
- Tests: `tests/test_app_stith_search.py`, 10 route tests (`StithSource` and
  `extract_from_text` monkeypatched to fakes, same pattern as `test_app_upload.py`'s
  `FakeMatchClient`). Full suite: 183 passed, 6 skipped (up from 173+6).
- Live-tested end to end against a real name (Hughes) in a real browser session against
  a running dev server — search → pick → extract (real Claude API call) → review page
  showed correctly extracted fields (`given_names="Daniel B."`, `surname="Hughes"`).
- Did not touch `fs_auth.py`, `fs_client.py`, or anything OAuth-related.

---

## Session 011-H2 Phase 2 (2026-08-09) — Pluggable ObituarySource Interface + Stith Adapter

- `src/obituary_source.py` — `ObituarySource` Protocol (`check_access`, `list_recent`,
  `fetch_detail`, `search`-with-`SearchUnavailable`-fallback) plus `AccessLevel`,
  `ObituaryStub`, `ObituaryDetail`. Shape taken from the FWL-011-H1 TriCounty recon's
  proposal, adjusted once code was written against a real source: Stith's `search()`
  returns real results, not `SearchUnavailable`, because a working non-`/pax/` search
  turned out to be buildable.
- `src/sources/stith_source.py` — adapter for `stithfamilyfunerals.com`. **Deviates from
  the brief's "use the real search form/pagination" on purpose:** both route through
  `POST /pax/obitsrch` (confirmed from the listings page's own inline JS), and
  `robots.txt` disallows `/pax/` outright. `list_recent()`/`search()` are built instead
  from `sth/obituary_sitemap.xml` (an explicit `robots.txt` `Sitemap:` entry, 500 URLs +
  `lastmod`, unrestricted); `fetch_detail()` hits `/obituary/<slug>` directly. Also
  corrects the brief: live `robots.txt` carries no `Crawl-delay` for this site at all — a
  self-imposed 1s courtesy delay is applied anyway, not directive-derived.
- **Pipeline compatibility confirmed against 3 real live obituaries** (Hughes, Ranes,
  Wilson): `extract_from_text(detail.text, detail.source_url)` runs with zero glue code —
  `ObituaryDetail.text` is already in the same clean-plain-text shape `fetch.py` produces
  (reuses its `_clean_whitespace`).
- `src/cli.py` — new `--stith-search NAME` mode: search → numbered picker → fetch → same
  extract-and-save path as `--url`. Not wired into the Flask UI (deliberately kept
  standalone; `app.py`/`fetch.py`/`fs_*.py` untouched).
- Tests: `tests/test_stith_source.py`, 23 offline + 3 live-network (`RUN_NETWORK_TESTS=1`).
  Full suite: 173 passed, 6 skipped (up from 150+3).
- TriCounty Weekly explicitly out of scope this session (pending business conversation).
- Ledger row L-039 → DONE.

---

## Session 010-H3 merge (2026-08-09) — Multi-Worker-Safe Token Store + Booth Hardening

**Version: 0.7.0** (same milestone as H2, prod-hardening layer merged in alongside it)

- Merged `fwl-010-fs-prod-hardening` (H-024/H3, built in parallel worktree against the
  M2.0/M2.1 base, never previously merged — ledger row H-026) into this branch. Real
  conflicts in `src/fs_auth.py` (H2's `FAMILYSEARCH_USE_PKCE` diagnostic toggle vs. H3's
  swap from module-level dicts to `token_store`) and `tests/test_fs_client.py` (import
  lists) resolved so both survive: `build_authorize_url()` keeps the PKCE on/off toggle,
  but every pending-handshake and session write now goes through `token_store` instead of
  a process-local dict. `tests/test_fs_auth.py` (not itself conflicted by git, but broken
  by the dict removal) updated to the same per-test isolated-store fixture pattern already
  used in `tests/test_token_store.py`.
- **The headline fix:** Render runs `gunicorn -w 2`. The module-level dicts `fs_auth.py`
  used to hold OAuth handshake state and sessions in are per-worker, so `/auth/login`
  landing on one worker and `/callback` on the other lost the PKCE `code_verifier` and
  failed with "Unknown or expired OAuth state." New `src/token_store.py` (SQLite, shared
  filesystem, connections opened per call since gunicorn forks) backs pending handshakes,
  sessions (sliding idle window + hard cap at token expiry), and upload-job locks instead.
  This is a **different bug** than the "Invalid Oauth2 Request" error Joel hit testing
  against `localhost:8081` in H2 — single-process, so the worker-split can't be the cause
  there. That failure is still open; see `repo-memory.md` Pending Decisions.
- `src/fs_client.py`: upload journal now records intent *before* each write, not after —
  an interrupted write used to leave no record, so a resume would POST again and risk a
  duplicate person. A resume that finds an unsettled write now halts (`FSUncertainWriteError`)
  instead of guessing. `FSJobLockedError` + optional job lock in `run_upload_sequence()` stop
  two browser tabs (or a double-submitted commit) from running the same upload twice.
- `src/app.py`: `POST /auth/logout` (explicit sign-out, clears server-side token + cookie —
  the shared-kiosk control). Production now refuses to start without `FLASK_SECRET_KEY` set
  (was a silent dev-placeholder fallback). Session cookie gets `HttpOnly`/`SameSite=Lax`/
  `Secure`-in-production. `/logs` 404s in production unless `FWL_LOGS_PUBLIC` is set (the
  buffers can carry a prior visitor's FamilySearch display name). Header badge now uses
  `peek_session()` (new, non-idle-sliding read) instead of `get_session()`, so a tab left
  open on a walked-away kiosk visitor still times out on schedule.
- `render.yaml`: `FLASK_SECRET_KEY` via `generateValue: true` (stable across deploys, both
  workers agree).
- `docs/prod-hardening.md` — design doc: options weighed (SQLite vs. Redis vs. per-session
  files vs. encrypted cookie vs. sticky sessions), a 12-row booth failure-mode table, and
  the security-review feed for plan §6 (8 items answered, 6 still open — see the doc).
- Tests: 150 passed, 3 skipped (up from 117+3; H3 alone added 33 tests before merge —
  `tests/test_token_store.py` plus new `TestInterruptedWrite`/`TestJobLocking` classes in
  `tests/test_fs_client.py`).
- **Still open, not resolved by this merge:** the F-08 booth cold-start risk (Render
  free-tier idle spin-down) needs Chief's call on keep-alive-ping vs. plan upgrade; the
  single-instance/stop-before-start Render assumptions the SQLite design rests on were
  never verified against Render's own docs; the "Invalid Oauth2 Request" auth failure H2
  hit is unrelated to this fix and remains unreproduced.

---

## Session 010-H2 (2026-08-08) — Auth-Failure Diagnostics + M2.2 Match-Check/Confirm-Gate UI

**Version: 0.7.0**

- Live FamilySearch sign-in fails with "Invalid Oauth2 Request — unable to authenticate
  client" from FamilySearch's own identity server. Root cause not yet identified — see
  `repo-memory.md` Pending Decisions for the isolation test needed from Joel.
- `FAMILYSEARCH_USE_PKCE` diagnostic toggle (`src/fs_auth.py`, `.env`/`.env.example`,
  default on) — lets the plain authorization-code flow be isolation-tested without a
  code change. 16 new tests in `tests/test_fs_auth.py` (first test coverage for this
  module) cover both PKCE on/off paths.
- `src/fs_client.py` gained `search_matches()` (Person Matches by Example — always
  executes live even under dry-run since it's a read, not journaled since it's not a
  write) and `bucket_for_confidence()` (placeholder Strong/Possible/Weak bucketing
  against FS's 1-5 confidence scale — cannot be tuned empirically without a live token,
  plan §9 open question 5).
- M2.2 match-check + confirm-gate UI: `GET /upload/<job_id>` (match panel per person —
  name/lifespan/candidate PID/confidence bucket/link-out to FamilySearch.org) and
  `POST /upload/<job_id>/decide` (server-validated per-person decisions; commit blocked
  until every person is decided, per the hard human-confirm-gate rule) in `src/app.py`;
  `templates/upload.html` + `templates/decided.html`. Gated behind sign-in and
  `FWL_FS_UPLOAD_ENABLED` (default off, per plan §0 prod-safety). `approve()` now keeps
  `tmp/<job_id>.json` alive (overwritten with the approved data) instead of deleting it,
  so `/upload/<job_id>` has something to load.
- M2.3 (live writes) is not built — the decide route only records decisions.
- Tests: 117 passed, 3 skipped (up from 81+3)
- **Live browser-verified M2.2 acceptance (plan's "seeded near-duplicate surfaced" check)
  is blocked on the still-open auth failure** — carried into the next session.

---

## Session 010 (2026-08-08) — M2.0 FamilySearch OAuth + M2.1 Mapping/Client/Dry-Run

**Version: 0.6.0**

- `src/fs_auth.py` — OAuth2 authorization-code + PKCE (S256) flow for FamilySearch's public
  client, hand-rolled (no `authlib`). Server-side token store keyed off a Flask-session id;
  the raw access token never touches the session cookie.
- `GET /auth/login` / `GET /callback` routes in `src/app.py`; signed-in badge (FS display
  name) or a sign-in link in the header (`templates/base.html`)
- `src/fs_map.py` — pure GEDCOM X mapping from FWL extraction JSON to FamilySearch person/
  relationship/source structures (plan §2): partial-date formal-date conversion, maiden-name
  second `BirthName`, suffix handling, living-relative default-exclusion with opt-in,
  sibling gating on parents having been included in the plan, "don't guess the other parent"
  for children. 24 unit tests, no network.
- `src/fs_client.py` — `FamilySearchClient`: dry-run boundary (writes captured, reads real),
  upload journal for idempotent resume, 429/503 `Retry-After` backoff, 401 → re-auth signal,
  4xx halts without retry; `run_upload_sequence()` orchestrates the persons → relationships →
  source → attach write sequence (plan §4.2). 9 tests against `httpx.MockTransport`.
- Offline golden-file dry-run test against the Neese fixture, producing the expected
  20-entry intended-writes journal with zero network calls
- Confirmed live (WebFetch/WebSearch against developers.familysearch.org, not from memory):
  authorize/token hosts on `identbeta.familysearch.org`, current-user resource on
  `apibeta.familysearch.org`
- Removed vestigial `FAMILYSEARCH_CLIENT_SECRET` (`.env`, `.env.example`) and the
  commented-out `authlib` line in `requirements.txt` — M2.0 decided to hand-roll OAuth
- Tests: 81 passed, 3 skipped (up from 45+3)
- Live sign-in handshake (scope-name capture) still needs a human FamilySearch login —
  see `repo-memory.md` Pending Decisions

---

## Session 008 (2026-07-11) — M3.0 Vision Transcription Eval

- `scripts/gen_fixtures.py` — synthetic scan fixture generator (Pillow): clean/degraded/
  phone-photo clippings + a 3-obituary page with ground-truth bboxes, deterministic per seed
  (`make fixtures` target)
- `scripts/m3_eval.py` — Sonnet 5 vs. Haiku 4.5 vision transcription eval: resolution-knee
  matrix, segmentation-probe accuracy, PDF-vs-per-page-image comparison
- `scripts/eval_metrics.py` — pure CER/WER/IoU metrics, unit-tested
- `prompts/obituary_transcribe.md` — v1 transcription system prompt (verbatim, illegible
  markers, header context, portrait detection)
- Ran full eval against synthetic fixtures (~$0.39 API spend, BYOK): results and chosen
  defaults recorded in `docs/m3-0-eval-note.md` — Haiku 4.5 @ 1568px long edge is the default
  transcription tier (parity with Sonnet at 1092px+, ~2-3x cheaper); crop-then-transcribe
  confirmed for segmentation; per-page image ingest kept over native PDF input
- Tests: 45 passed, 3 skipped

---

## Session 005a (2026-04-27) — Stale Job ID Cleanup

**Version: 0.5.1**

- `/review/<job_id>` and `/approve/<job_id>` (POST) now silently redirect to `/tool` when tmp file is missing, replacing the user-facing "Session expired or job not found" error banner
- New `/approve/<job_id>` (GET) handler — silent redirect to `/tool` for stale bookmarks / back-button navigation (kills the 405 Method Not Allowed page)

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
