"""Tests for viewer access checks against the co-located Forgejo (ifcurl-6ow).

A fake Forgejo stands in for ``httpx.get`` in :mod:`ifcurl.access`: the web
route honours a session cookie, the API route honours an Authorization header,
and REQUIRE_SIGNIN_VIEW redirects anonymous web requests to /user/login.
"""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from ifcurl import access
from ifcurl.service import (
    _rate_hits,
    _t2_cache,
    _t3_cache,
    _t4_put,
    _t4m_put,
    app,
    configure_allowed_hosts,
)

client = TestClient(app)

HOST = "git.example.com"
FAKE_HEXSHA = "a" * 40
OTHER_HEXSHA = "b" * 40
FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64

PUBLIC_URL = f"ifc://{HOST}/org/public@{FAKE_HEXSHA}?path=model.ifc"
PRIVATE_URL = f"ifc://{HOST}/org/private@{FAKE_HEXSHA}?path=model.ifc"
PRIVATE_MUTABLE_URL = f"ifc://{HOST}/org/private@heads/main?path=model.ifc"
PRIVATE_SELECTOR_URL = f"ifc://{HOST}/org/private@heads/main?path=model.ifc&selector=IfcWall&query=Name"

ALICE_COOKIE = "i_like_gitea=alice-session"
BOB_COOKIE = "i_like_gitea=bob-session"
ALICE_TOKEN = "alice-pat"


class FakeForgejo:
    """Minimal Forgejo: org/public is public, org/private is readable by alice only."""

    def __init__(self) -> None:
        self.require_signin = False
        self.calls: list[str] = []
        self.down = False

    def _user(self, headers: dict) -> str | None:
        auth = headers.get("Authorization", "")
        if auth in (f"token {ALICE_TOKEN}", f"Bearer {ALICE_TOKEN}"):
            return "alice"
        cookie = headers.get("Cookie", "")
        if cookie == ALICE_COOKIE:
            return "alice"
        if cookie == BOB_COOKIE:
            return "bob"
        return None

    def get(self, url, headers=None, timeout=None, follow_redirects=True):
        self.calls.append(url)
        if self.down:
            raise httpx.ConnectError("connection refused")
        headers = headers or {}
        assert follow_redirects is False
        is_api = "/api/v1/repos/" in url
        user = self._user(headers)
        repo = url.rsplit("/", 2)[-2:]
        if self.require_signin and user is None:
            if is_api:
                return httpx.Response(401)
            return httpx.Response(303, headers={"Location": "/user/login"})
        if repo == ["org", "public"]:
            return httpx.Response(200)
        if repo == ["org", "private"] and user == "alice":
            return httpx.Response(200)
        return httpx.Response(404)


@pytest.fixture
def forgejo(monkeypatch):
    fake = FakeForgejo()
    monkeypatch.setattr(access.httpx, "get", fake.get)
    return fake


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr("ifcurl.service.user_cache_dir", lambda *a, **kw: str(tmp_path))
    monkeypatch.setattr(access, "_FORGEJO_HOSTS", frozenset({HOST}))
    monkeypatch.setattr(access, "get_token_for_host", lambda host: "service-token")
    monkeypatch.setattr("ifcurl.service.run_sandboxed", lambda fn, *a, **kw: fn(*a))
    configure_allowed_hosts({HOST, "other.example.com"})
    for c in (_t2_cache, _t3_cache, _rate_hits, access._access_cache):
        c.clear()
    yield
    configure_allowed_hosts(None)
    access._access_cache.clear()


def _get(path: str, cookie: str | None = None, **params):
    headers = {"Cookie": cookie} if cookie else {}
    return client.get(path, params=params, headers=headers)


def _no_fetch(*a, **kw):
    raise AssertionError("fetch_ifc must not be called for a denied request")


# ---------------------------------------------------------------------------
# /preview — acceptance matrix
# ---------------------------------------------------------------------------


