# Production Hardening — Multi-Worker Token Store and Booth Failure Modes

**Status:** DESIGNED + BUILT (FWL-010-H3, 2026-08-08).
**Scope:** the layer between "M2 works on a dev laptop" and "a stranger can use this at the
Chautauqua booth without corrupting their family tree."
**Relationship to the plan:** `planning/familysearch-upload-plan.md` §0 defers this with "that's
a solved problem (server-side session store) by the time prod matters," and §6 lists
"Render-side auth (multi-worker token store)" under M3. This document is that work, done
early because the booth date arrives before the M3 certification gate does.

---

## 1. What was actually broken

Three separate defects, all invisible in dev because dev runs a single-process Flask server.

### 1.1 The OAuth handshake could not complete under `gunicorn -w 2`

`src/fs_auth.py` held two module-level dicts, `_PENDING` and `_SESSIONS`. Gunicorn forks two
worker processes, and nothing pins a browser to a worker. So:

```
GET /auth/login   -> worker A   _PENDING[state] = {code_verifier: ...}
                                (browser goes off to identbeta.familysearch.org)
GET /callback     -> worker B   _PENDING.pop(state) -> None
                                FSAuthError: "Unknown or expired OAuth state"
```

Roughly half of all sign-in attempts fail, non-deterministically, and the error message
points the user at "restart sign-in," which fails again half the time. The failure has no
relationship to the credentials, the AppKey, or PKCE, which is exactly what makes it
expensive to diagnose from the symptom.

Note the near miss: the `state` value itself is *also* stored in the Flask cookie session
(`session["fs_oauth_state"]` in `src/app.py`), and cookies travel with the browser, so CSRF
state matching works fine cross-worker. It is only the PKCE `code_verifier` in `_PENDING`
that is lost. That narrows the symptom to a token-exchange failure rather than a state
mismatch, which is worth knowing while H2 diagnoses the live failure.

**H2 coordination:** if H2's live OAuth failure was reproduced on `farwestlegacy.com`, this
is a strong candidate for the root cause and the fix is in this branch. If it was reproduced
on `localhost:8081` (single process), this is not the cause and H2 should keep looking. The
two diagnoses are independent, and both fixes are wanted regardless.

### 1.2 A signed-in visitor would randomly appear signed out

Same cause, `_SESSIONS`. The header badge, and any authorization check on an upload route,
would flicker between "signed in as Jane" and "sign in" depending on which worker served the
page. On a booth kiosk this reads as the app being broken.

### 1.3 An interrupted write could create a person twice

`src/fs_client.py` wrote its journal entry *after* each call returned. The plan (§4.2) calls
for "written before and after every intended call," and the gap matters: if a `POST` reaches
FamilySearch but the response never comes back (dropped connection, worker killed, Render
spin-down), the journal has no record at all. A resume sees a clean slate for that step and
POSTs again. That is the "created Grandma twice" case the journal exists to prevent.

---

## 2. Token store: the options, and why SQLite

The requirement is a store that every gunicorn worker can read and write, holding three
kinds of short-lived record: in-flight OAuth handshakes, authenticated sessions, and upload
job locks.

| Option | Works under `-w 2`? | Cost | Why not / why yes |
| --- | --- | --- | --- |
| **SQLite file in the container** | Yes | None. `sqlite3` is stdlib. | **Chosen.** Both workers are processes inside one container, so they share one filesystem. Handles the concurrent-writer race correctly via `BEGIN IMMEDIATE` without hand-rolled lock files. Ceiling: breaks the day FWL runs more than one instance. |
| Redis / Render Key Value | Yes, and across instances | A second service to provision, monitor, and pay for past the free 25MB tier; a new pinned dependency. | Correct answer at scale, over-provisioned for one booth and one instance. This is the upgrade path, not the starting point. |
| Filesystem, one JSON file per session | Yes | None | Same sharing property as SQLite but needs a hand-written expiry sweep, a hand-written atomic-replace, and hand-written lock semantics for the job lock. More code for less correctness. |
| Encrypted client-side cookie | Yes, trivially | New crypto dependency | Puts a FamilySearch bearer token in the browser, on a **shared kiosk device**. Rejected on the booth threat model, not on the engineering. |
| Sticky sessions at the load balancer | Papers over it | Render free plan does not offer it | Not available, and would not survive a worker restart anyway. |

