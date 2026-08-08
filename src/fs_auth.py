"""fs_auth.py — FamilySearch OAuth2 authorization-code flow with PKCE (public client).

Beta identity server confirmed live (2026-08-08, see repo-memory.md):
  authorize: https://identbeta.familysearch.org/cis-web/oauth2/v3/authorization
  token:     https://identbeta.familysearch.org/cis-web/oauth2/v3/token
  user:      https://apibeta.familysearch.org/platform/users/current

No client secret — FWL is a public client (AppKey only). PKCE S256 by default.
"""

import base64
import hashlib
import logging
import os
import secrets
from urllib.parse import urlencode

import httpx

from src import token_store

logger = logging.getLogger(__name__)

IDENT_HOST = "https://identbeta.familysearch.org"
API_HOST = "https://apibeta.familysearch.org"
AUTHORIZE_PATH = "/cis-web/oauth2/v3/authorization"
TOKEN_PATH = "/cis-web/oauth2/v3/token"
CURRENT_USER_PATH = "/platform/users/current"

# Exact granted scope names are unknown until the first live human login — the
# Authorization resource treats `scope` as optional, so omitting it lets FamilySearch
# grant whatever the beta AppKey is provisioned for. Set FAMILYSEARCH_SCOPE once
# confirmed (see plan §1 agenda item 1 / repo-memory.md).
DEFAULT_SCOPE = os.getenv("FAMILYSEARCH_SCOPE", "")

# Server-side stores, never in the (signed but unencrypted) Flask session cookie.
# Backed by token_store, which is shared across gunicorn workers — the module-level
# dicts that used to live here broke under Render's `-w 2`, because /auth/login and
# /callback are not guaranteed to land on the same worker. See docs/prod-hardening.md.


class FSAuthError(Exception):
    """Raised on OAuth handshake failures: state mismatch, token exchange failure, etc."""


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _new_pkce_pair() -> tuple[str, str]:
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def build_authorize_url(client_id: str, redirect_uri: str) -> tuple[str, str]:
    """Return (authorize_url, state). Caller must persist `state` and match it on /callback."""
    state = secrets.token_urlsafe(24)
    verifier, challenge = _new_pkce_pair()
    token_store.put_pending(state, {"code_verifier": verifier, "redirect_uri": redirect_uri})

    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    if DEFAULT_SCOPE:
        params["scope"] = DEFAULT_SCOPE

    return f"{IDENT_HOST}{AUTHORIZE_PATH}?{urlencode(params)}", state


def exchange_code(client_id: str, code: str, state: str) -> dict:
    """Exchange an authorization code for an access token. Returns the raw token response."""
    pending = token_store.pop_pending(state)
    if pending is None:
        raise FSAuthError("Unknown or expired OAuth state — restart sign-in at /auth/login")

    resp = httpx.post(
        f"{IDENT_HOST}{TOKEN_PATH}",
        data={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": code,
            "redirect_uri": pending["redirect_uri"],
            "code_verifier": pending["code_verifier"],
        },
        headers={"Accept": "application/json"},
        timeout=15.0,
    )
    if resp.status_code != 200:
        logger.error("FamilySearch token exchange failed: %s %s", resp.status_code, resp.text)
        raise FSAuthError(f"Token exchange failed: HTTP {resp.status_code}")

    token = resp.json()
    logger.info(
        "FamilySearch OAuth token granted. scope=%r expires_in=%s",
        token.get("scope", ""),
        token.get("expires_in"),
    )
    return token


def fetch_current_user(access_token: str) -> dict:
    """Fetch the current user's profile (raw parsed JSON, shape unconfirmed until first live call)."""
    resp = httpx.get(
        f"{API_HOST}{CURRENT_USER_PATH}",
        headers={"Accept": "application/json", "Authorization": f"Bearer {access_token}"},
        timeout=15.0,
    )
    if resp.status_code != 200:
        logger.error("FamilySearch current-user fetch failed: %s %s", resp.status_code, resp.text)
        raise FSAuthError(f"Current-user fetch failed: HTTP {resp.status_code}")
    return resp.json()


def display_name_from_user_response(data: dict) -> str:
    """Best-effort extraction of a display name. FS wraps the user in a `users` array in
    some resource representations and not others — try both shapes rather than assume."""
    users = data.get("users")
    user = users[0] if users else data
    return user.get("displayName") or user.get("contactName") or user.get("givenName") or "FamilySearch user"


def store_session(session_id: str, token: dict, display_name: str) -> None:
    token_store.put_session(session_id, token, display_name)


def get_session(session_id: str) -> dict | None:
    """Active use of the session — slides the idle window forward."""
    return token_store.get_session(session_id)


def peek_session(session_id: str) -> dict | None:
    """Passive read (header badge). Does not slide the idle window, so an idle tab left
    open on the booth kiosk still times out on schedule."""
    return token_store.peek_session(session_id)


def clear_session(session_id: str) -> None:
    token_store.clear_session(session_id)
