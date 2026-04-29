"""Tests for ifcurl.bcf and the /bcf service endpoint."""

from __future__ import annotations

import io
import zipfile
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from ifcurl.bcf import bcf_viewpoint_to_ifc_url, build_bcf
from ifcurl.service import _t2_cache, _t3_cache, app, configure_allowed_hosts
from ifcurl.url import IfcUrl

client = TestClient(app)

FAKE_HEXSHA = "b" * 40
MUTABLE_URL = "ifc://example.com/org/repo@heads/main?path=model.ifc"
CAMERA_URL = (
    "ifc://example.com/org/repo@heads/main"
    "?path=model.ifc&camera=1,2,3,0,0,-1,0,1,0&fov=60"
)
CLIP_URL = (
    "ifc://example.com/org/repo@heads/main"
    "?path=model.ifc&camera=0,0,5,0,0,-1,0,1,0&fov=60"
    "&clip=0,0,2,0,0,-1"
)


@pytest.fixture(autouse=True)
def reset_service(tmp_path, monkeypatch):
    monkeypatch.setattr("ifcurl.service.user_cache_dir", lambda *a, **kw: str(tmp_path))
    _t2_cache.clear()
    _t3_cache.clear()
    configure_allowed_hosts(None)
    yield
    _t2_cache.clear()
    _t3_cache.clear()
    configure_allowed_hosts(None)


def _zip_names(data: bytes) -> set[str]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return set(zf.namelist())


def _zip_read(data: bytes, name: str) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return zf.read(name).decode()


# ---------------------------------------------------------------------------
# build_bcf unit tests
# ---------------------------------------------------------------------------


class TestBuildBcf:
    def test_returns_valid_zip(self):
        data = build_bcf()
        assert zipfile.is_zipfile(io.BytesIO(data))

    def test_contains_version_file(self):
        data = build_bcf()
        assert "bcf.version" in _zip_names(data)

    def test_contains_markup(self):
        data = build_bcf(title="Test topic")
        names = _zip_names(data)
        markup_files = [n for n in names if n.endswith("markup.bcf")]
        assert len(markup_files) == 1
        content = _zip_read(data, markup_files[0])
        assert "Test topic" in content

    def test_no_viewpoint_without_camera(self):
        data = build_bcf()
        names = _zip_names(data)
        assert not any(n.endswith("viewpoint.bcfv") for n in names)

    def test_viewpoint_present_with_camera(self):
        cam = (1.0, 2.0, 3.0, 0.0, 0.0, -1.0, 0.0, 1.0, 0.0)
        data = build_bcf(camera=cam, fov=60.0)
        names = _zip_names(data)
        assert any(n.endswith("viewpoint.bcfv") for n in names)

    def test_perspective_camera_in_viewpoint(self):
        cam = (1.0, 2.0, 3.0, 0.0, 0.0, -1.0, 0.0, 1.0, 0.0)
        data = build_bcf(camera=cam, fov=60.0)
        vp_file = next(n for n in _zip_names(data) if n.endswith("viewpoint.bcfv"))
        content = _zip_read(data, vp_file)
        assert "PerspectiveCamera" in content
        assert "<FieldOfView>60.0000</FieldOfView>" in content

    def test_orthographic_camera_in_viewpoint(self):
        cam = (0.0, 0.0, 10.0, 0.0, 0.0, -1.0, 0.0, 1.0, 0.0)
        data = build_bcf(camera=cam, scale=50.0)
        vp_file = next(n for n in _zip_names(data) if n.endswith("viewpoint.bcfv"))
        content = _zip_read(data, vp_file)
        assert "OrthogonalCamera" in content
        assert "ViewToWorldScale" in content

    def test_clipping_planes_in_viewpoint(self):
        cam = (0.0, 0.0, 5.0, 0.0, 0.0, -1.0, 0.0, 1.0, 0.0)
        data = build_bcf(camera=cam, fov=60.0, clips=[(0.0, 0.0, 2.0, 0.0, 0.0, -1.0)])
        vp_file = next(n for n in _zip_names(data) if n.endswith("viewpoint.bcfv"))
        content = _zip_read(data, vp_file)
        assert "ClippingPlane" in content

    def test_comment_in_markup(self):
        cam = (0.0, 0.0, 5.0, 0.0, 0.0, -1.0, 0.0, 1.0, 0.0)
        data = build_bcf(camera=cam, fov=60.0, comment="Look at this crack")
        markup = next(n for n in _zip_names(data) if n.endswith("markup.bcf"))
        content = _zip_read(data, markup)
        assert "Look at this crack" in content

    def test_guids_in_viewpoint_selection(self):
        cam = (0.0, 0.0, 5.0, 0.0, 0.0, -1.0, 0.0, 1.0, 0.0)
        data = build_bcf(camera=cam, fov=60.0, guids=["abc123", "def456"])
        vp_file = next(n for n in _zip_names(data) if n.endswith("viewpoint.bcfv"))
        content = _zip_read(data, vp_file)
        assert 'IfcGuid="abc123"' in content
        assert 'IfcGuid="def456"' in content

    def test_isolate_visibility_uses_exceptions(self):
        cam = (0.0, 0.0, 5.0, 0.0, 0.0, -1.0, 0.0, 1.0, 0.0)
        data = build_bcf(camera=cam, fov=60.0, guids=["abc123"], visibility="isolate")
        vp_file = next(n for n in _zip_names(data) if n.endswith("viewpoint.bcfv"))
        content = _zip_read(data, vp_file)
        assert 'DefaultVisibility="false"' in content
        assert "Exceptions" in content

    def test_description_in_markup(self):
        data = build_bcf(description="ifc://example.com/org/repo@heads/main?path=m.ifc")
        markup = next(n for n in _zip_names(data) if n.endswith("markup.bcf"))
        content = _zip_read(data, markup)
        assert "<Description>ifc://example.com" in content

    def test_no_description_element_when_empty(self):
        data = build_bcf()
        markup = next(n for n in _zip_names(data) if n.endswith("markup.bcf"))
        content = _zip_read(data, markup)
        assert "<Description>" not in content