### 2.1 Assumptions this choice rests on

State these plainly, because if any is wrong the answer changes to Redis:

1. **Render's free plan runs exactly one instance of the service.** SQLite-in-container is
   correct for N workers in 1 container and incorrect for N containers.
2. **Free-plan deploys stop the old instance before starting the new one** (no zero-downtime
   overlap). If two containers ever run simultaneously during a deploy, sessions created on
   one are invisible to the other. Consequence is mild (re-login), not corrupting.
3. **`-w 2` means two forked processes sharing a filesystem**, which is standard gunicorn
   behavior and not Render-specific.

Assumptions 1 and 2 are Render plan behavior and were not verified against Render's docs
this session. See "Needs Chief" in the completion report.

### 2.2 Implementation notes worth keeping

- **Connections are opened per call, never at module level.** Gunicorn forks workers; a
  SQLite connection created before the fork and used from two processes afterward corrupts
  the file. Per-call `connect()` sidesteps the whole class of bug and is far below any
  performance threshold that matters here.
- **WAL journal mode + a 10s busy timeout** so one worker writing does not block the other
  reading.
- **The store file lives under `tmp/`**, which is gitignored and, on Render, ephemeral. A
  redeploy wipes every bearer token. That is the behavior we want.
- **The store is agnostic to PKCE.** `put_pending()` takes an opaque dict. With PKCE it
  carries `code_verifier` + `redirect_uri`; without PKCE it carries just `redirect_uri`. If
  H2 concludes this AppKey cannot do PKCE at all, only `fs_auth.py` changes and this store
  does not. There is a test asserting exactly that (`test_works_without_pkce_verifier`).

### 2.3 Session lifetime: two clocks

A session dies at whichever comes first:

- **Sliding idle window**, default 20 minutes (`FWL_FS_SESSION_IDLE_SECONDS`). Refreshed on
  active use, deliberately *not* refreshed by merely rendering a page. The header badge uses
  `peek_session()`, which reads without sliding, so a tab left open on the kiosk still times
  out on schedule. This is the control that stops a walked-away visitor's session being
  inherited by the next person at the same browser.
- **Hard cap from the token's own `expires_in`.** No point holding a session past the token
  it wraps. Falls back to `FWL_FS_SESSION_ABSOLUTE_SECONDS` (8h) if FamilySearch returns no
  usable `expires_in`.

20 minutes is a judgment call, not a derived number. It is long enough to work through one
obituary without re-authenticating and short enough that a visitor who wanders off has
usually timed out before the next person sits down. Tune it after the first booth day.

---

## 3. Booth failure modes

The operating picture: a visitor with a phone, unfamiliar with FamilySearch, possibly using a
shared kiosk browser, with a non-developer volunteer nearby who cannot read a stack trace and
cannot restart a service. Every row below has to end in a state a volunteer can act on.

