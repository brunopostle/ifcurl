# IFC URL — resolve and render ifc:// URLs
# Copyright (C) 2026 Bruno Postle <bruno@postle.net>
#
# This file is part of IFC URL.
#
# IFC URL is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# IFC URL is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with IFC URL.  If not, see <http://www.gnu.org/licenses/>.

"""Render an IFC model to a PNG using pyvista (off-screen).

Adapted from ifcquery/render.py (Bruno Postle, 2026).  Key differences:
  - Camera is set from explicit URL parameters (position, direction, up,
    fov/scale) rather than named views.
  - Clip planes are applied per-mesh before adding to the scene.
  - Three visibility modes: highlight, ghost, isolate.
  - Type entities (IfcTypeProduct) are handled via a temporary model copy,
    as in ifcquery.
"""

from __future__ import annotations

import multiprocessing
import os
import tempfile

# Cap parallel geometry workers per render call.  In service deployments,
# N concurrent requests × cpu_count workers each can saturate the host.
# Override with IFCURL_RENDER_WORKERS (e.g. "8" for a dedicated render host).
_WORKER_COUNT: int = min(
    multiprocessing.cpu_count(),
    int(os.environ.get("IFCURL_RENDER_WORKERS", "4")),
)

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.guid
import ifcopenshell.util.selector

try:
    import numpy as np
    import pyvista as pv

    # Resolve pyvista's lazy-loaded Plotter class at import time so that
    # concurrent requests in a threadpool don't race on the lazy __getattr__.
    _Plotter = pv.Plotter
    _HAS_PYVISTA = True
except (ImportError, AttributeError):
    _HAS_PYVISTA = False

try:
    import io as _io

    from PIL import Image as _PILImage

    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False

# Highlight colour used in 'highlight' visibility mode (orange).
_HIGHLIGHT_COLOR = (255, 140, 0)

# Clash colour used in 'clash' visibility mode (red).
_CLASH_COLOR = (220, 50, 50)

# Diff colours: green=added, blue=modified, red=removed, grey=unchanged ghost.
_DIFF_ADDED_COLOR = (50, 200, 50)
_DIFF_MODIFIED_COLOR = (50, 100, 200)
_DIFF_REMOVED_COLOR = (200, 50, 50)
_DIFF_GHOST_COLOR = (180, 180, 180)
_DIFF_GHOST_OPACITY = 0.08


# ---------------------------------------------------------------------------
# Camera
# ---------------------------------------------------------------------------


def _apply_camera(
    plotter: pv.Plotter,
    camera: tuple[float, ...],
    fov: float | None,
    scale: float | None,
) -> None:
    """Set the pyvista camera from ifc:// URL camera parameters.

    *camera* is (px, py, pz, dx, dy, dz, ux, uy, uz) in IFC world coordinates.
    Exactly one of *fov* (perspective) or *scale* (orthographic) must be given.
    """
    px, py, pz, dx, dy, dz, ux, uy, uz = camera
    plotter.camera.position = (px, py, pz)
    plotter.camera.focal_point = (px + dx, py + dy, pz + dz)
    plotter.camera.up = (ux, uy, uz)
    if fov is not None:
        plotter.camera.parallel_projection = False
        plotter.camera.view_angle = fov
    else:
        plotter.camera.parallel_projection = True
        plotter.camera.parallel_scale = scale


# ---------------------------------------------------------------------------
# Geometry settings (shared with ifcquery)
# ---------------------------------------------------------------------------


def _build_geom_settings(model: ifcopenshell.file) -> ifcopenshell.geom.settings:
    """Build geometry settings, excluding Clearance subcontexts."""
    settings = ifcopenshell.geom.settings()
    settings.set("use-world-coords", True)

    clearance_ids = {
        c.id()
        for c in model.by_type("IfcGeometricRepresentationSubContext")
        if c.ContextIdentifier == "Clearance"
    }
    if clearance_ids:
        ctx_ids = [
            c.id()
            for c in model.by_type("IfcGeometricRepresentationContext")
            if c.id() not in clearance_ids
        ]
        if ctx_ids:
            settings.set("context-ids", ctx_ids)

    return settings


