"""tests/test_fs_auth.py — src/fs_auth.py tests, no real network calls.

Covers: authorize-URL construction (PKCE on/off — see FAMILYSEARCH_USE_PKCE diagnostic
toggle, module docstring), token exchange (PKCE on/off, unknown state), and display-name
extraction across FamilySearch's two current-user response shapes.
"""

from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from src import fs_auth


@pytest.fixture(autouse=True)
def _clear_pending():
    fs_auth._PENDING.clear()
    yield
    fs_auth._PENDING.clear()


class TestBuildAuthorizeUrl:
    def test_pkce_on_includes_challenge_and_stores_verifier(self):
        url, state = fs_auth.build_authorize_url("client-x", "http://localhost:8081/callback", use_pkce=True)
        params = parse_qs(urlparse(url).query)

        assert params["response_type"] == ["code"]
        assert params["client_id"] == ["client-x"]
        assert params["redirect_uri"] == ["http://localhost:8081/callback"]
        assert params["state"] == [state]
        assert params["code_challenge_method"] == ["S256"]
        assert "code_challenge" in params
        assert fs_auth._PENDING[state]["code_verifier"] is not None

    def test_pkce_off_omits_challenge_params(self):
        url, state = fs_auth.build_authorize_url("client-x", "http://localhost:8081/callback", use_pkce=False)
        params = parse_qs(urlparse(url).query)

        assert "code_challenge" not in params
        assert "code_challenge_method" not in params
        assert fs_auth._PENDING[state]["code_verifier"] is None

    def test_default_use_pkce_follows_module_default(self, monkeypatch):
        monkeypatch.setattr(fs_auth, "DEFAULT_USE_PKCE", False)
        url, _ = fs_auth.build_authorize_url("client-x", "http://localhost:8081/callback")
        assert "code_challenge" not in parse_qs(urlparse(url).query)

    def test_scope_included_when_set(self, monkeypatch):
        monkeypatch.setattr(fs_auth, "DEFAULT_SCOPE", "tree openid")
        url, _ = fs_auth.build_authorize_url("client-x", "http://localhost:8081/callback")
        assert parse_qs(urlparse(url).query)["scope"] == ["tree openid"]


class TestExchangeCode:
    def test_unknown_state_raises(self):
        with pytest.raises(fs_auth.FSAuthError, match="Unknown or expired"):
            fs_auth.exchange_code("client-x", "some-code", "never-issued-state")

    def test_pkce_on_sends_code_verifier(self, monkeypatch):
        _, state = fs_auth.build_authorize_url("client-x", "http://localhost:8081/callback", use_pkce=True)
        captured = {}

        def fake_post(url, data, headers, timeout):
            captured["data"] = data
            return httpx.Response(200, json={"access_token": "tok", "scope": "tree", "expires_in": 3600})

        monkeypatch.setattr(fs_auth.httpx, "post", fake_post)
        token = fs_auth.exchange_code("client-x", "auth-code", state)

        assert token["access_token"] == "tok"
        assert "code_verifier" in captured["data"]
        assert captured["data"]["code_verifier"] is not None

    def test_pkce_off_omits_code_verifier(self, monkeypatch):
        _, state = fs_auth.build_authorize_url("client-x", "http://localhost:8081/callback", use_pkce=False)
        captured = {}

        def fake_post(url, data, headers, timeout):
            captured["data"] = data
            return httpx.Response(200, json={"access_token": "tok"})

        monkeypatch.setattr(fs_auth.httpx, "post", fake_post)
        fs_auth.exchange_code("client-x", "auth-code", state)

        assert "code_verifier" not in captured["data"]

    def test_non_200_raises_fsautherror(self, monkeypatch):
        _, state = fs_auth.build_authorize_url("client-x", "http://localhost:8081/callback")

        def fake_post(url, data, headers, timeout):
            return httpx.Response(400, text="invalid_client")

        monkeypatch.setattr(fs_auth.httpx, "post", fake_post)
        with pytest.raises(fs_auth.FSAuthError, match="400"):
            fs_auth.exchange_code("client-x", "auth-code", state)


class TestFetchCurrentUser:
    def test_non_200_raises(self, monkeypatch):
        def fake_get(url, headers, timeout):
            return httpx.Response(401, text="expired")

        monkeypatch.setattr(fs_auth.httpx, "get", fake_get)
        with pytest.raises(fs_auth.FSAuthError, match="401"):
            fs_auth.fetch_current_user("expired-token")

    def test_200_returns_parsed_json(self, monkeypatch):
        def fake_get(url, headers, timeout):
            return httpx.Response(200, json={"users": [{"displayName": "Joel Cannon"}]})

        monkeypatch.setattr(fs_auth.httpx, "get", fake_get)
        data = fs_auth.fetch_current_user("tok")
        assert data["users"][0]["displayName"] == "Joel Cannon"


class TestDisplayNameFromUserResponse:
    def test_users_array_shape(self):
        assert fs_auth.display_name_from_user_response({"users": [{"displayName": "Joel Cannon"}]}) == "Joel Cannon"

    def test_flat_shape(self):
        assert fs_auth.display_name_from_user_response({"displayName": "Joel Cannon"}) == "Joel Cannon"

    def test_falls_back_to_contact_name(self):
        assert fs_auth.display_name_from_user_response({"contactName": "J. Cannon"}) == "J. Cannon"

    def test_falls_back_to_given_name(self):
        assert fs_auth.display_name_from_user_response({"givenName": "Joel"}) == "Joel"

    def test_falls_back_to_default(self):
        assert fs_auth.display_name_from_user_response({}) == "FamilySearch user"


class TestSessionStore:
    def test_store_get_clear_roundtrip(self):
        fs_auth.store_session("sid-1", {"access_token": "tok"}, "Joel Cannon")
        session = fs_auth.get_session("sid-1")
        assert session["display_name"] == "Joel Cannon"

        fs_auth.clear_session("sid-1")
        assert fs_auth.get_session("sid-1") is None
