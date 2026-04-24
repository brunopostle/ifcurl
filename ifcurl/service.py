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

import hashlib
import ipaddress
import os
import tempfile
import threading
import time

from collections import OrderedDict
from pathlib import Path

import ifcopenshell
import ifcopenshell.util.selector
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from platformdirs import user_cache_dir
from pydantic import BaseModel

from ifcurl import render as render_mod
from ifcurl.auth import get_token_for_host
from ifcurl.git import fetch_ifc

from ifcurl.url import IfcUrl

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
        return addr.is_loopback or addr.is_link_local or addr.is_private or addr.is_reserved
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Tier 2: (commit_hexsha, path) → IFC bytes
# ---------------------------------------------------------------------------

_T2_MAX = 8
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

_T3_MAX = 64
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


def _guids_to_step_ids(model: ifcopenshell.file, guids: frozenset[str]) -> list[int]:
    """Resolve a set of GlobalIds to step IDs in *model*."""
    ids = []
    for guid in guids:
        try:
            entity = model.by_guid(guid)
            ids.append(entity.id())
        except Exception:
            pass  # entity not present in this model version
    return ids


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
# Helper: load model from bytes via a temp file
# ---------------------------------------------------------------------------

def _load_model(ifc_bytes: bytes) -> ifcopenshell.file:
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".ifc")
    try:
        os.write(tmp_fd, ifc_bytes)
        os.close(tmp_fd)
        return ifcopenshell.open(tmp_path)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Could not parse IFC file: {exc}") from exc
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


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


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/preview")
def preview_get(url: str) -> Response:
    """GET variant of POST /preview for use in HTML ``<img src>`` tags.

    The ``url`` query parameter is the ifc:// URL to render.  All caching
    and authentication behaviour is identical to the POST endpoint.
    """
    return preview(PreviewRequest(url=url))


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
    if ifc_url.transport == "local":
        raise HTTPException(status_code=403, detail="Local file transport is not permitted in service mode")
    if _allowed_hosts is not None:
        if ifc_url.host not in _allowed_hosts:
            raise HTTPException(status_code=403, detail=f"Host {ifc_url.host!r} is not in the allowed-hosts list")
    elif _is_private_ip(ifc_url.host):
        raise HTTPException(status_code=403, detail="Requests to private/loopback addresses are not permitted")

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
        hexsha, ifc_bytes = fetch_ifc(ifc_url, token=token)
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

    # --- Load model ---
    model = _load_model(ifc_bytes)

    # --- Tier 3: resolve selector via GUID cache ---
    element_ids: list[int] | None = None
    selector_for_render: str | None = ifc_url.selector

    if ifc_url.selector:
        cached_guids = _t3_get(hexsha, ifc_url.path, ifc_url.selector)
        if cached_guids is not None:
            # Convert cached GUIDs to step IDs in this model instance.
            # Pass element_ids only (no selector) — filter_elements is skipped.
            # Note: for 'isolate' mode this iterates all geometry rather than
            # restricting the iterator; correctness is preserved, not optimal.
            element_ids = _guids_to_step_ids(model, cached_guids)
            selector_for_render = None
        else:
            # Run selector, cache GUIDs for future requests
            try:
                matched = list(ifcopenshell.util.selector.filter_elements(model, ifc_url.selector))
            except Exception as exc:
                raise HTTPException(status_code=422, detail=f"Invalid selector: {exc}") from exc
            guids = frozenset(
                e.GlobalId for e in matched if hasattr(e, "GlobalId") and e.GlobalId
            )
            _t3_put(hexsha, ifc_url.path, ifc_url.selector, guids)
            # Fall through: render() will re-run filter_elements via selector_for_render

    # --- Render ---
    try:
        png_bytes = render_mod.render(
            model,
            selector=selector_for_render,
            element_ids=element_ids,
            camera=ifc_url.camera,
            fov=ifc_url.fov,
            scale=ifc_url.scale,
            clips=ifc_url.clips or None,
            visibility=ifc_url.visibility,
        )
    except ImportError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # --- Tier 4 / 4m: store PNG and return with appropriate cache headers ---
    if ifc_url.is_mutable_ref():
        _t4m_put(request.url, png_bytes)
        return Response(
            content=png_bytes,
            media_type="image/png",
            headers={"Cache-Control": f"public, max-age={_T4M_TTL}"},
        )
    else:
        _t4_put(request.url, png_bytes)
        return Response(
            content=png_bytes,
            media_type="image/png",
            headers={"Cache-Control": "public, max-age=31536000, immutable"},
        )