# ---------------------------------------------------------------------------
# Shape rendering
# ---------------------------------------------------------------------------


def _material_color_and_opacity(mat: object) -> tuple[tuple[int, int, int], float]:
    """Return (RGB tuple, opacity) from an IfcOpenShell material."""
    diffuse = np.clip(np.array(mat.diffuse.components), 0.0, 1.0)
    color = tuple((diffuse * 255).astype(np.uint8))
    transparency = mat.transparency if mat.transparency == mat.transparency else 0.0
    opacity = float(np.clip(1.0 - transparency, 0.0, 1.0))
    return color, opacity  # type: ignore[return-value]


def _add_shape(
    shape: object,
    plotter: pv.Plotter,
    selection_ids: frozenset[int] | None,
    visibility: str,
    clips: list[tuple[float, ...]],
    diff_ids: dict[str, frozenset[int]] | None = None,
) -> None:
    """Triangulate a geometry shape, apply clips, and add it to the plotter.

    Visibility modes (when *diff_ids* is None):
      highlight — non-selected shown normally, selected shown in highlight colour
      ghost     — selected shown normally, non-selected shown as grey + translucent
      isolate   — only selected elements are added (non-selected are skipped)
      clash     — non-selected shown normally, selected shown in clash colour (red)

    When *diff_ids* is provided the diff colouring takes precedence:
      green  — step ID in diff_ids["added"]
      blue   — step ID in diff_ids["modified"]
      red    — step ID in diff_ids["removed"]
      ghost  — step ID in none of the above (unchanged context)
    """
    geom = shape.geometry
    verts = np.array(geom.verts, dtype=float).reshape(-1, 3)
    if verts.size == 0:
        return

    raw_faces = np.array(geom.faces, dtype=int)
    if raw_faces.size == 0 or raw_faces.size % 3 != 0:
        return

    faces = raw_faces.reshape(-1, 3)
    material_ids = np.array(geom.material_ids, dtype=int)

    is_selected = selection_ids is not None and shape.product.id() in selection_ids

    if (
        diff_ids is None
        and visibility == "isolate"
        and selection_ids is not None
        and not is_selected
    ):
        return

    for midx, mat in enumerate(geom.materials):
        tri_mask = material_ids == midx
        if not np.any(tri_mask):
            continue

        sub_faces = faces[tri_mask]
        faces_pv = np.hstack(
            [np.full((sub_faces.shape[0], 1), 3, dtype=int), sub_faces]
        ).ravel()
        mesh = pv.PolyData(verts, faces_pv)

        # Apply clipping planes — spec: normal points toward the visible side
        for px, py, pz, nx, ny, nz in clips:
            mesh = mesh.clip(normal=(nx, ny, nz), origin=(px, py, pz), invert=False)
            if mesh.n_points == 0:
                break
        if mesh.n_points == 0:
            continue

        # Determine colour and opacity
        if diff_ids is not None:
            sid = shape.product.id()
            if sid in diff_ids.get("added", frozenset()):
                color, opacity = _DIFF_ADDED_COLOR, 1.0
            elif sid in diff_ids.get("modified", frozenset()):
                color, opacity = _DIFF_MODIFIED_COLOR, 1.0
            elif sid in diff_ids.get("removed", frozenset()):
                color, opacity = _DIFF_REMOVED_COLOR, 1.0
            else:
                color, opacity = _DIFF_GHOST_COLOR, _DIFF_GHOST_OPACITY
        elif selection_ids is None:
            color, opacity = _material_color_and_opacity(mat)
        elif visibility == "highlight":
            if is_selected:
                color, opacity = _HIGHLIGHT_COLOR, 1.0
            else:
                color, opacity = _material_color_and_opacity(mat)
        elif visibility == "ghost":
            if is_selected:
                color, opacity = _material_color_and_opacity(mat)
            else:
                color, opacity = (180, 180, 180), 0.10
        elif visibility == "clash":
            if is_selected:
                color, opacity = _CLASH_COLOR, 1.0
            else:
                color, opacity = _material_color_and_opacity(mat)
        else:  # isolate — non-selected already returned above
            color, opacity = _material_color_and_opacity(mat)

        plotter.add_mesh(mesh, color=color, opacity=opacity, show_edges=False)


