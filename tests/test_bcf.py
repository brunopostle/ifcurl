"""Tests for ifcurl.bcf — BCF 3.0 viewpoint ↔ ifc:// URL conversion."""

from __future__ import annotations

import pytest

from ifcurl.bcf import bcf_viewpoint_to_ifc_url
from ifcurl.url import IfcUrl


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