# ---------------------------------------------------------------------------
# /bcf service endpoint tests
# ---------------------------------------------------------------------------


class TestBcfEndpoint:
    def test_returns_zip_bytes(self):
        r = client.post("/bcf", json={"url": CAMERA_URL})
        assert r.status_code == 200
        assert zipfile.is_zipfile(io.BytesIO(r.content))

    def test_content_disposition_header(self):
        r = client.post("/bcf", json={"url": CAMERA_URL})
        assert "view.bcf" in r.headers["content-disposition"]

    def test_contains_viewpoint_when_camera_in_url(self):
        r = client.post("/bcf", json={"url": CAMERA_URL})
        names = _zip_names(r.content)
        assert any(n.endswith("viewpoint.bcfv") for n in names)

    def test_no_viewpoint_when_no_camera(self):
        r = client.post("/bcf", json={"url": MUTABLE_URL})
        assert r.status_code == 200
        names = _zip_names(r.content)
        assert not any(n.endswith("viewpoint.bcfv") for n in names)

    def test_title_and_comment_in_markup(self):
        r = client.post(
            "/bcf", json={"url": CAMERA_URL, "title": "My issue", "comment": "Fix this"}
        )
        markup = next(n for n in _zip_names(r.content) if n.endswith("markup.bcf"))
        content = _zip_read(r.content, markup)
        assert "My issue" in content
        assert "Fix this" in content

    def test_source_url_in_description(self):
        r = client.post("/bcf", json={"url": CAMERA_URL})
        markup = next(n for n in _zip_names(r.content) if n.endswith("markup.bcf"))
        content = _zip_read(r.content, markup)
        # & is XML-escaped to &amp; in the description element
        assert CAMERA_URL.replace("&", "&amp;") in content

    def test_clip_plane_in_viewpoint(self):
        r = client.post("/bcf", json={"url": CLIP_URL})
        vp = next(n for n in _zip_names(r.content) if n.endswith("viewpoint.bcfv"))
        assert "ClippingPlane" in _zip_read(r.content, vp)

    def test_ssrf_local_transport_rejected(self):
        r = client.post("/bcf", json={"url": "ifc:///some/path@HEAD?path=m.ifc"})
        assert r.status_code == 403

    def test_ssrf_private_ip_rejected(self):
        r = client.post(
            "/bcf", json={"url": "ifc://192.168.1.1/org/repo@HEAD?path=m.ifc"}
        )
        assert r.status_code == 403

    def test_invalid_url_returns_400(self):
        r = client.post("/bcf", json={"url": "https://example.com/model.ifc"})
        assert r.status_code == 400

    def test_selector_resolves_guids(self, model_with_geometry):
        ifc_bytes = model_with_geometry.to_string().encode()

        def mock_fetch(ifc_url, token=None):
            return FAKE_HEXSHA, ifc_bytes, False

        selector_url = MUTABLE_URL.replace(
            "?path=model.ifc", "?path=model.ifc&selector=IfcWall"
        )
        with patch("ifcurl.service.fetch_ifc", mock_fetch):
            r = client.post("/bcf", json={"url": selector_url})
        assert r.status_code == 200
        vp_files = [n for n in _zip_names(r.content) if n.endswith("viewpoint.bcfv")]
        # No camera → no viewpoint even with selector
        assert len(vp_files) == 0