# ---------------------------------------------------------------------------
# Iterator rendering
# ---------------------------------------------------------------------------


def _plotter_screenshot(plotter: pv.Plotter) -> bytes:
    """Render *plotter* off-screen and return PNG bytes."""
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".png")
    os.close(tmp_fd)
    try:
        plotter.show(screenshot=tmp_path, auto_close=True)
        with open(tmp_path, "rb") as f:
            return f.read()
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _render_iterator(
    iterator: object,
    selection_ids: list[int] | None,
    visibility: str,
    clips: list[tuple[float, ...]],
    camera: tuple[float, ...] | None,
    fov: float | None,
    scale: float | None,
    diff_ids: dict[str, frozenset[int]] | None = None,
) -> bytes:
    """Drive a geometry iterator into a pyvista plotter and return PNG bytes."""
    plotter = _Plotter(off_screen=True, window_size=(1280, 960))
    plotter.background_color = "white"

    frozen_ids = frozenset(selection_ids) if selection_ids else None

    while True:
        try:
            _add_shape(
                iterator.get(),
                plotter,
                frozen_ids,
                visibility,
                clips,
                diff_ids=diff_ids,
            )
        except Exception:
            pass  # skip broken shapes, keep rendering the rest
        if not iterator.next():
            break

    if camera is not None:
        _apply_camera(plotter, camera, fov, scale)
    else:
        plotter.reset_camera()
        plotter.camera.up = (0, 0, 1)
        if scale is not None:
            plotter.camera.parallel_projection = True
            plotter.camera.parallel_scale = scale

    return _plotter_screenshot(plotter)


# ---------------------------------------------------------------------------
# Type entity handling (adapted from ifcquery)
# ---------------------------------------------------------------------------


def _get_occurrence_class(type_entity: object) -> str:
    type_class = type_entity.is_a()
    if type_class.endswith("Type"):
        return type_class[:-4]
    return "IfcBuildingElementProxy"


def _make_type_occurrence(
    model: ifcopenshell.file, type_entity: object
) -> object | None:
    rep_maps = getattr(type_entity, "RepresentationMaps", None) or []
    if not rep_maps:
        return None

    mapped_items = []
    for rep_map in rep_maps:
        origin = model.create_entity("IfcCartesianPoint", Coordinates=(0.0, 0.0, 0.0))
        transform = model.create_entity(
            "IfcCartesianTransformationOperator3D", LocalOrigin=origin
        )
        mapped_items.append(
            model.create_entity(
                "IfcMappedItem", MappingSource=rep_map, MappingTarget=transform
            )
        )

    context = rep_maps[0].MappedRepresentation.ContextOfItems
    shape_rep = model.create_entity(
        "IfcShapeRepresentation",
        ContextOfItems=context,
        RepresentationIdentifier="Body",
        RepresentationType="MappedRepresentation",
        Items=mapped_items,
    )
    prod_def = model.create_entity(
        "IfcProductDefinitionShape", Representations=[shape_rep]
    )

    pt = model.create_entity("IfcCartesianPoint", Coordinates=(0.0, 0.0, 0.0))
    axis2 = model.create_entity(
        "IfcAxis2Placement3D",
        Location=pt,
        Axis=model.create_entity("IfcDirection", DirectionRatios=(0.0, 0.0, 1.0)),
        RefDirection=model.create_entity(
            "IfcDirection", DirectionRatios=(1.0, 0.0, 0.0)
        ),
    )
    placement = model.create_entity("IfcLocalPlacement", RelativePlacement=axis2)

    occ_class = _get_occurrence_class(type_entity)
    try:
        return model.create_entity(
            occ_class,
            GlobalId=ifcopenshell.guid.new(),
            Name=f"_type_preview_{type_entity.id()}",
            ObjectPlacement=placement,
            Representation=prod_def,
        )
    except Exception:
        return model.create_entity(
            "IfcBuildingElementProxy",
            GlobalId=ifcopenshell.guid.new(),
            Name=f"_type_preview_{type_entity.id()}",
            ObjectPlacement=placement,
            Representation=prod_def,
        )