class TestPreviewAccess:
    def test_anonymous_public_repo_renders(self, forgejo):
        _t4_put(PUBLIC_URL, FAKE_PNG)
        r = _get("/preview", url=PUBLIC_URL)
        assert r.status_code == 200
        assert r.content == FAKE_PNG

    def test_anonymous_private_repo_404(self, forgejo):
        _t4_put(PRIVATE_URL, FAKE_PNG)  # a render is already cached
        with patch("ifcurl.service.fetch_ifc", _no_fetch):
            r = _get("/preview", url=PRIVATE_URL)
        assert r.status_code == 404
        assert r.content != FAKE_PNG

    def test_require_signin_view_denies_everything(self, forgejo):
        forgejo.require_signin = True
        _t4_put(PUBLIC_URL, FAKE_PNG)
        _t4_put(PRIVATE_URL, FAKE_PNG)
        with patch("ifcurl.service.fetch_ifc", _no_fetch):
            assert _get("/preview", url=PUBLIC_URL).status_code == 404
            assert _get("/preview", url=PRIVATE_URL).status_code == 404

    def test_require_signin_view_logged_in_user_renders(self, forgejo):
        forgejo.require_signin = True
        _t4_put(PUBLIC_URL, FAKE_PNG)
        assert _get("/preview", cookie=BOB_COOKIE, url=PUBLIC_URL).status_code == 200

    def test_logged_in_reader_renders_with_stored_token(self, forgejo, model_with_geometry):
        ifc_bytes = model_with_geometry.to_string().encode()
        tokens = []

        def fetch(ifc_url, token=None):
            tokens.append(token)
            return FAKE_HEXSHA, ifc_bytes, False

        with (
            patch("ifcurl.service.fetch_ifc", fetch),
            patch("ifcurl.render.render", return_value=FAKE_PNG),
        ):
            r = _get("/preview", cookie=ALICE_COOKIE, url=PRIVATE_MUTABLE_URL)
        assert r.status_code == 200
        assert tokens == ["service-token"]
        assert r.headers["cache-control"].startswith("private")

    def test_logged_in_non_reader_404_even_when_cached(self, forgejo):
        _t4_put(PRIVATE_URL, FAKE_PNG)
        _t4m_put(PRIVATE_MUTABLE_URL, FAKE_PNG)
        assert _get("/preview", cookie=ALICE_COOKIE, url=PRIVATE_URL).status_code == 200
        with patch("ifcurl.service.fetch_ifc", _no_fetch):
            assert _get("/preview", cookie=BOB_COOKIE, url=PRIVATE_URL).status_code == 404
            assert _get("/preview", cookie=BOB_COOKIE, url=PRIVATE_MUTABLE_URL).status_code == 404

    def test_web_route_used_for_cookie_check(self, forgejo):
        _t4_put(PRIVATE_URL, FAKE_PNG)
        _get("/preview", cookie=ALICE_COOKIE, url=PRIVATE_URL)
        assert forgejo.calls == ["http://localhost:3000/org/private"]

    def test_cached_render_is_private(self, forgejo):
        _t4_put(PUBLIC_URL, FAKE_PNG)
        r = _get("/preview", url=PUBLIC_URL)
        assert r.headers["cache-control"].startswith("private")

    def test_forgejo_unreachable_fails_closed(self, forgejo):
        forgejo.down = True
        _t4_put(PUBLIC_URL, FAKE_PNG)
        with patch("ifcurl.service.fetch_ifc", _no_fetch):
            r = _get("/preview", url=PUBLIC_URL)
        assert r.status_code == 503

    def test_non_owner_repo_path_denied(self, forgejo):
        url = f"ifc://{HOST}/org/sub/private@{FAKE_HEXSHA}?path=model.ifc"
        _t4_put(url, FAKE_PNG)
        with patch("ifcurl.service.fetch_ifc", _no_fetch):
            assert _get("/preview", cookie=ALICE_COOKIE, url=url).status_code == 404
        assert forgejo.calls == []