| # | Failure | What happens now | What the visitor sees | What the volunteer does |
| --- | --- | --- | --- | --- |
| **F-01** | Session times out mid-sequence (idle > 20 min) | Store returns None. Next FS call gets 401 -> `FSAuthExpiredError`. Journal is intact on disk. | "Your FamilySearch sign-in expired. Sign in again and we will pick up where we left off." | Point at Sign in. Nothing is lost; the journal resumes. |
| **F-02** | Browser closed mid-OAuth (visitor bails at the FamilySearch login page) | Pending record expires after 10 min and is swept. Nothing was created. | Nothing. They are gone. | Nothing to clean up. Next visitor gets a clean sign-in. |
| **F-03** | OAuth callback lands on the other worker | **Fixed.** Store is shared, `code_verifier` is found. | Normal successful sign-in. | Nothing. This was the ~50% random sign-in failure. |
| **F-04** | Duplicate tab / double-tapped commit button | Second run fails `acquire_job_lock` -> `FSJobLockedError`. | "This upload is already running in another window. Finish or close that one." | Close the extra tab. No double write. |
| **F-05** | Journal left half-complete, all steps cleanly recorded | Resume skips `ok` steps, reuses PIDs, continues. | "Resuming your upload." Summary screen completes normally. | Nothing. This is the designed path. |
| **F-06** | Journal left with an `in_flight` write (POST sent, response lost) | **Halts.** `FSUncertainWriteError`. Never retried automatically. | "We sent one step to FamilySearch but never heard back, so we cannot tell whether it was saved. Check the person on FamilySearch before continuing." with a link out. | Escalate, or have the visitor check FamilySearch.org. **Deliberately a dead end**, because guessing here is how you get a duplicate ancestor. |
| **F-07** | Visitor walks away still signed in; next person sits down | Idle window expires the session. Until it does, the header shows the previous visitor's name next to a "Not you? Sign out" button. | The previous visitor's name, clearly labeled. | Click Sign out. For a hard reset of every session at once, `token_store.reset()`. |
| **F-08** | Render free instance spins down (idle ~15 min) | Container is destroyed. Every token and every journal in `tmp/` is gone. | Cold-start delay (tens of seconds), then a signed-out app. | Wait for the page, sign in again. **Any in-progress upload is unrecoverable and its journal is gone.** See risk note below. |
| **F-09** | Redeploy lands mid-upload | Same as F-08, plus `FLASK_SECRET_KEY` stays stable (Render `generateValue`) so cookies survive. Tokens do not. | Signed out mid-flow. | Sign in again, restart the upload. |
| **F-10** | One gunicorn worker crashes and is replaced | Store and journal are on disk and survive. Any job lock the dead worker held expires in 5 min. | Nothing, or a single failed request. | Nothing. |
| **F-11** | FamilySearch throttles the user (429) | Existing `fs_client` behavior: honor `Retry-After`, back off, cap 3 retries, journal intact. | "FamilySearch is throttling us. Try again in a minute." | Wait, then resume. |
| **F-12** | Forged session cookie | Production refuses to boot without `FLASK_SECRET_KEY`; `generateValue` supplies a strong one. Cookie is HttpOnly, SameSite=Lax, Secure in production. | N/A | N/A |

### 3.1 F-08 is the unmitigated one, and it is the biggest booth risk

Render's free plan spins the instance down after a period of inactivity. A booth has exactly
the traffic pattern that triggers this: a burst while someone is at the table, then twenty
quiet minutes. The consequences are a slow cold start for the next visitor and, worse, total
loss of any journal for an upload that was in progress.

Three ways out, in increasing cost:

1. **A keep-alive ping** every ~10 minutes from anything external (Chief's phone, an uptime
   monitor, a cron on the Mac Mini) for the duration of the booth. Cheapest, and enough.
2. **Upgrade the Render service off the free plan** for the booth month. Removes spin-down
   entirely and is the honest answer if FWL is being demoed to a paying customer.
3. **Persist the journal somewhere durable** (Render disk, or the shared store). Real work,
   and it does not fix the cold-start delay.

Recommendation: (1) for the booth, and put (2) on the table if Matthew Johnson is watching.
This is a Chief decision, not an engineering one, so it is not implemented here.

---

## 4. What is built, and what M2.2 still has to wire up

Built in this branch and tested:

- `src/token_store.py` — the shared store. 25 tests including two genuine two-process tests.
- `src/fs_auth.py` — swapped off module dicts, plus `peek_session()`.
- `src/fs_client.py` — intent-before-outcome journaling, `FSUncertainWriteError`,
  `FSJobLockedError`, optional job locking in `run_upload_sequence()`.
- `src/app.py` — `POST /auth/logout`, production secret-key enforcement, cookie hardening,
  `/logs` closed in production.
