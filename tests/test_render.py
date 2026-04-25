"""Tests for ifcurl.render — PNG rendering with camera, clips, and visibility."""

from __future__ import annotations

import pytest

from ifcurl.render import render

try:
    import pyvista  # noqa: F401

    HAS_PYVISTA = True
except ImportError:
    HAS_PYVISTA = False

pytestmark = pytest.mark.skipif(not HAS_PYVISTA, reason="pyvista not installed")

PNG_MAGIC = b"\x89PNG"


# ---------------------------------------------------------------------------
# Basic rendering
# ---------------------------------------------------------------------------


class TestRenderBasic:
    def test_returns_png_bytes(self, model_with_geometry):
        result = render(model_with_geometry)
        assert result[:4] == PNG_MAGIC

    def test_no_geometry_raises(self):
        import ifcopenshell.api.aggregate
        import ifcopenshell.api.owner.settings
        import ifcopenshell.api.project
        import ifcopenshell.api.root
        import ifcopenshell.api.spatial
        import ifcopenshell.api.unit

        f = ifcopenshell.api.project.create_file()
        ifcopenshell.api.owner.settings.get_user = lambda ifc: None
        ifcopenshell.api.owner.settings.get_application = lambda ifc: None
        project = ifcopenshell.api.root.create_entity(
            f, ifc_class="IfcProject", name="P"
        )
        ifcopenshell.api.unit.assign_unit(f)
        site = ifcopenshell.api.root.create_entity(f, ifc_class="IfcSite")
        building = ifcopenshell.api.root.create_entity(f, ifc_class="IfcBuilding")
        storey = ifcopenshell.api.root.create_entity(f, ifc_class="IfcBuildingStorey")
        ifcopenshell.api.aggregate.assign_object(
            f, products=[site], relating_object=project
        )
        ifcopenshell.api.aggregate.assign_object(
            f, products=[building], relating_object=site
        )
        ifcopenshell.api.aggregate.assign_object(
            f, products=[storey], relating_object=building
        )
        wall = ifcopenshell.api.root.create_entity(f, ifc_class="IfcWall", name="W")
        ifcopenshell.api.spatial.assign_container(
            f, products=[wall], relating_structure=storey
        )
        with pytest.raises(ValueError, match="No renderable geometry"):
            render(f)


# ---------------------------------------------------------------------------
# Selector
# ---------------------------------------------------------------------------


class TestRenderSelector:
    def test_selector_returns_png(self, model_with_geometry):
        result = render(model_with_geometry, selector="IfcWall")
        assert result[:4] == PNG_MAGIC

    def test_selector_no_match_raises(self, model_with_geometry):
        with pytest.raises(ValueError, match="matched no elements"):
            render(model_with_geometry, selector="IfcDoor")


# ---------------------------------------------------------------------------
# Camera
# ---------------------------------------------------------------------------


class TestRenderCamera:
    def test_explicit_perspective_camera(self, model_with_geometry):
        # Camera above and looking down
        result = render(
            model_with_geometry,
            camera=(0.0, 0.0, 20.0, 0.0, 0.0, -1.0, 0.0, 1.0, 0.0),
            fov=60.0,
        )
        assert result[:4] == PNG_MAGIC

    def test_explicit_orthographic_camera(self, model_with_geometry):
        result = render(
            model_with_geometry,
            camera=(0.0, 0.0, 20.0, 0.0, 0.0, -1.0, 0.0, 1.0, 0.0),
            scale=10.0,
        )
        assert result[:4] == PNG_MAGIC

    def test_no_camera_auto_fits(self, model_with_geometry):
        # No camera → reset_camera() called, should still produce valid PNG
        result = render(model_with_geometry)
        assert result[:4] == PNG_MAGIC

    def test_perspective_and_orthographic_differ(self, model_with_geometry):
        cam = (0.0, 0.0, 20.0, 0.0, 0.0, -1.0, 0.0, 1.0, 0.0)
        persp = render(model_with_geometry, camera=cam, fov=60.0)
        ortho = render(model_with_geometry, camera=cam, scale=10.0)
        assert persp != ortho


# ---------------------------------------------------------------------------
# Clipping planes
# ---------------------------------------------------------------------------


class TestRenderClips:
    def test_clip_plane_returns_png(self, model_with_geometry):
        # Clip at z=1.5, keeping below (normal points down -Z)
        result = render(
            model_with_geometry,
            clips=[(0.0, 0.0, 1.5, 0.0, 0.0, -1.0)],
        )
        assert result[:4] == PNG_MAGIC

    def test_multiple_clip_planes(self, model_with_geometry):
        result = render(
            model_with_geometry,
            clips=[
                (0.0, 0.0, 2.0, 0.0, 0.0, -1.0),
                (0.0, 0.0, 0.5, 0.0, 0.0, 1.0),
            ],
        )
        assert result[:4] == PNG_MAGIC

    def test_clip_produces_different_image(self, model_with_geometry):
        no_clip = render(model_with_geometry)
        clipped = render(model_with_geometry, clips=[(0.0, 0.0, 1.0, 0.0, 0.0, -1.0)])
        assert no_clip != clipped


# ---------------------------------------------------------------------------
# Visibility modes
# ---------------------------------------------------------------------------


class TestRenderVisibility:
    def test_highlight_mode(self, model_with_geometry):
        wall = model_with_geometry.by_type("IfcWall")[0]
        result = render(
            model_with_geometry, element_ids=[wall.id()], visibility="highlight"
        )
        assert result[:4] == PNG_MAGIC

    def test_ghost_mode(self, model_with_geometry):
        wall = model_with_geometry.by_type("IfcWall")[0]
        result = render(
            model_with_geometry, element_ids=[wall.id()], visibility="ghost"
        )
        assert result[:4] == PNG_MAGIC

    def test_isolate_mode(self, model_with_geometry):
        wall = model_with_geometry.by_type("IfcWall")[0]
        result = render(
            model_with_geometry, element_ids=[wall.id()], visibility="isolate"
        )
        assert result[:4] == PNG_MAGIC

    def test_visibility_modes_produce_different_images(self, model_with_geometry):
        wall = model_with_geometry.by_type("IfcWall")[0]
        highlight = render(
            model_with_geometry, element_ids=[wall.id()], visibility="highlight"
        )
        ghost = render(model_with_geometry, element_ids=[wall.id()], visibility="ghost")
        isolate = render(
            model_with_geometry, element_ids=[wall.id()], visibility="isolate"
        )
        # All three are valid PNG but should differ in content
        assert highlight[:4] == ghost[:4] == isolate[:4] == PNG_MAGIC
        assert len({highlight, ghost, isolate}) > 1

    def test_selector_with_visibility(self, model_with_geometry):
        result = render(model_with_geometry, selector="IfcWall", visibility="ghost")
        assert result[:4] == PNG_MAGIC


# ---------------------------------------------------------------------------
# Combined parameters
# ---------------------------------------------------------------------------


class TestRenderCombined:
    def test_selector_camera_clip(self, model_with_geometry):
        result = render(
            model_with_geometry,
            selector="IfcWall",
            camera=(0.0, 0.0, 20.0, 0.0, 0.0, -1.0, 0.0, 1.0, 0.0),
            fov=45.0,
            clips=[(0.0, 0.0, 2.0, 0.0, 0.0, -1.0)],
            visibility="ghost",
        )
        assert result[:4] == PNG_MAGIC
