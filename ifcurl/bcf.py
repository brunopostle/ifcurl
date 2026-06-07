# IFC URL — resolve and render ifc:// URLs
# Copyright (C) 2026 Bruno Postle <bruno@postle.net>
# SPDX-License-Identifier: LGPL-3.0-or-later
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

"""BCF 3.0 REST viewpoint ↔ ifc:// URL conversion.

BCF zip generation has moved to the browser (viewer.js).  This module
only provides the translation layer used by the BCF 3.0 REST API routes
in bcf_api.py.
"""

from __future__ import annotations

import dataclasses
import re

from ifcurl.url import IfcUrl

_IFC_GUID_RE = re.compile(r"^[0-9A-Za-z_$]{22}$")


def bcf_viewpoint_to_ifc_url(base: IfcUrl, viewpoint: dict) -> str:
    """Convert a BCF 3.0 REST viewpoint dict to an ifc:// URL.

    Applies camera, clipping planes, and component selection from the BCF
    viewpoint to *base*, which supplies the repo/ref/path context.
    Returns the resulting ifc:// URL string.
    """
    camera: tuple[float, ...] | None = None
    fov: float | None = None
    scale: float | None = None

    if pc := viewpoint.get("perspective_camera"):
        p, d, u = pc["camera_view_point"], pc["camera_direction"], pc["camera_up_vector"]
        camera = (p["x"], p["y"], p["z"], d["x"], d["y"], d["z"], u["x"], u["y"], u["z"])
        fov = float(pc["field_of_view"])
    elif oc := viewpoint.get("orthogonal_camera"):
        p, d, u = oc["camera_view_point"], oc["camera_direction"], oc["camera_up_vector"]
        camera = (p["x"], p["y"], p["z"], d["x"], d["y"], d["z"], u["x"], u["y"], u["z"])
        scale = float(oc["view_to_world_scale"])

    clips: list[tuple[float, ...]] = []
    for cp in viewpoint.get("clipping_planes", []):
        loc, dir_ = cp["location"], cp["direction"]
        clips.append((loc["x"], loc["y"], loc["z"], dir_["x"], dir_["y"], dir_["z"]))

    components = viewpoint.get("components", {})
    vis = components.get("visibility", {})
    default_visible = vis.get("default_visibility", True)
    exceptions = [e["ifc_guid"] for e in vis.get("exceptions", []) if "ifc_guid" in e]
    selected = [s["ifc_guid"] for s in components.get("selection", []) if "ifc_guid" in s]

    if not default_visible:
        # exceptions are the only visible elements → isolate
        guids = exceptions
        visibility = "isolate"
    else:
        guids = selected
        visibility = "highlight"

    selector = "+".join(guids) if guids else None

    return dataclasses.replace(
        base,
        camera=camera,  # type: ignore[arg-type]
        fov=fov,
        scale=scale,
        clips=clips,  # type: ignore[arg-type]
        selector=selector,
        visibility=visibility,
    ).to_string()


def ifc_url_to_bcf_viewpoint(ifc_url: IfcUrl, guid: str) -> dict:
    """Convert an IfcUrl to a BCF 3.0 REST viewpoint dict.

    Only GUID selectors (bare 22-char IFC GlobalIds joined with '+') are
    mapped to BCF components/selection.  Complex IfcOpenShell expressions
    (e.g. 'IfcWall') cannot be expanded without the model and are omitted.
    """
    vp: dict = {"guid": guid, "lines": [], "clipping_planes": []}

    if ifc_url.camera is not None:
        px, py, pz, dx, dy, dz, ux, uy, uz = ifc_url.camera
        cam = {
            "camera_view_point": {"x": px, "y": py, "z": pz},
            "camera_direction": {"x": dx, "y": dy, "z": dz},
            "camera_up_vector": {"x": ux, "y": uy, "z": uz},
        }
        if ifc_url.fov is not None:
            vp["perspective_camera"] = {**cam, "field_of_view": ifc_url.fov}
        elif ifc_url.scale is not None:
            vp["orthogonal_camera"] = {**cam, "view_to_world_scale": ifc_url.scale}

    for px, py, pz, nx, ny, nz in ifc_url.clips:
        vp["clipping_planes"].append({
            "location": {"x": px, "y": py, "z": pz},
            "direction": {"x": nx, "y": ny, "z": nz},
        })

    selection: list[dict] = []
    default_visibility = True
    exceptions: list[dict] = []

    if ifc_url.selector:
        guid_parts = [p.strip() for p in ifc_url.selector.split("+") if _IFC_GUID_RE.match(p.strip())]
        if guid_parts:
            if ifc_url.visibility == "isolate":
                default_visibility = False
                exceptions = [{"ifc_guid": g} for g in guid_parts]
            else:
                selection = [{"ifc_guid": g, "originating_system": "ifcurl", "authoring_tool_id": "ifcurl"} for g in guid_parts]

    vp["components"] = {
        "selection": selection,
        "visibility": {"default_visibility": default_visibility, "exceptions": exceptions},
    }
    return vp