# ---------------------------------------------------------------------------
# Explicit token
# ---------------------------------------------------------------------------


class TestExplicitToken:
    def test_valid_token_used_as_is(self, forgejo, model_with_geometry):
        ifc_bytes = model_with_geometry.to_string().encode()
        tokens = []

        def fetch(ifc_url, token=None):
            tokens.append(token)
            return FAKE_HEXSHA, ifc_bytes, False

        with (
            patch("ifcurl.service.fetch_ifc", fetch),
            patch("ifcurl.render.render", return_value=FAKE_PNG),
        ):
            r = _get("/preview", url=PRIVATE_MUTABLE_URL, token=ALICE_TOKEN)
        assert r.status_code == 200
        assert tokens == [ALICE_TOKEN]
        assert forgejo.calls == ["http://localhost:3000/api/v1/repos/org/private"]

    def test_bogus_token_does_not_unlock_cached_render(self, forgejo):
        _t4_put(PRIVATE_URL, FAKE_PNG)
        with patch("ifcurl.service.fetch_ifc", _no_fetch):
            r = _get("/preview", url=PRIVATE_URL, token="bogus")
        assert r.status_code == 404

    def test_authorization_header_checked_via_api(self, forgejo):
        _t4_put(PRIVATE_URL, FAKE_PNG)
        r = client.get(
            "/preview",
            params={"url": PRIVATE_URL},
            headers={"Authorization": f"Bearer {ALICE_TOKEN}"},
        )
        assert r.status_code == 200
        assert forgejo.calls == ["http://localhost:3000/api/v1/repos/org/private"]


# ---------------------------------------------------------------------------
# Other hosts
# ---------------------------------------------------------------------------


class TestNonForgejoHost:
    def test_stored_token_never_used(self, forgejo, model_with_geometry):
        ifc_bytes = model_with_geometry.to_string().encode()
        tokens = []

        def fetch(ifc_url, token=None):
            tokens.append(token)
            return FAKE_HEXSHA, ifc_bytes, False

        with (
            patch("ifcurl.service.fetch_ifc", fetch),
            patch("ifcurl.render.render", return_value=FAKE_PNG),
        ):
            r = _get("/preview", url="ifc://other.example.com/org/repo@heads/main?path=model.ifc")
        assert r.status_code == 200
        assert tokens == [None]
        assert forgejo.calls == []
        assert r.headers["cache-control"].startswith("public")

    @pytest.mark.parametrize(
        "path,params",
        [
            ("/preview", {"url": "ifc://other.example.com/org/repo@heads/main?path=model.ifc"}),
            ("/select", {"url": "ifc://other.example.com/org/repo@heads/main?path=model.ifc&selector=IfcWall"}),
            (
                "/render_diff",
                {
                    "base": f"ifc://other.example.com/org/repo@{FAKE_HEXSHA}?path=model.ifc",
                    "head": f"ifc://other.example.com/org/repo@{OTHER_HEXSHA}?path=model.ifc",
                },
            ),
        ],
    )
    def test_explicit_token_refused(self, forgejo, path, params):
        """Caches are keyed without credentials, so a token-fetched render would
        be served to later anonymous callers (ifcurl-e78)."""
        with patch("ifcurl.service.fetch_ifc", _no_fetch):
            r = _get(path, token="secret", **params)
        assert r.status_code == 400

    def test_explicit_token_refused_even_when_cached(self, forgejo):
        url = f"ifc://other.example.com/org/repo@{FAKE_HEXSHA}?path=model.ifc"
        _t4_put(url, FAKE_PNG)
        assert _get("/preview", url=url, token="secret").status_code == 400

    def test_ssh_transport_refused(self, forgejo):
        """The service's SSH key is a standing credential like the stored token."""
        url = f"ifc://git@other.example.com/org/repo@{FAKE_HEXSHA}?path=model.ifc"
        _t4_put(url, FAKE_PNG)
        with patch("ifcurl.service.fetch_ifc", _no_fetch):
            r = _get("/preview", url=url)
        assert r.status_code == 403
        assert r.content != FAKE_PNG

    def test_ssh_transport_allowed_on_forgejo_after_check(self, forgejo):
        url = f"ifc://git@{HOST}/org/private@{FAKE_HEXSHA}?path=model.ifc"
        _t4_put(url, FAKE_PNG)
        assert _get("/preview", url=url).status_code == 404
        assert _get("/preview", cookie=ALICE_COOKIE, url=url).status_code == 200