def _render_with_types(
    model: ifcopenshell.file,
    types: list,
    selector_elements: list | None,
    element_ids: list[int] | None,
    type_highlight_ids: set[int],
    visibility: str,
    clips: list[tuple[float, ...]],
    camera: tuple[float, ...] | None,
    fov: float | None,
    scale: float | None,
) -> bytes:
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".ifc")
    os.close(tmp_fd)
    try:
        model.write(tmp_path)
        tmp = ifcopenshell.open(tmp_path)

        type_id_to_occ_id: dict[int, int] = {}
        for t in types:
            tmp_type = tmp.by_id(t.id())
            occ = _make_type_occurrence(tmp, tmp_type)
            if occ:
                type_id_to_occ_id[t.id()] = occ.id()

        if not type_id_to_occ_id:
            raise ValueError("Type entities have no RepresentationMaps to render")

        include = [tmp.by_id(occ_id) for occ_id in type_id_to_occ_id.values()]
        if selector_elements:
            include.extend(tmp.by_id(e.id()) for e in selector_elements)

        settings = _build_geom_settings(tmp)
        iterator = ifcopenshell.geom.iterator(
            settings, tmp, _WORKER_COUNT, include=include
        )
        if not iterator.initialize():
            raise ValueError("Type entities have no renderable geometry")

        new_highlight = None
        if element_ids:
            new_highlight = []
            for hid in element_ids:
                if hid in type_highlight_ids:
                    mapped = type_id_to_occ_id.get(hid)
                    if mapped:
                        new_highlight.append(mapped)
                else:
                    new_highlight.append(hid)

        return _render_iterator(
            iterator, new_highlight, visibility, clips, camera, fov, scale
        )
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def render(
    model: ifcopenshell.file,
    selector: str | None = None,
    element_ids: list[int] | None = None,
    camera: (
        tuple[float, float, float, float, float, float, float, float, float] | None
    ) = None,
    fov: float | None = None,
    scale: float | None = None,
    clips: list[tuple[float, float, float, float, float, float]] | None = None,
    visibility: str = "highlight",
) -> bytes:
    """Render IFC model geometry to a PNG image.

    :param model: The in-memory IFC model.
    :param selector: IfcOpenShell selector to restrict which elements are
        considered the "selection" for visibility purposes.  When omitted the
        whole model is rendered with no selection.
    :param element_ids: Step IDs to treat as the selection (alternative to
        *selector*; both may be combined).
    :param camera: Nine floats (px,py,pz,dx,dy,dz,ux,uy,uz) defining the
        camera in IFC world coordinates.  When ``None`` the camera is fitted
        to the scene automatically.
    :param fov: Perspective field of view in degrees.  Required when *camera*
        is given and orthographic projection is not wanted.
    :param scale: Orthographic view-to-world scale (BCF ViewToWorldScale).
        Required when *camera* is given and perspective is not wanted.
    :param clips: List of clipping planes, each ``(px,py,pz,nx,ny,nz)``.
        The normal points toward the *visible* side.
    :param visibility: One of ``'highlight'``, ``'ghost'``, or ``'isolate'``.
    :return: PNG image as raw bytes.
    :raises ImportError: If pyvista or numpy are not installed.
    :raises ValueError: If the selector matches nothing or there is no
        renderable geometry.
    """
    if not _HAS_PYVISTA:
        raise ImportError(
            "pyvista and numpy are required for rendering.  Install with: pip install pyvista numpy"
        )

    clips = clips or []

    # --- Partition selector results into types and ordinary elements ---
    if selector:
        matched = list(ifcopenshell.util.selector.filter_elements(model, selector))
        if not matched:
            raise ValueError(f"Selector {selector!r} matched no elements")
        types = [e for e in matched if e.is_a("IfcTypeProduct")]
        selector_elements: list | None = [
            e for e in matched if not e.is_a("IfcTypeProduct")
        ]
    else:
        types = []
        selector_elements = None

    # --- Collect type entities from element_ids ---
    type_highlight_ids: set[int] = set()
    if element_ids:
        for eid in element_ids:
            entity = model.by_id(eid)
            if entity.is_a("IfcTypeProduct"):
                type_highlight_ids.add(eid)
                if eid not in {t.id() for t in types}:
                    types.append(entity)

    # Build the unified selection ID set used for visibility colouring.
    selection_ids: list[int] | None = None
    if selector_elements is not None or element_ids:
        selection_ids = list(element_ids or [])
        if selector_elements:
            selection_ids.extend(e.id() for e in selector_elements)

    # --- Delegate to temp-copy path when any type entities are involved ---
    if types:
        return _render_with_types(
            model,
            types,
            selector_elements,
            selection_ids,
            type_highlight_ids,
            visibility,
            clips,
            camera,
            fov,
            scale,
        )

    # --- Regular element rendering ---
    settings = _build_geom_settings(model)

    if selector_elements is not None and not selector_elements:
        raise ValueError(f"Selector {selector!r} matched only type entities")

    if selector_elements is not None and visibility == "isolate":
        iterator = ifcopenshell.geom.iterator(
            settings, model, _WORKER_COUNT, include=selector_elements
        )
    else:
        exclude = list(model.by_type("IfcOpeningElement"))
        iterator = ifcopenshell.geom.iterator(
            settings,
            model,
            _WORKER_COUNT,
            exclude=exclude if exclude else None,
        )

    if not iterator.initialize():
        raise ValueError("No renderable geometry found")

    return _render_iterator(
        iterator, selection_ids, visibility, clips, camera, fov, scale
    )


