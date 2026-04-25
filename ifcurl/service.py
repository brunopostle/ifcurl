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

"""Preview service: POST /preview → image/png.

Caching tiers
-------------
Tier 2  (commit_hexsha, path) → IFC bytes
    In-memory LRU.  Avoids repeated git blob reads for the same commit.

Tier 3  (commit_hexsha, path, selector) → frozenset of GlobalIds
    In-memory LRU.  Avoids re-running ifcopenshell selector execution.
    The GUID set is converted back to step IDs against the loaded model on
    each render, so it remains valid across model reloads from the same blob.

Tier 4  sha256(url) → PNG bytes
    Filesystem.  Only written for immutable refs (commit hashes, tags).
    Mutable refs (HEAD, branches) are never cached at this tier.
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import os
import threading
import time
from collections import OrderedDict
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from platformdirs import user_cache_dir
from pydantic import BaseModel

from ifcurl.auth import get_token_for_host
from ifcurl.bcf import build_bcf
from ifcurl.git import diff_text as git_diff_text
from ifcurl.git import fetch_ifc
from ifcurl.render_service import _sandboxed_diff, _sandboxed_pipeline, _sandboxed_select
from ifcurl.sandbox import SandboxCrashError, SandboxTimeoutError, run_sandboxed
from ifcurl.url import IfcUrl

# ---------------------------------------------------------------------------
# Render service delegation (when IFCURL_RENDER_SOCKET is set)
# ---------------------------------------------------------------------------

_RENDER_SOCKET: str | None = os.environ.get("IFCURL_RENDER_SOCKET")
_RENDER_TIMEOUT: int = int(os.environ.get("IFCURL_SANDBOX_TIMEOUT", "120")) + 30


def _render_via_socket(
    ifc_bytes: bytes,
    selector: str | None,
    element_guids: frozenset[str] | None,
    camera: tuple | None,
    fov: float | None,
    scale: float | None,
    clips: list,
    visibility: str,
) -> tuple[frozenset[str] | None, bytes]:
    """Delegate render to the render service over the Unix socket."""
    import httpx

    params = {
        "selector": selector,
        "element_guids": list(element_guids) if element_guids is not None else None,
        "camera": list(camera) if camera else None,
        "fov": fov,
        "scale": scale,
        "clips": clips,
        "visibility": visibility,
    }
    try:
        with httpx.Client(transport=httpx.HTTPTransport(uds=_RENDER_SOCKET)) as client:
            resp = client.post(
                "http://render/render",
                files={"ifc": ifc_bytes},
                data={"params": json.dumps(params)},
                timeout=_RENDER_TIMEOUT,
            )
    except httpx.TransportError as exc:
        raise HTTPException(status_code=503, detail=f"Render service unavailable: {exc}") from exc
    _check_render_response(resp)
    result = resp.json()
    png_bytes = base64.b64decode(result["png"])
    raw_guids = result.get("guids")
    new_guids = frozenset(raw_guids) if raw_guids is not None else None
    return new_guids, png_bytes


def _select_via_socket(ifc_bytes: bytes, selector: str) -> list[str]:
    """Delegate selector execution to the render service over the Unix socket."""
    import httpx

    try:
        with httpx.Client(transport=httpx.HTTPTransport(uds=_RENDER_SOCKET)) as client:
            resp = client.post(
                "http://render/select",
                files={"ifc": ifc_bytes},
                data={"selector": selector},
                timeout=_RENDER_TIMEOUT,
            )
    except httpx.TransportError as exc:
        raise HTTPException(status_code=503, detail=f"Render service unavailable: {exc}") from exc
    _check_render_response(resp)
    return resp.json()["guids"]


def _diff_via_socket(
    head_bytes: bytes,
    base_bytes: bytes,
    raw_diff: str,
    camera: tuple | None,
    fov: float | None,
    scale: float | None,
    clips: list,
) -> bytes:
    """Delegate diff rendering to the render service over the Unix socket."""
    import httpx

    params = {
        "raw_diff": raw_diff,
        "camera": list(camera) if camera else None,
        "fov": fov,
        "scale": scale,
        "clips": clips,
    }
    try:
        with httpx.Client(transport=httpx.HTTPTransport(uds=_RENDER_SOCKET)) as client:
            resp = client.post(
                "http://render/render_diff",
                files={"head_ifc": head_bytes, "base_ifc": base_bytes},
                data={"params": json.dumps(params)},
                timeout=_RENDER_TIMEOUT,
            )
    except httpx.TransportError as exc:
        raise HTTPException(status_code=503, detail=f"Render service unavailable: {exc}") from exc
    _check_render_response(resp)
    return resp.content


def _check_render_response(resp) -> None:
    """Raise SandboxCrashError / SandboxTimeoutError / HTTPException from render service response."""
    if resp.status_code == 422:
        raise SandboxCrashError(resp.json().get("detail", "render error"))
    if resp.status_code == 503:
        raise SandboxTimeoutError(resp.json().get("detail", "render timeout"))
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)


app = FastAPI(
    title="ifcurl preview service",
    description="Renders ifc:// URLs to PNG images for embedding in Gitea and other consumers.",
    version="0.0.0",
)

# ---------------------------------------------------------------------------
# SSRF protection
# ---------------------------------------------------------------------------

_allowed_hosts: set[str] | None = None


def configure_allowed_hosts(hosts: set[str] | None) -> None:
    """Set the allowlist of git hosts the service will fetch from.

    Pass a set of hostname strings (optionally with :port) to restrict which
    hosts are contacted.  Pass None to allow all non-private remote hosts.
    """
    global _allowed_hosts
    _allowed_hosts = hosts


def _is_private_ip(host: str) -> bool:
    """Return True if *host* is a literal private/loopback/link-local IP."""
    bare = host.split(":")[0].strip("[]")  # strip port and IPv6 brackets
    try:
        addr = ipaddress.ip_address(bare)
        return (
            addr.is_loopback
            or addr.is_link_local
            or addr.is_private
            or addr.is_reserved
        )
    except ValueError:
        return False


def _ssrf_check(ifc_url: IfcUrl) -> None:
    """Raise HTTPException if the URL fails SSRF protection checks."""
    if ifc_url.transport == "local":
        raise HTTPException(
            status_code=403,
            detail="Local file transport is not permitted in service mode",
        )
    if _allowed_hosts is not None:
        if ifc_url.host not in _allowed_hosts:
            raise HTTPException(
                status_code=403,
                detail=f"Host {ifc_url.host!r} is not in the allowed-hosts list",
            )
    elif _is_private_ip(ifc_url.host):
        raise HTTPException(
            status_code=403,
            detail="Requests to private/loopback addresses are not permitted",
        )


# ---------------------------------------------------------------------------
# Tier 2: (commit_hexsha, path) → IFC bytes
# ---------------------------------------------------------------------------

_T2_MAX: int = int(os.environ.get("IFCURL_T2_MAX", "8"))
_t2_cache: OrderedDict[tuple[str, str], bytes] = OrderedDict()
_t2_lock = threading.Lock()


def _t2_get(hexsha: str, path: str) -> bytes | None:
    key = (hexsha, path)
    with _t2_lock:
        if key not in _t2_cache:
            return None
        _t2_cache.move_to_end(key)
        return _t2_cache[key]


def _t2_put(hexsha: str, path: str, data: bytes) -> None:
    key = (hexsha, path)
    with _t2_lock:
        _t2_cache[key] = data
        _t2_cache.move_to_end(key)
        while len(_t2_cache) > _T2_MAX:
            _t2_cache.popitem(last=False)


# ---------------------------------------------------------------------------
# Tier 3: (commit_hexsha, path, selector) → frozenset[GlobalId]
# ---------------------------------------------------------------------------

_T3_MAX: int = int(os.environ.get("IFCURL_T3_MAX", "64"))
_t3_cache: OrderedDict[tuple[str, str, str], frozenset[str]] = OrderedDict()
_t3_lock = threading.Lock()


def _t3_get(hexsha: str, path: str, selector: str) -> frozenset[str] | None:
    key = (hexsha, path, selector)
    with _t3_lock:
        if key not in _t3_cache:
            return None
        _t3_cache.move_to_end(key)
        return _t3_cache[key]


def _t3_put(hexsha: str, path: str, selector: str, guids: frozenset[str]) -> None:
    key = (hexsha, path, selector)
    with _t3_lock:
        _t3_cache[key] = guids
        _t3_cache.move_to_end(key)
        while len(_t3_cache) > _T3_MAX:
            _t3_cache.popitem(last=False)


# ---------------------------------------------------------------------------
# Tier 4: sha256(url) → PNG (filesystem, immutable refs only, no expiry)
# ---------------------------------------------------------------------------


def _t4_path(url: str) -> Path:
    cache_dir = Path(user_cache_dir("ifcurl")) / "renders" / "immutable"
    cache_dir.mkdir(parents=True, exist_ok=True)
    url_hash = hashlib.sha256(url.encode()).hexdigest()
    return cache_dir / f"{url_hash}.png"


def _t4_get(url: str) -> bytes | None:
    try:
        return _t4_path(url).read_bytes()
    except FileNotFoundError:
        return None


def _t4_put(url: str, png: bytes) -> None:
    _t4_path(url).write_bytes(png)


# ---------------------------------------------------------------------------
# Tier 4m: sha256(url) → PNG (filesystem, mutable refs, 5-minute TTL)
# ---------------------------------------------------------------------------

_T4M_TTL = 300  # seconds


def _t4m_path(url: str) -> Path:
    cache_dir = Path(user_cache_dir("ifcurl")) / "renders" / "mutable"
    cache_dir.mkdir(parents=True, exist_ok=True)
    url_hash = hashlib.sha256(url.encode()).hexdigest()
    return cache_dir / f"{url_hash}.png"


def _t4m_get(url: str) -> tuple[bytes, int] | None:
    """Return (png_bytes, remaining_seconds) or None if missing/expired."""
    path = _t4m_path(url)
    try:
        remaining = int(os.path.getmtime(path) + _T4M_TTL - time.time())
        if remaining <= 0:
            return None
        return path.read_bytes(), remaining
    except (FileNotFoundError, OSError):
        return None


def _t4m_put(url: str, png: bytes) -> None:
    _t4m_path(url).write_bytes(png)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class PreviewRequest(BaseModel):
    url: str
    token: str | None = None
    """Optional bearer token for git authentication.

    When provided, takes precedence over any token configured in
    ``~/.config/ifcurl/tokens.json``.  Intended for co-located Gitea
    deployments that pass the requesting user's session token.
    """


class BcfRequest(BaseModel):
    url: str
    title: str = "IFC View"
    comment: str = ""
    token: str | None = None


class DiffRequest(BaseModel):
    base: str
    """ifc:// URL for the base (older) commit."""
    head: str
    """ifc:// URL for the head (newer) commit."""
    token: str | None = None
    """Optional bearer token for git authentication."""


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/preview")
def preview_get(url: str, token: str | None = None) -> Response:
    """GET variant of POST /preview for use in HTML ``<img src>`` tags.

    The ``url`` query parameter is the ifc:// URL to render.  The optional
    ``token`` parameter accepts a bearer token for private-repository access,
    mirroring the JSON body ``token`` field of POST /preview.  All caching
    and authentication behaviour is otherwise identical to the POST endpoint.
    """
    return preview(PreviewRequest(url=url, token=token))


