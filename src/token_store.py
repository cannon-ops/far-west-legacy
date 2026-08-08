"""token_store.py — process-shared store for OAuth handshakes, FamilySearch sessions,
and upload-job locks.

Why this exists: Render runs `gunicorn -w 2`, so the module-level dicts in fs_auth.py
lived in whichever worker happened to serve the request. `/auth/login` landing on worker A
and `/callback` on worker B loses the PKCE code_verifier and the handshake fails outright.
See docs/prod-hardening.md for the full design and the options that were weighed.

Backend is SQLite because Render's free plan runs a single instance, so both gunicorn
workers share one filesystem, and sqlite3 is stdlib — no new dependency, no new service.
The module boundary is the swap point if FWL ever needs a second instance (§2 of the doc).

Connections are opened per call, never at module level. Gunicorn forks its workers, and a
connection created before the fork and used in two processes afterward corrupts the file.

Three namespaces, all in one table:
  pending  — in-flight OAuth handshake (state -> code_verifier + redirect_uri), single-use
  session  — an authenticated FamilySearch session (sliding idle TTL, hard cap at token expiry)
  joblock  — advisory lock so one upload job can't run twice concurrently (duplicate tabs)
"""

import json
import logging
import os
import sqlite3
import time
from pathlib import Path

logger = logging.getLogger(__name__)

NS_PENDING = "pending"
NS_SESSION = "session"
NS_JOBLOCK = "joblock"

# An OAuth handshake a visitor abandons is garbage within minutes. Long enough for
# someone slow on a phone keyboard, short enough that abandoned state does not pile up.
PENDING_TTL_SECONDS = int(os.getenv("FWL_FS_PENDING_TTL_SECONDS", "600"))

# Booth safety control, not a convenience setting. A visitor who walks away must not
# leave a live FamilySearch session for the next person at the same kiosk browser.
SESSION_IDLE_SECONDS = int(os.getenv("FWL_FS_SESSION_IDLE_SECONDS", "1200"))

# Fallback hard cap when the token response carries no usable `expires_in`.
SESSION_ABSOLUTE_SECONDS = int(os.getenv("FWL_FS_SESSION_ABSOLUTE_SECONDS", "28800"))