# ---------------------------------------------------------------------------
# /render_diff
# ---------------------------------------------------------------------------


class TestRenderDiffAccess:
    @pytest.mark.parametrize(
        "base,head",
        [
            (PUBLIC_URL, PRIVATE_URL.replace(FAKE_HEXSHA, OTHER_HEXSHA)),
            (PRIVATE_URL, PUBLIC_URL.replace(FAKE_HEXSHA, OTHER_HEXSHA)),
        ],
    )
    def test_one_denied_url_denies(self, forgejo, base, head):
        _t4_put(f"diff:{base}:{head}", FAKE_PNG)
        with patch("ifcurl.service.fetch_ifc", _no_fetch):
            r = _get("/render_diff", base=base, head=head)
        assert r.status_code == 404

    def test_both_allowed_serves_cached(self, forgejo):
        base, head = PRIVATE_URL, PRIVATE_URL.replace(FAKE_HEXSHA, OTHER_HEXSHA)
        _t4_put(f"diff:{base}:{head}", FAKE_PNG)
        r = _get("/render_diff", cookie=ALICE_COOKIE, base=base, head=head)
        assert r.status_code == 200
        assert r.content == FAKE_PNG


# ---------------------------------------------------------------------------
# /select, /query, /clash
# ---------------------------------------------------------------------------


class TestJsonEndpointsAccess:
    @pytest.mark.parametrize("path", ["/select", "/query", "/clash"])
    def test_denied(self, forgejo, path):
        with patch("ifcurl.service.fetch_ifc", _no_fetch):
            r = _get(path, cookie=BOB_COOKIE, url=PRIVATE_SELECTOR_URL)
        assert r.status_code == 404

    def test_select_allowed_uses_stored_token(self, forgejo, model_with_geometry):
        ifc_bytes = model_with_geometry.to_string().encode()
        tokens = []

        def fetch(ifc_url, token=None):
            tokens.append(token)
            return FAKE_HEXSHA, ifc_bytes, False

        with patch("ifcurl.service.fetch_ifc", fetch):
            r = _get("/select", cookie=ALICE_COOKIE, url=PRIVATE_SELECTOR_URL)
        assert r.status_code == 200
        assert tokens == ["service-token"]


# ---------------------------------------------------------------------------
# Decision cache
# ---------------------------------------------------------------------------


class TestDecisionCache:
    def test_decision_cached_per_credential(self, forgejo):
        _t4_put(PRIVATE_URL, FAKE_PNG)
        for _ in range(3):
            _get("/preview", cookie=ALICE_COOKIE, url=PRIVATE_URL)
        assert len(forgejo.calls) == 1
        _get("/preview", cookie=BOB_COOKIE, url=PRIVATE_URL)
        assert len(forgejo.calls) == 2

    def test_cache_disabled_with_zero_ttl(self, forgejo, monkeypatch):
        monkeypatch.setattr(access, "_ACCESS_TTL", 0)
        _t4_put(PRIVATE_URL, FAKE_PNG)
        for _ in range(2):
            _get("/preview", cookie=ALICE_COOKIE, url=PRIVATE_URL)
        assert len(forgejo.calls) == 2
