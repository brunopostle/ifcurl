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

"""Render isolation service — IFC parsing, selecting, and rendering.

This service receives raw IFC bytes over a Unix-domain socket and performs all
ifcopenshell and pyvista operations in a subprocess sandbox.  It has no access
to git credentials, no network access, and (when deployed under systemd with
the provided unit file) has execve blocked.

Three endpoints:
  POST /render       → JSON {png: base64, guids: list|null}
  POST /select       → JSON {guids: list}
  POST /render_diff  → image/png
"""

from __future__ import annotations

import base64
import json

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from ifcurl.sandbox import SandboxCrashError, SandboxTimeoutError, run_sandboxed

# ---------------------------------------------------------------------------
# Pipeline functions (serialisable callables — run inside run_sandboxed)
# ---------------------------------------------------------------------------


def _sandboxed_pipeline(
    ifc_bytes: bytes,
    selector: str | None,
    element_guids: frozenset[str] | None,
    camera: tuple | None,
    fov: float | None,
    scale: float | None,
    clips: list,
    visibility: str,
) -> tuple[frozenset[str] | None, bytes]:
    """Parse, optionally resolve selection, and render in a child process.

    Returns ``(new_guids, png_bytes)`` where *new_guids* is populated only when
    *selector* was given so the caller can update the T3 cache.
    """
    import ifcopenshell
    import ifcopenshell.util.selector

    from ifcurl import render as render_mod

    model = ifcopenshell.file.from_string(ifc_bytes.decode())

    new_guids: frozenset[str] | None = None
    element_ids: list[int] | None = None

    if selector is not None:
        matched = list(ifcopenshell.util.selector.filter_elements(model, selector))
        new_guids = frozenset(
            e.GlobalId for e in matched if hasattr(e, "GlobalId") and e.GlobalId
        )
    elif element_guids is not None:
        ids = []
        for guid in element_guids:
            try:
                ids.append(model.by_guid(guid).id())
            except Exception:
                pass
        element_ids = ids if ids else None

    png = render_mod.render(
        model,
        selector=selector,
        element_ids=element_ids,
        camera=camera,
        fov=fov,
        scale=scale,
        clips=clips or None,
        visibility=visibility,
    )
    return new_guids, png


def _sandboxed_select(ifc_bytes: bytes, selector: str) -> list[str]:
    """Parse and run selector in a child process. Returns list of GlobalIds."""
    import ifcopenshell
    import ifcopenshell.util.selector

    model = ifcopenshell.file.from_string(ifc_bytes.decode())
    matched = list(ifcopenshell.util.selector.filter_elements(model, selector))
    return [e.GlobalId for e in matched if hasattr(e, "GlobalId") and e.GlobalId]


def _sandboxed_diff(
    head_bytes: bytes,
    base_bytes: bytes,
    raw_diff_text: str,
    camera: tuple | None,
    fov: float | None,
    scale: float | None,
    clips: list,
) -> bytes:
    """Parse both models, expand diff IDs, and render the two-pass diff image."""
    import ifcopenshell

    from ifcurl import render as render_mod
    from ifcurl.diff import expand_step_ids, step_ids_from_diff

    model_head = ifcopenshell.file.from_string(head_bytes.decode())
    model_base = ifcopenshell.file.from_string(base_bytes.decode())

    raw_ids = step_ids_from_diff(raw_diff_text)
    diff_ids = expand_step_ids(model_head, raw_ids)

    return render_mod.render_diff(
        model_head=model_head,
        model_base=model_base,
        diff_ids=diff_ids,
        camera=camera,
        fov=fov,
        scale=scale,
        clips=clips or None,
    )


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="ifcurl render service",
    description="Internal render-isolation service. Receives IFC bytes, returns PNG.",
    version="0.0.0",
)


@app.post("/render")
async def render_endpoint(
    ifc: UploadFile = File(...),
    params: str = Form(...),
) -> Response:
    """Render an IFC model to PNG.

    ``params`` is a JSON object with keys:
    ``selector`` (str|null), ``element_guids`` (list|null),
    ``camera`` (list[float]|null), ``fov`` (float|null),
    ``scale`` (float|null), ``clips`` (list), ``visibility`` (str).

    Returns JSON ``{png: base64_string, guids: list|null}``.
    """
    ifc_bytes = await ifc.read()
    p = json.loads(params)

    selector: str | None = p.get("selector")
    raw_guids = p.get("element_guids")
    element_guids: frozenset[str] | None = frozenset(raw_guids) if raw_guids is not None else None
    camera: tuple | None = tuple(p["camera"]) if p.get("camera") else None
    fov: float | None = p.get("fov")
    scale: float | None = p.get("scale")
    clips: list = p.get("clips", [])
    visibility: str = p.get("visibility", "highlight")

    try:
        new_guids, png_bytes = run_sandboxed(
            _sandboxed_pipeline,
            ifc_bytes,
            selector,
            element_guids,
            camera,
            fov,
            scale,
            clips,
            visibility,
        )
    except SandboxCrashError as exc:
        raise HTTPException(status_code=422, detail=f"IFC parse/render crashed: {exc}") from exc
    except SandboxTimeoutError as exc:
        raise HTTPException(status_code=503, detail=f"Render timed out: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return Response(
        content=json.dumps({
            "png": base64.b64encode(png_bytes).decode(),
            "guids": list(new_guids) if new_guids is not None else None,
        }),
        media_type="application/json",
    )


@app.post("/select")
async def select_endpoint(
    ifc: UploadFile = File(...),
    selector: str = Form(...),
) -> Response:
    """Run an IfcOpenShell selector against an IFC model.

    Returns JSON ``{guids: list[str]}``.
    """
    ifc_bytes = await ifc.read()

    try:
        guids = run_sandboxed(_sandboxed_select, ifc_bytes, selector)
    except SandboxCrashError as exc:
        raise HTTPException(status_code=422, detail=f"IFC parse/select crashed: {exc}") from exc
    except SandboxTimeoutError as exc:
        raise HTTPException(status_code=503, detail=f"Select timed out: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid selector: {exc}") from exc

    return Response(
        content=json.dumps({"guids": guids}),
        media_type="application/json",
    )


@app.post("/render_diff")
async def render_diff_endpoint(
    head_ifc: UploadFile = File(...),
    base_ifc: UploadFile = File(...),
    params: str = Form(...),
) -> Response:
    """Render a two-pass IFC diff image (green=added, blue=modified, red=removed).

    ``params`` is a JSON object with keys:
    ``raw_diff`` (str), ``camera`` (list[float]|null), ``fov`` (float|null),
    ``scale`` (float|null), ``clips`` (list).

    Returns ``image/png``.
    """
    head_bytes = await head_ifc.read()
    base_bytes = await base_ifc.read()
    p = json.loads(params)

    raw_diff: str = p["raw_diff"]
    camera: tuple | None = tuple(p["camera"]) if p.get("camera") else None
    fov: float | None = p.get("fov")
    scale: float | None = p.get("scale")
    clips: list = p.get("clips", [])

    try:
        png_bytes = run_sandboxed(
            _sandboxed_diff,
            head_bytes,
            base_bytes,
            raw_diff,
            camera,
            fov,
            scale,
            clips,
        )
    except SandboxCrashError as exc:
        raise HTTPException(status_code=422, detail=f"IFC diff render crashed: {exc}") from exc
    except SandboxTimeoutError as exc:
        raise HTTPException(status_code=503, detail=f"Diff render timed out: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return Response(content=png_bytes, media_type="image/png")