# ---------------------------------------------------------------------------
# bcf_viewpoint_to_ifc_url() unit tests
# ---------------------------------------------------------------------------

BASE = IfcUrl.parse("ifc://example.com/org/repo@heads/main?path=model.ifc")

PERSPECTIVE_VP = {
    "perspective_camera": {
        "camera_view_point": {"x": 1.0, "y": 2.0, "z": 3.0},
        "camera_direction": {"x": 0.0, "y": 0.0, "z": -1.0},
        "camera_up_vector": {"x": 0.0, "y": 1.0, "z": 0.0},
        "field_of_view": 60.0,
    }
}

ORTHO_VP = {
    "orthogonal_camera": {
        "camera_view_point": {"x": 0.0, "y": 0.0, "z": 10.0},
        "camera_direction": {"x": 0.0, "y": 0.0, "z": -1.0},
        "camera_up_vector": {"x": 0.0, "y": 1.0, "z": 0.0},
        "view_to_world_scale": 50.0,
    }
}


class TestBcfViewpointToIfcUrl:
    def test_perspective_camera(self):
        result = IfcUrl.parse(bcf_viewpoint_to_ifc_url(BASE, PERSPECTIVE_VP))
        assert result.camera == (1.0, 2.0, 3.0, 0.0, 0.0, -1.0, 0.0, 1.0, 0.0)
        assert result.fov == 60.0
        assert result.scale is None

    def test_orthogonal_camera(self):
        result = IfcUrl.parse(bcf_viewpoint_to_ifc_url(BASE, ORTHO_VP))
        assert result.scale == 50.0
        assert result.fov is None

    def test_preserves_base_repo_context(self):
        result = IfcUrl.parse(bcf_viewpoint_to_ifc_url(BASE, PERSPECTIVE_VP))
        assert result.host == "example.com"
        assert result.repo_path == "org/repo"
        assert result.ref == "heads/main"
        assert result.path == "model.ifc"

    def test_no_camera_gives_no_camera_params(self):
        result = IfcUrl.parse(bcf_viewpoint_to_ifc_url(BASE, {}))
        assert result.camera is None
        assert result.fov is None
        assert result.scale is None

    def test_clipping_planes(self):
        vp = {
            **PERSPECTIVE_VP,
            "clipping_planes": [
                {
                    "location": {"x": 0.0, "y": 0.0, "z": 2.0},
                    "direction": {"x": 0.0, "y": 0.0, "z": -1.0},
                }
            ],
        }
        result = IfcUrl.parse(bcf_viewpoint_to_ifc_url(BASE, vp))
        assert result.clips == [(0.0, 0.0, 2.0, 0.0, 0.0, -1.0)]

    def test_selection_becomes_selector(self):
        vp = {
            **PERSPECTIVE_VP,
            "components": {
                "selection": [
                    {"ifc_guid": "abc123"},
                    {"ifc_guid": "def456"},
                ]
            },
        }
        result = IfcUrl.parse(bcf_viewpoint_to_ifc_url(BASE, vp))
        assert result.selector == "abc123+def456"
        assert result.visibility == "highlight"

    def test_isolate_from_default_hidden(self):
        vp = {
            **PERSPECTIVE_VP,
            "components": {
                "visibility": {
                    "default_visibility": False,
                    "exceptions": [{"ifc_guid": "abc123"}],
                }
            },
        }
        result = IfcUrl.parse(bcf_viewpoint_to_ifc_url(BASE, vp))
        assert result.selector == "abc123"
        assert result.visibility == "isolate"

    def test_no_components_gives_no_selector(self):
        result = IfcUrl.parse(bcf_viewpoint_to_ifc_url(BASE, PERSPECTIVE_VP))
        assert result.selector is None
        assert result.visibility == "highlight"
