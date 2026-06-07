"""Tests for ifcurl.foundation_api — OpenCDE Foundation API 1.1 OAuth2 routes."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import ifcurl.foundation_api as foundation_api
from ifcurl.service import _rate_hits, app

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_rate_limit():
    _rate_hits.clear()
    yield
    _rate_hits.clear()


@pytest.fixture()
def with_credentials(monkeypatch):
    monkeypatch.setattr(foundation_api, "_CLIENT_ID", "test-client-id")
    monkeypatch.setattr(foundation_api, "_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setattr(foundation_api, "_FORGEJO_URL", "http://forgejo.example.com")


# ---------------------------------------------------------------------------
# GET /foundation/1.1/oauth2/auth_url
# ---------------------------------------------------------------------------

class TestAuthUrl:
    def test_returns_200(self, with_credentials):
        r = client.get("/foundation/1.1/oauth2/auth_url")
        assert r.status_code == 200

    def test_returns_url_and_expires_in(self, with_credentials):
        r = client.get("/foundation/1.1/oauth2/auth_url")
        data = r.json()
        assert "url" in data
        assert "expires_in" in data

    def test_url_contains_client_id(self, with_credentials):
        r = client.get("/foundation/1.1/oauth2/auth_url")
        assert "test-client-id" in r.json()["url"]

    def test_url_contains_response_type_code(self, with_credentials):
        r = client.get("/foundation/1.1/oauth2/auth_url")
        assert "response_type=code" in r.json()["url"]

    def test_url_points_to_forgejo_authorize(self, with_credentials):
        r = client.get("/foundation/1.1/oauth2/auth_url")
        assert r.json()["url"].startswith("http://forgejo.example.com/login/oauth/authorize")

    def test_redirect_uri_forwarded(self, with_credentials):
        r = client.get(
            "/foundation/1.1/oauth2/auth_url",
            params={"redirect_uri": "https://app.example.com/callback"},
        )
        assert "redirect_uri=" in r.json()["url"]
        assert "app.example.com" in r.json()["url"]

    def test_no_redirect_uri_omits_param(self, with_credentials):
        r = client.get("/foundation/1.1/oauth2/auth_url")
        assert "redirect_uri" not in r.json()["url"]

    def test_503_when_not_configured(self, monkeypatch):
        monkeypatch.setattr(foundation_api, "_CLIENT_ID", None)
        monkeypatch.setattr(foundation_api, "_CLIENT_SECRET", None)
        r = client.get("/foundation/1.1/oauth2/auth_url")
        assert r.status_code == 503


# ---------------------------------------------------------------------------
# POST /foundation/1.1/oauth2/token
# ---------------------------------------------------------------------------

def _mock_forgejo_token_ok():
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "access_token": "ACCESS",
        "token_type": "bearer",
        "expires_in": 3600,
        "refresh_token": "REFRESH",
    }
    return resp


class TestToken:
    def test_returns_200(self, with_credentials):
        with patch("ifcurl.foundation_api.httpx.post", return_value=_mock_forgejo_token_ok()):
            r = client.post("/foundation/1.1/oauth2/token", json={"code": "abc123"})
        assert r.status_code == 200

    def test_returns_access_token(self, with_credentials):
        with patch("ifcurl.foundation_api.httpx.post", return_value=_mock_forgejo_token_ok()):
            r = client.post("/foundation/1.1/oauth2/token", json={"code": "abc123"})
        assert r.json()["access_token"] == "ACCESS"

    def test_proxies_authorization_code_grant(self, with_credentials):
        mock_post = MagicMock(return_value=_mock_forgejo_token_ok())
        with patch("ifcurl.foundation_api.httpx.post", mock_post):
            client.post("/foundation/1.1/oauth2/token", json={"code": "abc123"})
        payload = mock_post.call_args.kwargs["json"]
        assert payload["grant_type"] == "authorization_code"
        assert payload["code"] == "abc123"
        assert payload["client_id"] == "test-client-id"
        assert payload["client_secret"] == "test-client-secret"

    def test_redirect_uri_forwarded(self, with_credentials):
        mock_post = MagicMock(return_value=_mock_forgejo_token_ok())
        with patch("ifcurl.foundation_api.httpx.post", mock_post):
            client.post(
                "/foundation/1.1/oauth2/token",
                json={"code": "abc123", "redirect_uri": "https://app.example.com/cb"},
            )
        payload = mock_post.call_args.kwargs["json"]
        assert payload["redirect_uri"] == "https://app.example.com/cb"

    def test_no_redirect_uri_omitted(self, with_credentials):
        mock_post = MagicMock(return_value=_mock_forgejo_token_ok())
        with patch("ifcurl.foundation_api.httpx.post", mock_post):
            client.post("/foundation/1.1/oauth2/token", json={"code": "abc123"})
        payload = mock_post.call_args.kwargs["json"]
        assert "redirect_uri" not in payload

    def test_forgejo_error_propagated(self, with_credentials):
        err_resp = MagicMock()
        err_resp.status_code = 401
        err_resp.text = "invalid_grant"
        with patch("ifcurl.foundation_api.httpx.post", return_value=err_resp):
            r = client.post("/foundation/1.1/oauth2/token", json={"code": "bad"})
        assert r.status_code == 401

    def test_forgejo_unreachable_returns_503(self, with_credentials):
        import httpx as _httpx
        with patch("ifcurl.foundation_api.httpx.post", side_effect=_httpx.ConnectError("refused")):
            r = client.post("/foundation/1.1/oauth2/token", json={"code": "abc123"})
        assert r.status_code == 503

    def test_503_when_not_configured(self, monkeypatch):
        monkeypatch.setattr(foundation_api, "_CLIENT_ID", None)
        monkeypatch.setattr(foundation_api, "_CLIENT_SECRET", None)
        r = client.post("/foundation/1.1/oauth2/token", json={"code": "abc123"})
        assert r.status_code == 503

    def test_missing_code_returns_422(self, with_credentials):
        r = client.post("/foundation/1.1/oauth2/token", json={})
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# POST /foundation/1.1/oauth2/token_refresh
# ---------------------------------------------------------------------------

class TestTokenRefresh:
    def test_returns_200(self, with_credentials):
        with patch("ifcurl.foundation_api.httpx.post", return_value=_mock_forgejo_token_ok()):
            r = client.post(
                "/foundation/1.1/oauth2/token_refresh",
                json={"refresh_token": "REFRESH"},
            )
        assert r.status_code == 200

    def test_proxies_refresh_token_grant(self, with_credentials):
        mock_post = MagicMock(return_value=_mock_forgejo_token_ok())
        with patch("ifcurl.foundation_api.httpx.post", mock_post):
            client.post(
                "/foundation/1.1/oauth2/token_refresh",
                json={"refresh_token": "REFRESH"},
            )
        payload = mock_post.call_args.kwargs["json"]
        assert payload["grant_type"] == "refresh_token"
        assert payload["refresh_token"] == "REFRESH"
        assert payload["client_id"] == "test-client-id"
        assert payload["client_secret"] == "test-client-secret"

    def test_forgejo_error_propagated(self, with_credentials):
        err_resp = MagicMock()
        err_resp.status_code = 401
        err_resp.text = "invalid_grant"
        with patch("ifcurl.foundation_api.httpx.post", return_value=err_resp):
            r = client.post(
                "/foundation/1.1/oauth2/token_refresh",
                json={"refresh_token": "expired"},
            )
        assert r.status_code == 401

    def test_503_when_not_configured(self, monkeypatch):
        monkeypatch.setattr(foundation_api, "_CLIENT_ID", None)
        monkeypatch.setattr(foundation_api, "_CLIENT_SECRET", None)
        r = client.post(
            "/foundation/1.1/oauth2/token_refresh",
            json={"refresh_token": "REFRESH"},
        )
        assert r.status_code == 503

    def test_missing_refresh_token_returns_422(self, with_credentials):
        r = client.post("/foundation/1.1/oauth2/token_refresh", json={})
        assert r.status_code == 422