def _entity_bounds(model: ifcopenshell.file, entities: list) -> list[float] | None:
    """Return [xmin,xmax,ymin,ymax,zmin,zmax] for *entities* in world coords, or None."""
    if not entities:
        return None
    products = [e for e in entities if e.is_a("IfcProduct")]
    if not products:
        return None
    settings = _build_geom_settings(model)
    it = ifcopenshell.geom.iterator(settings, model, _WORKER_COUNT, include=products)
    if not it.initialize():
        return None
    all_verts: list[np.ndarray] = []
    while True:
        try:
            v = np.array(it.get().geometry.verts, dtype=float).reshape(-1, 3)
            if v.size > 0:
                all_verts.append(v)
        except Exception:
            pass
        if not it.next():
            break
    if not all_verts:
        return None
    v = np.vstack(all_verts)
    return [
        float(v[:, 0].min()),
        float(v[:, 0].max()),
        float(v[:, 1].min()),
        float(v[:, 1].max()),
        float(v[:, 2].min()),
        float(v[:, 2].max()),
    ]


def _merge_bounds(a: list[float] | None, b: list[float] | None) -> list[float] | None:
    """Merge two [xmin,xmax,ymin,ymax,zmin,zmax] extents, either may be None."""
    if a is None:
        return b
    if b is None:
        return a
    return [
        min(a[0], b[0]),
        max(a[1], b[1]),
        min(a[2], b[2]),
        max(a[3], b[3]),
        min(a[4], b[4]),
        max(a[5], b[5]),
    ]


