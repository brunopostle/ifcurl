"""Tests for ifcurl.service — POST /preview endpoint and caching."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from ifcurl.service import _t2_cache, _t3_cache, _t2_get, _t2_put, app, configure_allowed_hosts

client = TestClient(app)

# Fake values used across tests
FAKE_HEXSHA = "a" * 40
FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64

MUTABLE_URL = "ifc://example.com/org/repo@heads/main?path=model.ifc"
IMMUTABLE_URL = f"ifc://example.com/org/repo@{FAKE_HEXSHA}?path=model.ifc"
# Mutable ref so tier-4 is never checked, keeping tier-3 tests hermetic
SELECTOR_URL = "ifc://example.com/org/repo@heads/main?path=model.ifc&selector=IfcWall"


@pytest.fixture(autouse=True)
def clear_caches(tmp_path, monkeypatch):
    """Reset in-memory caches, redirect filesystem PNG cache, and reset allowed-hosts between tests."""
    monkeypatch.setattr("ifcurl.service.user_cache_dir", lambda *a, **kw: str(tmp_path))
    _t2_cache.clear()
    _t3_cache.clear()
    configure_allowed_hosts(None)
    yield
    _t2_cache.clear()
    _t3_cache.clear()
    configure_allowed_hosts(None)


@pytest.fixture()
def tmp_t4_cache(tmp_path):
    """Return the temp cache directory (already redirected by clear_caches)."""
    return tmp_path


def _mock_fetch(ifc_bytes: bytes):
    return lambda ifc_url, token=None: (FAKE_HEXSHA, ifc_bytes)


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


class TestValidation:
    def test_invalid_url_scheme_returns_400(self):
        r = client.post("/preview", json={"url": "https://example.com/model.ifc"})
        assert r.status_code == 400

    def test_missing_url_field_returns_422(self):
        r = client.post("/preview", json={})
        assert r.status_code == 422

    def test_url_without_path_param_returns_400(self):
        r = client.post("/preview", json={"url": "ifc://example.com/org/repo@heads/main"})
        assert r.status_code == 400
        assert "path" in r.json()["detail"]

    def test_malformed_url_returns_400(self):
        r = client.post("/preview", json={"url": "ifc://example.com/repo_no_ref?path=m.ifc"})
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# Successful render
# ---------------------------------------------------------------------------


class TestPreviewRender:
    def test_returns_png_content_type(self, model_with_geometry):
        ifc_bytes = model_with_geometry.to_string().encode()
        with patch("ifcurl.service.fetch_ifc", _mock_fetch(ifc_bytes)), \
             patch("ifcurl.service.render_mod.render", return_value=FAKE_PNG):
            r = client.post("/preview", json={"url": MUTABLE_URL})
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/png"
        assert r.content == FAKE_PNG

    def test_fetch_error_returns_404(self):
        with patch("ifcurl.service.fetch_ifc", side_effect=ValueError("Ref not found")):
            r = client.post("/preview", json={"url": MUTABLE_URL})
        assert r.status_code == 404

    def test_render_error_returns_422(self, model_with_geometry):
        ifc_bytes = model_with_geometry.to_string().encode()
        with patch("ifcurl.service.fetch_ifc", _mock_fetch(ifc_bytes)), \
             patch("ifcurl.service.render_mod.render", side_effect=ValueError("No geometry")):
            r = client.post("/preview", json={"url": MUTABLE_URL})
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# Tier 2: byte cache
# ---------------------------------------------------------------------------


class TestTier2Cache:
    def test_bytes_cached_after_first_request(self, model_with_geometry):
        ifc_bytes = model_with_geometry.to_string().encode()
        with patch("ifcurl.service.fetch_ifc", _mock_fetch(ifc_bytes)) as mock_fetch, \
             patch("ifcurl.service.render_mod.render", return_value=FAKE_PNG):
            client.post("/preview", json={"url": MUTABLE_URL})
        assert (FAKE_HEXSHA, "model.ifc") in _t2_cache

    def test_second_request_uses_cached_bytes(self, model_with_geometry, monkeypatch):
        ifc_bytes = model_with_geometry.to_string().encode()
        call_count = 0

        def counting_fetch(ifc_url, token=None):
            nonlocal call_count
            call_count += 1
            return FAKE_HEXSHA, ifc_bytes

        # Disable tier-4m so both requests reach fetch_ifc, letting us
        # verify that tier-2 serves ifc_bytes from cache on the second call.
        monkeypatch.setattr("ifcurl.service._T4M_TTL", 0)
        with patch("ifcurl.service.fetch_ifc", counting_fetch), \
             patch("ifcurl.service.render_mod.render", return_value=FAKE_PNG):
            client.post("/preview", json={"url": MUTABLE_URL})
            client.post("/preview", json={"url": MUTABLE_URL})
        # fetch_ifc still called both times (for the commit hexsha),
        # but _t2_cache hit means ifc_bytes comes from cache on 2nd call
        assert call_count == 2
        assert (FAKE_HEXSHA, "model.ifc") in _t2_cache


# ---------------------------------------------------------------------------
# Tier 3: GUID/selector cache
# ---------------------------------------------------------------------------


class TestTier3Cache:
    def test_guids_cached_after_selector_request(self, model_with_geometry):
        ifc_bytes = model_with_geometry.to_string().encode()
        with patch("ifcurl.service.fetch_ifc", _mock_fetch(ifc_bytes)), \
             patch("ifcurl.service.render_mod.render", return_value=FAKE_PNG):
            client.post("/preview", json={"url": SELECTOR_URL})
        assert (FAKE_HEXSHA, "model.ifc", "IfcWall") in _t3_cache

    def test_cached_guids_are_frozenset(self, model_with_geometry):
        ifc_bytes = model_with_geometry.to_string().encode()
        with patch("ifcurl.service.fetch_ifc", _mock_fetch(ifc_bytes)), \
             patch("ifcurl.service.render_mod.render", return_value=FAKE_PNG):
            client.post("/preview", json={"url": SELECTOR_URL})
        cached = _t3_cache[(FAKE_HEXSHA, "model.ifc", "IfcWall")]
        assert isinstance(cached, frozenset)

    def test_second_selector_request_skips_filter_elements(self, model_with_geometry):
        ifc_bytes = model_with_geometry.to_string().encode()
        filter_call_count = 0
        real_filter = __import__("ifcopenshell.util.selector", fromlist=["filter_elements"]).filter_elements

        def counting_filter(model, selector):
            nonlocal filter_call_count
            filter_call_count += 1
            return real_filter(model, selector)

        with patch("ifcurl.service.fetch_ifc", _mock_fetch(ifc_bytes)), \
             patch("ifcurl.service.render_mod.render", return_value=FAKE_PNG), \
             patch("ifcopenshell.util.selector.filter_elements", counting_filter):
            client.post("/preview", json={"url": SELECTOR_URL})
            client.post("/preview", json={"url": SELECTOR_URL})

        assert filter_call_count == 1  # only called on first request


# ---------------------------------------------------------------------------
# Tier 4: PNG filesystem cache
# ---------------------------------------------------------------------------


class TestTier4Cache:
    def test_png_cached_for_immutable_ref(self, tmp_t4_cache, model_with_geometry):
        ifc_bytes = model_with_geometry.to_string().encode()
        render_call_count = 0

        def counting_render(*args, **kwargs):
            nonlocal render_call_count
            render_call_count += 1
            return FAKE_PNG

        with patch("ifcurl.service.fetch_ifc", _mock_fetch(ifc_bytes)), \
             patch("ifcurl.service.render_mod.render", counting_render):
            r1 = client.post("/preview", json={"url": IMMUTABLE_URL})
            r2 = client.post("/preview", json={"url": IMMUTABLE_URL})

        assert r1.content == FAKE_PNG
        assert r2.content == FAKE_PNG
        assert render_call_count == 1  # render only called once

    def test_png_cached_for_mutable_ref_within_ttl(self, tmp_t4_cache, model_with_geometry):
        ifc_bytes = model_with_geometry.to_string().encode()
        render_call_count = 0

        def counting_render(*args, **kwargs):
            nonlocal render_call_count
            render_call_count += 1
            return FAKE_PNG

        with patch("ifcurl.service.fetch_ifc", _mock_fetch(ifc_bytes)), \
             patch("ifcurl.service.render_mod.render", counting_render):
            client.post("/preview", json={"url": MUTABLE_URL})
            client.post("/preview", json={"url": MUTABLE_URL})

        assert render_call_count == 1  # second request served from tier-4m cache

    def test_png_not_cached_for_mutable_ref_after_ttl(self, tmp_t4_cache, model_with_geometry, monkeypatch):
        ifc_bytes = model_with_geometry.to_string().encode()
        render_call_count = 0

        def counting_render(*args, **kwargs):
            nonlocal render_call_count
            render_call_count += 1
            return FAKE_PNG

        monkeypatch.setattr("ifcurl.service._T4M_TTL", 0)
        with patch("ifcurl.service.fetch_ifc", _mock_fetch(ifc_bytes)), \
             patch("ifcurl.service.render_mod.render", counting_render):
            client.post("/preview", json={"url": MUTABLE_URL})
            client.post("/preview", json={"url": MUTABLE_URL})

        assert render_call_count == 2  # TTL=0 means cache always expired

    def test_tier4_cache_file_exists_on_disk(self, tmp_t4_cache, model_with_geometry):
        ifc_bytes = model_with_geometry.to_string().encode()
        with patch("ifcurl.service.fetch_ifc", _mock_fetch(ifc_bytes)), \
             patch("ifcurl.service.render_mod.render", return_value=FAKE_PNG):
            client.post("/preview", json={"url": IMMUTABLE_URL})
        png_files = list(tmp_t4_cache.rglob("*.png"))
        assert len(png_files) == 1
        assert png_files[0].read_bytes() == FAKE_PNG


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


class TestAuthentication:
    def test_request_token_passed_to_fetch_ifc(self, model_with_geometry):
        ifc_bytes = model_with_geometry.to_string().encode()
        received_token = []

        def capturing_fetch(ifc_url, token=None):
            received_token.append(token)
            return FAKE_HEXSHA, ifc_bytes

        with patch("ifcurl.service.fetch_ifc", capturing_fetch), \
             patch("ifcurl.service.render_mod.render", return_value=FAKE_PNG):
            client.post("/preview", json={"url": MUTABLE_URL, "token": "mytoken123"})

        assert received_token == ["mytoken123"]

    def test_config_token_used_when_no_request_token(self, model_with_geometry, monkeypatch):
        ifc_bytes = model_with_geometry.to_string().encode()
        received_token = []

        def capturing_fetch(ifc_url, token=None):
            received_token.append(token)
            return FAKE_HEXSHA, ifc_bytes

        monkeypatch.setattr("ifcurl.service.get_token_for_host", lambda host: "config_token")
        with patch("ifcurl.service.fetch_ifc", capturing_fetch), \
             patch("ifcurl.service.render_mod.render", return_value=FAKE_PNG):
            client.post("/preview", json={"url": MUTABLE_URL})

        assert received_token == ["config_token"]

    def test_request_token_overrides_config_token(self, model_with_geometry, monkeypatch):
        ifc_bytes = model_with_geometry.to_string().encode()
        received_token = []

        def capturing_fetch(ifc_url, token=None):
            received_token.append(token)
            return FAKE_HEXSHA, ifc_bytes

        monkeypatch.setattr("ifcurl.service.get_token_for_host", lambda host: "config_token")
        with patch("ifcurl.service.fetch_ifc", capturing_fetch), \
             patch("ifcurl.service.render_mod.render", return_value=FAKE_PNG):
            client.post("/preview", json={"url": MUTABLE_URL, "token": "request_token"})

        assert received_token == ["request_token"]


# ---------------------------------------------------------------------------
# GET /preview endpoint
# ---------------------------------------------------------------------------


class TestGetEndpoint:
    def test_get_returns_png(self, model_with_geometry):
        ifc_bytes = model_with_geometry.to_string().encode()
        with patch("ifcurl.service.fetch_ifc", _mock_fetch(ifc_bytes)), \
             patch("ifcurl.service.render_mod.render", return_value=FAKE_PNG):
            r = client.get("/preview", params={"url": MUTABLE_URL})
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/png"
        assert r.content == FAKE_PNG

    def test_get_invalid_scheme_returns_400(self):
        r = client.get("/preview", params={"url": "https://example.com/model.ifc"})
        assert r.status_code == 400

    def test_get_local_transport_rejected(self):
        r = client.get("/preview", params={"url": "ifc:///some/path@HEAD?path=m.ifc"})
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# Tier 2: LRU eviction
# ---------------------------------------------------------------------------


class TestTier2Eviction:
    def test_oldest_entry_evicted_at_max(self, monkeypatch):
        monkeypatch.setattr("ifcurl.service._T2_MAX", 2)
        _t2_put("sha_a", "a.ifc", b"data_a")
        _t2_put("sha_b", "b.ifc", b"data_b")
        _t2_put("sha_c", "c.ifc", b"data_c")  # should evict sha_a
        assert _t2_get("sha_a", "a.ifc") is None
        assert _t2_get("sha_b", "b.ifc") == b"data_b"
        assert _t2_get("sha_c", "c.ifc") == b"data_c"

    def test_get_promotes_entry(self, monkeypatch):
        monkeypatch.setattr("ifcurl.service._T2_MAX", 2)
        _t2_put("sha_a", "a.ifc", b"data_a")
        _t2_put("sha_b", "b.ifc", b"data_b")
        _t2_get("sha_a", "a.ifc")             # promote sha_a to most-recent
        _t2_put("sha_c", "c.ifc", b"data_c")  # should evict sha_b (now oldest)
        assert _t2_get("sha_a", "a.ifc") == b"data_a"
        assert _t2_get("sha_b", "b.ifc") is None
        assert _t2_get("sha_c", "c.ifc") == b"data_c"

    def test_cache_miss_returns_none(self):
        assert _t2_get("nonexistent", "x.ifc") is None


# ---------------------------------------------------------------------------
# SSRF protection
# ---------------------------------------------------------------------------


class TestSSRF:
    def test_local_transport_always_rejected(self):
        r = client.post("/preview", json={"url": "ifc:///some/path@HEAD?path=m.ifc"})
        assert r.status_code == 403
        assert "local" in r.json()["detail"].lower()

    def test_host_not_in_allowlist_rejected(self):
        configure_allowed_hosts({"allowed.example.com"})
        r = client.post("/preview", json={"url": MUTABLE_URL})  # host: example.com
        assert r.status_code == 403
        assert "allowed-hosts" in r.json()["detail"].lower()

    def test_host_in_allowlist_permitted(self, model_with_geometry):
        configure_allowed_hosts({"example.com"})
        ifc_bytes = model_with_geometry.to_string().encode()
        with patch("ifcurl.service.fetch_ifc", _mock_fetch(ifc_bytes)), \
             patch("ifcurl.service.render_mod.render", return_value=FAKE_PNG):
            r = client.post("/preview", json={"url": MUTABLE_URL})
        assert r.status_code == 200

    def test_no_allowlist_permits_public_host(self, model_with_geometry):
        ifc_bytes = model_with_geometry.to_string().encode()
        with patch("ifcurl.service.fetch_ifc", _mock_fetch(ifc_bytes)), \
             patch("ifcurl.service.render_mod.render", return_value=FAKE_PNG):
            r = client.post("/preview", json={"url": MUTABLE_URL})
        assert r.status_code == 200

    def test_loopback_ip_rejected_without_allowlist(self):
        r = client.post("/preview", json={"url": "ifc://127.0.0.1/org/repo@HEAD?path=m.ifc"})
        assert r.status_code == 403

    def test_private_ip_rejected_without_allowlist(self):
        r = client.post("/preview", json={"url": "ifc://192.168.1.1/org/repo@HEAD?path=m.ifc"})
        assert r.status_code == 403

    def test_link_local_ip_rejected_without_allowlist(self):
        r = client.post("/preview", json={"url": "ifc://169.254.169.254/org/repo@HEAD?path=m.ifc"})
        assert r.status_code == 403

    def test_allowlist_with_port(self, model_with_geometry):
        configure_allowed_hosts({"localhost:3000"})
        ifc_bytes = model_with_geometry.to_string().encode()
        with patch("ifcurl.service.fetch_ifc", _mock_fetch(ifc_bytes)), \
             patch("ifcurl.service.render_mod.render", return_value=FAKE_PNG):
            r = client.post("/preview", json={
                "url": "ifc://localhost:3000/org/repo@heads/main?path=model.ifc"
            })
        assert r.status_code == 200