# Long enough to cover a slow multi-write upload, short enough that a crashed worker's
# lock frees itself without an operator. Refreshed by the running request.
JOBLOCK_TTL_SECONDS = int(os.getenv("FWL_FS_JOBLOCK_TTL_SECONDS", "300"))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS store (
    ns          TEXT NOT NULL,
    key         TEXT NOT NULL,
    value       TEXT NOT NULL,
    owner       TEXT,
    created_at  REAL NOT NULL,
    expires_at  REAL NOT NULL,
    hard_expires_at REAL,
    PRIMARY KEY (ns, key)
);
CREATE INDEX IF NOT EXISTS idx_store_expiry ON store (expires_at);
"""


def store_path() -> Path:
    """Resolved store location. Defaults under tmp/, which is gitignored and, on Render,
    ephemeral — a redeploy wipes it, which is the behavior we want for bearer tokens."""
    configured = os.getenv("FWL_TOKEN_STORE_PATH")
    if configured:
        return Path(configured)
    return Path(__file__).parent.parent / "tmp" / "token_store.sqlite3"


def _connect() -> sqlite3.Connection:
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    # WAL lets the two workers read while one writes; busy_timeout absorbs the rest.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(_SCHEMA)
    return conn


def _sweep(conn: sqlite3.Connection, now: float) -> int:
    cur = conn.execute("DELETE FROM store WHERE expires_at <= ?", (now,))
    return cur.rowcount or 0


def sweep() -> int:
    """Delete every expired row. Called opportunistically on writes; exposed for tests
    and for a future scheduled sweep if volume ever justifies one."""
    with _connect() as conn:
        return _sweep(conn, time.time())


def reset() -> None:
    """Drop all rows. Test helper and the nuclear option for a booth volunteer who needs
    to guarantee nobody is signed in (see docs/prod-hardening.md, failure mode F-07)."""
    with _connect() as conn:
        conn.execute("DELETE FROM store")


# ---------------------------------------------------------------------------
# Pending OAuth handshakes
# ---------------------------------------------------------------------------


def put_pending(state: str, record: dict) -> None:
    """Record an in-flight handshake keyed by the OAuth `state` parameter.

    `record` is an opaque blob so this works whether or not PKCE is in play: with PKCE it
    carries code_verifier + redirect_uri, without it just redirect_uri. If the beta AppKey
    turns out to reject PKCE, only fs_auth.py changes — this store does not.
    """
    now = time.time()
    with _connect() as conn:
        _sweep(conn, now)
        conn.execute(
            "INSERT OR REPLACE INTO store (ns, key, value, owner, created_at, expires_at)"
            " VALUES (?, ?, ?, NULL, ?, ?)",
            (NS_PENDING, state, json.dumps(record), now, now + PENDING_TTL_SECONDS),
        )


def pop_pending(state: str) -> dict | None:
    """Fetch and delete a handshake record. Single-use: a replayed authorization code
    finds nothing the second time, which is what we want on a shared kiosk browser."""
    now = time.time()
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT value FROM store WHERE ns = ? AND key = ? AND expires_at > ?",
            (NS_PENDING, state, now),
        ).fetchone()
        conn.execute("DELETE FROM store WHERE ns = ? AND key = ?", (NS_PENDING, state))
        conn.execute("COMMIT")
    return json.loads(row["value"]) if row else None


# ---------------------------------------------------------------------------
# Authenticated FamilySearch sessions
# ---------------------------------------------------------------------------


def put_session(session_id: str, token: dict, display_name: str) -> None:
    """Store a FamilySearch session. Two clocks apply, whichever expires first wins:
    a sliding idle window (booth walk-away) and a hard cap from the token's own
    `expires_in` (no point holding a session past the token it wraps)."""
    now = time.time()
    try:
        lifetime = int(token.get("expires_in") or 0)
    except (TypeError, ValueError):
        logger.warning("token_store: unparseable expires_in %r, using absolute fallback",
                       token.get("expires_in"))
        lifetime = 0
    hard_expires_at = now + (lifetime if lifetime > 0 else SESSION_ABSOLUTE_SECONDS)

    value = json.dumps({"token": token, "display_name": display_name})
    with _connect() as conn:
        _sweep(conn, now)
        conn.execute(
            "INSERT OR REPLACE INTO store (ns, key, value, owner, created_at, expires_at, hard_expires_at)"
            " VALUES (?, ?, ?, NULL, ?, ?, ?)",
            (NS_SESSION, session_id, value, now,
             min(now + SESSION_IDLE_SECONDS, hard_expires_at), hard_expires_at),
        )


def get_session(session_id: str) -> dict | None:
    """Return {"token": ..., "display_name": ...} and slide the idle window forward,
    or None if the session is unknown, idle-expired, or past its hard cap."""
    now = time.time()
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT value, hard_expires_at FROM store"
            " WHERE ns = ? AND key = ? AND expires_at > ?",
            (NS_SESSION, session_id, now),
        ).fetchone()
        if row is None:
            conn.execute("COMMIT")
            return None
        hard = row["hard_expires_at"] or (now + SESSION_ABSOLUTE_SECONDS)
        if hard <= now:
            conn.execute("DELETE FROM store WHERE ns = ? AND key = ?", (NS_SESSION, session_id))
            conn.execute("COMMIT")
            return None
        conn.execute(
            "UPDATE store SET expires_at = ? WHERE ns = ? AND key = ?",
            (min(now + SESSION_IDLE_SECONDS, hard), NS_SESSION, session_id),
        )
        conn.execute("COMMIT")
    return json.loads(row["value"])


def peek_session(session_id: str) -> dict | None:
    """Same read as get_session but without sliding the idle window. Used by the header
    badge so that merely having a page open does not keep a walked-away visitor signed in."""
    now = time.time()
    with _connect() as conn:
        row = conn.execute(
            "SELECT value, hard_expires_at FROM store"
            " WHERE ns = ? AND key = ? AND expires_at > ?",
            (NS_SESSION, session_id, now),
        ).fetchone()
    if row is None or (row["hard_expires_at"] or 0) <= now:
        return None
    return json.loads(row["value"])


def clear_session(session_id: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM store WHERE ns = ? AND key = ?", (NS_SESSION, session_id))


# ---------------------------------------------------------------------------
# Upload-job locks
# ---------------------------------------------------------------------------


def acquire_job_lock(job_id: str, owner: str) -> bool:
    """Take the advisory lock for an upload job. Returns False when another owner holds a
    live lock — the second browser tab, or a double-submitted commit button. Re-acquiring
    as the same owner succeeds and refreshes the lease, so a long upload keeps it alive.

    BEGIN IMMEDIATE takes SQLite's write lock before the read, which is what makes this
    safe against two gunicorn workers racing on the same job at the same instant.
    """
    now = time.time()
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT owner FROM store WHERE ns = ? AND key = ? AND expires_at > ?",
            (NS_JOBLOCK, job_id, now),
        ).fetchone()
        if row is not None and row["owner"] != owner:
            conn.execute("COMMIT")
            return False
        conn.execute(
            "INSERT OR REPLACE INTO store (ns, key, value, owner, created_at, expires_at)"
            " VALUES (?, ?, '{}', ?, ?, ?)",
            (NS_JOBLOCK, job_id, owner, now, now + JOBLOCK_TTL_SECONDS),
        )
        conn.execute("COMMIT")
    return True


def release_job_lock(job_id: str, owner: str) -> None:
    """Release only if we still hold it. An expired-and-retaken lock belongs to someone
    else; releasing it blind would let two uploads run at once."""
    with _connect() as conn:
        conn.execute(
            "DELETE FROM store WHERE ns = ? AND key = ? AND owner = ?",
            (NS_JOBLOCK, job_id, owner),
        )


def job_lock_owner(job_id: str) -> str | None:
    """Current live lock holder, or None. For the 'this upload is already running' screen."""
    now = time.time()
    with _connect() as conn:
        row = conn.execute(
            "SELECT owner FROM store WHERE ns = ? AND key = ? AND expires_at > ?",
            (NS_JOBLOCK, job_id, now),
        ).fetchone()
    return row["owner"] if row else None