def render_diff(
    model_head: ifcopenshell.file,
    model_base: ifcopenshell.file,
    diff_ids: dict[str, set[int]],
    camera: (
        tuple[float, float, float, float, float, float, float, float, float] | None
    ) = None,
    fov: float | None = None,
    scale: float | None = None,
    clips: list[tuple[float, float, float, float, float, float]] | None = None,
) -> bytes:
    """Render a two-pass IFC diff image.

    Pass 1 renders the head (new) model with diff colouring:
      green  — added elements
      blue   — modified elements
      ghost  — unchanged context (very light grey, 8 % opacity)

    Pass 2 renders only the removed elements from the base (old) model in red,
    using the same camera as pass 1.  The two images are then composited: any
    pixel in pass 2 that is not the white background is stamped onto pass 1.
    This means moved elements (classified as modified) appear once in blue at
    their new position, with no ghosting artefact.

    :param model_head: The newer IFC model (PR head commit).
    :param model_base: The older IFC model (PR base commit).
    :param diff_ids: ``{"added": set, "modified": set, "removed": set}`` of
        step IDs, pre-expanded via :func:`ifcurl.diff.expand_step_ids`.
    :param camera: Nine floats (px,py,pz,dx,dy,dz,ux,uy,uz).  When ``None``
        the camera is auto-fitted to the head model scene.
    :param fov: Perspective FOV in degrees.
    :param scale: Orthographic scale.
    :param clips: Clipping planes.
    :return: PNG image bytes.
    :raises ImportError: If pyvista, numpy, or Pillow are not installed.
    :raises ValueError: If the head model has no renderable geometry.
    """
    if not _HAS_PYVISTA:
        raise ImportError(
            "pyvista and numpy are required for rendering.  Install with: pip install pyvista numpy"
        )
    if not _HAS_PIL:
        raise ImportError(
            "Pillow is required for diff rendering.  Install with: pip install Pillow"
        )

    clips = clips or []

    frozen_diff = {k: frozenset(v) for k, v in diff_ids.items()}

    # ------------------------------------------------------------------
    # Pre-compute removed entities (needed for bounds and pass 2)
    # ------------------------------------------------------------------
    removed_ids = diff_ids.get("removed", set())
    removed_entities = []
    for eid in removed_ids:
        try:
            e = model_base.by_id(eid)
            if e.is_a("IfcProduct"):
                removed_entities.append(e)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Pre-compute bounding box of all changed elements for camera fitting.
    # added/modified bounds come from a subset iterator on the head model;
    # removed bounds come from a subset iterator on the base model.
    # Both are fast because they operate on small subsets.
    # ------------------------------------------------------------------
    if camera is None:
        changed_head_ids = frozen_diff.get("added", frozenset()) | frozen_diff.get(
            "modified", frozenset()
        )
        changed_head_entities = []
        for eid in changed_head_ids:
            try:
                changed_head_entities.append(model_head.by_id(eid))
            except Exception:
                pass
        diff_bounds = _merge_bounds(
            _entity_bounds(model_head, changed_head_entities),
            _entity_bounds(model_base, removed_entities),
        )
    else:
        diff_bounds = None

    # ------------------------------------------------------------------
    # Pass 1: head model — full scene with diff colouring
    # ------------------------------------------------------------------
    settings_head = _build_geom_settings(model_head)
    exclude_head = list(model_head.by_type("IfcOpeningElement"))
    iterator_head = ifcopenshell.geom.iterator(
        settings_head,
        model_head,
        _WORKER_COUNT,
        exclude=exclude_head if exclude_head else None,
    )
    if not iterator_head.initialize():
        raise ValueError("Head model has no renderable geometry")

    plotter1 = _Plotter(off_screen=True, window_size=(1280, 960))
    plotter1.background_color = "white"

    while True:
        try:
            _add_shape(
                iterator_head.get(),
                plotter1,
                None,
                "highlight",
                clips,
                diff_ids=frozen_diff,
            )
        except Exception:
            pass
        if not iterator_head.next():
            break

    if camera is not None:
        _apply_camera(plotter1, camera, fov, scale)
    elif diff_bounds is not None:
        # Reset first to establish a good viewing direction, then reposition
        # close to the diff area.  reset_camera(bounds=...) re-expands the
        # clipping range to include all actors, so it doesn't actually zoom;
        # explicit positioning is required.
        plotter1.reset_camera()
        plotter1.camera.up = (0, 0, 1)
        xmin, xmax, ymin, ymax, zmin, zmax = diff_bounds
        cx, cy, cz = (xmin + xmax) / 2, (ymin + ymax) / 2, (zmin + zmax) / 2
        max_dim = max(xmax - xmin, ymax - ymin, zmax - zmin, 1e-3)
        cam_pos = np.array(plotter1.camera.position)
        focal = np.array(plotter1.camera.focal_point)
        direction = cam_pos - focal
        direction /= np.linalg.norm(direction)
        fov_rad = np.radians(plotter1.camera.view_angle)
        dist = (max_dim / 2) / np.tan(fov_rad / 2) * 1.5
        plotter1.camera.focal_point = (cx, cy, cz)
        plotter1.camera.position = tuple(np.array([cx, cy, cz]) + direction * dist)
        if scale is not None:
            plotter1.camera.parallel_projection = True
            plotter1.camera.parallel_scale = scale
    else:
        plotter1.reset_camera()
        plotter1.camera.up = (0, 0, 1)
        if scale is not None:
            plotter1.camera.parallel_projection = True
            plotter1.camera.parallel_scale = scale

    # Capture camera state BEFORE closing plotter1 so pass 2 uses the same view.
    _cam1_pos = tuple(plotter1.camera.position)
    _cam1_focal = tuple(plotter1.camera.focal_point)
    _cam1_up = tuple(plotter1.camera.up)
    _cam1_angle = float(plotter1.camera.view_angle)
    _cam1_parallel = bool(plotter1.camera.parallel_projection)
    _cam1_pscale = float(plotter1.camera.parallel_scale)

    pass1_png = _plotter_screenshot(plotter1)

    # ------------------------------------------------------------------
    # Pass 2: base model — removed elements only, in red.
    # Apply the same camera that pass 1 used.
    # ------------------------------------------------------------------
    if not removed_entities:
        return pass1_png

    settings_base = _build_geom_settings(model_base)
    iterator_base = ifcopenshell.geom.iterator(
        settings_base, model_base, _WORKER_COUNT, include=removed_entities
    )
    if not iterator_base.initialize():
        return pass1_png

    removed_diff = {
        "added": frozenset(),
        "modified": frozenset(),
        "removed": frozenset(removed_ids),
    }

    plotter2 = _Plotter(off_screen=True, window_size=(1280, 960))
    plotter2.background_color = "white"

    while True:
        try:
            _add_shape(
                iterator_base.get(),
                plotter2,
                None,
                "highlight",
                clips,
                diff_ids=removed_diff,
            )
        except Exception:
            pass
        if not iterator_base.next():
            break

    plotter2.camera.position = _cam1_pos
    plotter2.camera.focal_point = _cam1_focal
    plotter2.camera.up = _cam1_up
    plotter2.camera.view_angle = _cam1_angle
    plotter2.camera.parallel_projection = _cam1_parallel
    plotter2.camera.parallel_scale = _cam1_pscale

    pass2_png = _plotter_screenshot(plotter2)

    # ------------------------------------------------------------------
    # Composite: stamp pass 2 non-background pixels onto pass 1
    # ------------------------------------------------------------------
    return _composite_diff(pass1_png, pass2_png)


def _composite_diff(pass1_png: bytes, pass2_png: bytes) -> bytes:
    """Overlay pass 2 onto pass 1 wherever pass 2 is not white background."""
    img1 = np.array(_PILImage.open(_io.BytesIO(pass1_png)).convert("RGB"))
    img2 = np.array(_PILImage.open(_io.BytesIO(pass2_png)).convert("RGB"))

    mask = np.any(img2 != 255, axis=2)
    result = img1.copy()
    result[mask] = img2[mask]

    buf = _io.BytesIO()
    _PILImage.fromarray(result).save(buf, format="PNG")
    return buf.getvalue()