@app.post("/preview")
def preview(request: PreviewRequest) -> Response:
    """Render an ifc:// URL to a PNG image.

    Returns ``image/png``.  For immutable refs the result is cached on disk
    and served directly on subsequent requests without re-rendering.
    """
    # --- Parse ---
    try:
        ifc_url = IfcUrl.parse(request.url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if ifc_url.path is None:
        raise HTTPException(status_code=400, detail="URL has no 'path' parameter")

    # --- SSRF protection ---
    _ssrf_check(ifc_url)

    # --- Tier 4 / 4m: cached PNG ---
    if ifc_url.is_mutable_ref():
        t4m_hit = _t4m_get(request.url)
        if t4m_hit is not None:
            cached_png, remaining = t4m_hit
            return Response(
                content=cached_png,
                media_type="image/png",
                headers={"Cache-Control": f"public, max-age={remaining}"},
            )
    else:
        cached_png = _t4_get(request.url)
        if cached_png is not None:
            return Response(
                content=cached_png,
                media_type="image/png",
                headers={"Cache-Control": "public, max-age=31536000, immutable"},
            )

    # --- Resolve authentication token ---
    token = request.token
    if token is None and ifc_url.host:
        token = get_token_for_host(ifc_url.host)

    # --- Fetch IFC bytes + commit hexsha ---
    try:
        hexsha, ifc_bytes, is_stale = fetch_ifc(ifc_url, token=token)
    except ImportError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    # --- Tier 2: populate / use byte cache ---
    cached_bytes = _t2_get(hexsha, ifc_url.path)
    if cached_bytes is not None:
        ifc_bytes = cached_bytes
    else:
        _t2_put(hexsha, ifc_url.path, ifc_bytes)

    # --- Tier 3: check GUID cache ---
    # T3 hit  → pass element_guids (child converts to step IDs), skip selector
    # T3 miss → pass selector string (child runs filter_elements and returns GUIDs)
    cached_guids: frozenset[str] | None = None
    selector_for_sandbox: str | None = ifc_url.selector
    element_guids: frozenset[str] | None = None

    if ifc_url.selector:
        cached_guids = _t3_get(hexsha, ifc_url.path, ifc_url.selector)
        if cached_guids is not None:
            element_guids = cached_guids
            selector_for_sandbox = None

    # --- Sandboxed parse + select + render ---
    try:
        if _RENDER_SOCKET:
            new_guids, png_bytes = _render_via_socket(
                ifc_bytes,
                selector_for_sandbox,
                element_guids,
                ifc_url.camera,
                ifc_url.fov,
                ifc_url.scale,
                ifc_url.clips or [],
                ifc_url.visibility,
            )
        else:
            new_guids, png_bytes = run_sandboxed(
                _sandboxed_pipeline,
                ifc_bytes,
                selector_for_sandbox,
                element_guids,
                ifc_url.camera,
                ifc_url.fov,
                ifc_url.scale,
                ifc_url.clips or [],
                ifc_url.visibility,
            )
    except SandboxCrashError as exc:
        raise HTTPException(
            status_code=422, detail=f"IFC parse/render crashed: {exc}"
        ) from exc
    except SandboxTimeoutError as exc:
        raise HTTPException(status_code=503, detail=f"Render timed out: {exc}") from exc
    except ImportError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # --- Tier 3: populate GUID cache from selector run ---
    if new_guids is not None and ifc_url.selector:
        _t3_put(hexsha, ifc_url.path, ifc_url.selector, new_guids)

    # --- Tier 4 / 4m: store PNG and return with appropriate cache headers ---
    extra_headers = {"X-Ifcurl-Cache": "stale"} if is_stale else {}
    if ifc_url.is_mutable_ref():
        _t4m_put(request.url, png_bytes)
        return Response(
            content=png_bytes,
            media_type="image/png",
            headers={"Cache-Control": f"public, max-age={_T4M_TTL}", **extra_headers},
        )
    else:
        _t4_put(request.url, png_bytes)
        return Response(
            content=png_bytes,
            media_type="image/png",
            headers={
                "Cache-Control": "public, max-age=31536000, immutable",
                **extra_headers,
            },
        )


@app.post("/bcf")
def bcf_export(request: BcfRequest) -> Response:
    """Generate a BCF 2.1 zip from an ifc:// URL viewpoint.

    Returns ``application/octet-stream`` with a ``.bcf`` zip file containing
    the camera, clipping planes, and (when a selector is present) the resolved
    component GUID selection.
    """
    try:
        ifc_url = IfcUrl.parse(request.url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if ifc_url.path is None:
        raise HTTPException(status_code=400, detail="URL has no 'path' parameter")

    _ssrf_check(ifc_url)

    # Resolve selector → component GUIDs when present.
    guids: list[str] | None = None
    if ifc_url.selector:
        token = request.token
        if token is None and ifc_url.host:
            token = get_token_for_host(ifc_url.host)
        try:
            hexsha, ifc_bytes, _ = fetch_ifc(ifc_url, token=token)
        except (ImportError, ValueError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        cached = _t2_get(hexsha, ifc_url.path)
        ifc_bytes = cached if cached is not None else ifc_bytes
        if cached is None:
            _t2_put(hexsha, ifc_url.path, ifc_bytes)

        try:
            if _RENDER_SOCKET:
                guids = _select_via_socket(ifc_bytes, ifc_url.selector)
            else:
                guids = run_sandboxed(_sandboxed_select, ifc_bytes, ifc_url.selector)
        except SandboxCrashError as exc:
            raise HTTPException(
                status_code=422, detail=f"IFC parse/select crashed: {exc}"
            ) from exc
        except SandboxTimeoutError as exc:
            raise HTTPException(
                status_code=503, detail=f"Select timed out: {exc}"
            ) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=422, detail=f"Invalid selector: {exc}"
            ) from exc

    bcf_bytes = build_bcf(
        camera=ifc_url.camera,
        fov=ifc_url.fov,
        scale=ifc_url.scale,
        clips=ifc_url.clips or None,
        guids=guids,
        visibility=ifc_url.visibility,
        title=request.title,
        comment=request.comment,
        description=request.url,
    )
    return Response(
        content=bcf_bytes,
        media_type="application/octet-stream",
        headers={"Content-Disposition": 'attachment; filename="view.bcf"'},
    )


@app.get("/render_diff")
def render_diff_get(base: str, head: str, token: str | None = None) -> Response:
    """GET variant of POST /render_diff for use in HTML ``<img src>`` tags.

    *base* and *head* are ifc:// URLs (URL-encoded) for the base and head
    commits of the same IFC file.  The optional *token* is a bearer token
    for private-repository access.
    """
    return render_diff(DiffRequest(base=base, head=head, token=token))


@app.post("/render_diff")
def render_diff(request: DiffRequest) -> Response:
    """Render a two-pass IFC diff image (green=added, blue=modified, red=removed).

    Both URLs must refer to the same IFC file path in the same repository.
    The result is cached on disk when both refs are immutable (commit hashes).
    Returns ``image/png``.
    """
    # --- Parse ---
    try:
        base_url = IfcUrl.parse(request.base)
        head_url = IfcUrl.parse(request.head)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    for label, ifc_url in (("base", base_url), ("head", head_url)):
        if ifc_url.path is None:
            raise HTTPException(
                status_code=400, detail=f"{label} URL has no 'path' parameter"
            )

    if base_url.path != head_url.path:
        raise HTTPException(
            status_code=400,
            detail=f"base and head must refer to the same IFC path ({base_url.path!r} vs {head_url.path!r})",
        )

    # --- SSRF protection ---
    _ssrf_check(base_url)
    _ssrf_check(head_url)

    # --- Tier 4: cached diff PNG (immutable refs only) ---
    both_immutable = not base_url.is_mutable_ref() and not head_url.is_mutable_ref()
    cache_key = f"diff:{request.base}:{request.head}"
    if both_immutable:
        cached_png = _t4_get(cache_key)
        if cached_png is not None:
            return Response(
                content=cached_png,
                media_type="image/png",
                headers={"Cache-Control": "public, max-age=31536000, immutable"},
            )

    # --- Resolve authentication token ---
    token = request.token
    if token is None and base_url.host:
        token = get_token_for_host(base_url.host)

    # --- Fetch IFC bytes for both commits ---
    try:
        base_hexsha, base_bytes, base_stale = fetch_ifc(base_url, token=token)
        head_hexsha, head_bytes, head_stale = fetch_ifc(head_url, token=token)
    except ImportError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    # --- Tier 2: populate byte cache ---
    for hexsha, path, ifc_bytes in (
        (base_hexsha, base_url.path, base_bytes),
        (head_hexsha, head_url.path, head_bytes),
    ):
        cached = _t2_get(hexsha, path)
        if cached is not None:
            if hexsha == base_hexsha:
                base_bytes = cached
            else:
                head_bytes = cached
        else:
            _t2_put(hexsha, path, ifc_bytes)

    # --- Get git diff text ---
    try:
        raw_diff = git_diff_text(base_url, head_url, token=token)
    except (ImportError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"Could not diff: {exc}") from exc

    # --- Sandboxed parse + expand + render ---
    try:
        if _RENDER_SOCKET:
            png_bytes = _diff_via_socket(
                head_bytes,
                base_bytes,
                raw_diff,
                head_url.camera,
                head_url.fov,
                head_url.scale,
                head_url.clips or [],
            )
        else:
            png_bytes = run_sandboxed(
                _sandboxed_diff,
                head_bytes,
                base_bytes,
                raw_diff,
                head_url.camera,
                head_url.fov,
                head_url.scale,
                head_url.clips or [],
            )
    except SandboxCrashError as exc:
        raise HTTPException(
            status_code=422, detail=f"IFC diff render crashed: {exc}"
        ) from exc
    except SandboxTimeoutError as exc:
        raise HTTPException(
            status_code=503, detail=f"Diff render timed out: {exc}"
        ) from exc
    except ImportError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # --- Tier 4: cache and return ---
    diff_stale_headers = (
        {"X-Ifcurl-Cache": "stale"} if (base_stale or head_stale) else {}
    )
    if both_immutable:
        _t4_put(cache_key, png_bytes)
        return Response(
            content=png_bytes,
            media_type="image/png",
            headers={
                "Cache-Control": "public, max-age=31536000, immutable",
                **diff_stale_headers,
            },
        )

    return Response(
        content=png_bytes,
        media_type="image/png",
        headers={"Cache-Control": "no-store", **diff_stale_headers},
    )