- `templates/base.html` — "Not you? Sign out" in the header.
- `render.yaml` — `FLASK_SECRET_KEY` via `generateValue`.

Deliberately **not** built here, because it lives in the M2.2 upload UI that H-023 owns:

- **Passing the job lock through.** `run_upload_sequence()` takes `job_id` and `owner` and
  does nothing without them. The upload route should pass `job_id=<the job uuid>` and
  `owner=<the Flask session's fs_sid, or a per-tab token>`, and render `FSJobLockedError` as
  the F-04 message rather than a 500.
- **Rendering `FSUncertainWriteError` as the F-06 screen.** If it reaches the generic error
  handler it becomes a stack trace, which is the one outcome F-06 must not produce.
- **The `FWL_FS_UPLOAD_ENABLED` gate** from plan §0. Upload routes do not exist yet, so
  there is nothing to gate; it belongs with the routes.
- **Auth checks on upload routes.** Use `fs_auth.get_session()` (which slides the idle
  window), not `peek_session()`.

---

## 5. Feed into the FamilySearch security review (plan §6, M3)

Items a Compatibility Review will plausibly ask about. Several are answered by this branch;
the rest are open and are listed honestly.

**Answered:**

1. **Token storage.** Access tokens are held server-side only, never in a cookie, never in
   `localStorage`, never logged. The store is container-local and wiped on redeploy.
2. **Session lifetime.** Bounded by both an idle timeout and the token's own `expires_in`.
   No indefinite sessions.
3. **Session termination.** Explicit user-initiated sign-out exists, is a POST (not a
   drive-by GET), and clears both the server-side token and the client cookie.
4. **CSRF on the OAuth handshake.** `state` is generated per attempt, stored in the signed
   cookie session, compared on callback, and the pending record is single-use.
5. **PKCE.** S256 by default, public client, no secret in the token exchange.
6. **Session cookie hygiene.** HttpOnly, SameSite=Lax, Secure in production, signed with a
   platform-generated key. Production refuses to start without one.
7. **Attribution.** Every write is made with the signed-in human's own token, so
   FamilySearch's change history attributes it to them. No service account, no shared
   credential, no headless batch path exists.
8. **Human confirm gate.** Unchanged and non-negotiable (plan §3): no code path from
   extraction to a write bypasses the per-person decision screen.

**Open, and worth raising before the review rather than during it:**

9. **Tokens are not encrypted at rest.** They sit in a SQLite file readable by anything
   running in the container. Encrypting them needs a real AEAD, which means adding
   `cryptography` as a pinned dependency and deriving a key from `FLASK_SECRET_KEY`. Worth
   doing if the review asks; not done unilaterally because it is a dependency decision.
   Note the threat model is thin: an attacker who can read that file already has the
   container.
10. **`/logs` exposure.** The log buffers hold FamilySearch display names and raw API error
    bodies. Now closed in production behind `FWL_LOGS_PUBLIC`, but the underlying issue is
    that `fs_client` logs `resp.text` verbatim on error, which could contain PII from a
    person record. Redacting API response bodies before they reach the log buffer is the
    real fix and is not done.
11. **Shared-kiosk model.** FWL will be operated on a device many unrelated people use in
    one afternoon. FamilySearch may have an opinion about this that is not in the written
    terms. Worth asking Gordon directly rather than discovering it at review.
12. **No token revocation call.** Sign-out drops FWL's copy of the token but does not tell
    FamilySearch to invalidate it; the token stays live until it expires. If identbeta
    exposes a revocation endpoint, sign-out should call it. Add to the Gordon agenda.
13. **Production redirect URI.** Still unregistered (plan §1 agenda item 4). Until
    `https://farwestlegacy.com/callback` exists on the AppKey, none of this runs in
    production regardless of how correct the token store is.
14. **F-08 data loss.** An upload journal lost to a free-tier spin-down is a correctness
    story, not just an availability one. Resolve per §3.1 before the booth.
