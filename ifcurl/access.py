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

"""Viewer access checks for the co-located Forgejo.

The service fetches from git with a stored service-account token
(``tokens.json``), but that token is a fetch credential, never a grant to the
caller.  Before any cache lookup or fetch for a repo on the co-located Forgejo,
:func:`resolve_fetch_token` asks Forgejo whether the *requesting user* can read
the repo, so Forgejo stays the sole authority on permissions:

- explicit ``token`` parameter → ``GET /api/v1/repos/<owner>/<repo>`` with it
- incoming ``Authorization`` header → same API route with that header
- otherwise → ``GET /<owner>/<repo>`` web route forwarding the ``Cookie``
  header (or none, for anonymous callers)

2xx means allowed; anything else (404, 403, redirect to ``/user/login``) is
answered with 404 so the response does not reveal that a private repo exists.

For hosts that are not the co-located Forgejo no check is possible, so every
fetch is anonymous: the stored token is never used, a caller-supplied token is
refused (400) and SSH transport is refused (403), since the service's SSH key
is a standing credential too.  The render cache and the bare-repo cache are
keyed without any credential, so this keeps them holding only content that
any caller could fetch anyway.

Configuration
-------------
``IFCURL_FORGEJO_URL``
    Internal base URL used for the check (default ``http://localhost:3000``).
``IFCURL_FORGEJO_HOSTS``
    Comma-separated ``host[:port]`` values that ifc:// URLs use for the
    co-located Forgejo (default: the host of ``IFCURL_FORGEJO_URL``).
``IFCURL_ACCESS_TTL``
    Seconds to cache each allow/deny decision per credential and repo
    (default 30, 0 disables).
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from collections import OrderedDict
from urllib.parse import quote, urlparse

import httpx
from fastapi import HTTPException

from ifcurl.auth import get_token_for_host
from ifcurl.url import IfcUrl

_FORGEJO_URL: str = os.environ.get("IFCURL_FORGEJO_URL", "http://localhost:3000").rstrip("/")
_FORGEJO_HOSTS: frozenset[str] = frozenset(
    h.strip()
    for h in os.environ.get("IFCURL_FORGEJO_HOSTS", urlparse(_FORGEJO_URL).netloc).split(",")
    if h.strip()
)
_ACCESS_TTL: float = float(os.environ.get("IFCURL_ACCESS_TTL", "30"))
_ACCESS_MAX = 1024
_CHECK_TIMEOUT = 10.0

_access_cache: OrderedDict[tuple[str, str, str], tuple[float, bool]] = OrderedDict()
_access_lock = threading.Lock()


def is_forgejo_host(host: str) -> bool:
    """Return True if *host* is the co-located Forgejo instance."""
    return host in _FORGEJO_HOSTS


def _owner_repo(ifc_url: IfcUrl) -> tuple[str, str] | None:
    parts = ifc_url.repo_path.strip("/").split("/")
    if len(parts) != 2 or not all(parts):
        return None
    owner, repo = parts
    if repo.endswith(".git"):
        repo = repo[:-4]
    return owner, repo


def _ask_forgejo(owner: str, repo: str, authorization: str | None, cookie: str | None) -> bool:
    """Return True if Forgejo lets the presented credential read *owner/repo*."""
    o, r = quote(owner, safe=""), quote(repo, safe="")
    if authorization:
        url = f"{_FORGEJO_URL}/api/v1/repos/{o}/{r}"
        headers = {"Authorization": authorization}
    else:
        # Web route: certainly honours the session cookie, and redirects to
        # /user/login under REQUIRE_SIGNIN_VIEW.
        url = f"{_FORGEJO_URL}/{o}/{r}"
        headers = {"Cookie": cookie} if cookie else {}
    try:
        resp = httpx.get(url, headers=headers, timeout=_CHECK_TIMEOUT, follow_redirects=False)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="Could not verify repository access") from exc
    return 200 <= resp.status_code < 300


def viewer_can_read(ifc_url: IfcUrl, authorization: str | None, cookie: str | None) -> bool:
    """Return True if the caller presenting these credentials can read the repo.

    Decisions are cached per (host, repo, credential) for ``IFCURL_ACCESS_TTL``.
    """
    owner_repo = _owner_repo(ifc_url)
    if owner_repo is None:
        return False
    owner, repo = owner_repo
    credential = hashlib.sha256(f"{authorization or ''}\0{cookie or ''}".encode()).hexdigest()
    key = (ifc_url.host, f"{owner}/{repo}", credential)
    now = time.monotonic()
    if _ACCESS_TTL > 0:
        with _access_lock:
            hit = _access_cache.get(key)
            if hit is not None and hit[0] > now:
                return hit[1]
    allowed = _ask_forgejo(owner, repo, authorization, cookie)
    if _ACCESS_TTL > 0:
        with _access_lock:
            _access_cache[key] = (now + _ACCESS_TTL, allowed)
            _access_cache.move_to_end(key)
            while len(_access_cache) > _ACCESS_MAX:
                _access_cache.popitem(last=False)
    return allowed


def resolve_fetch_token(
    ifc_url: IfcUrl,
    explicit_token: str | None,
    authorization: str | None,
    cookie: str | None,
) -> str | None:
    """Return the git token to fetch *ifc_url* with, or raise 404 if denied.

    Must be called before any render-cache lookup for *ifc_url*.

    On the co-located Forgejo every request is checked, including those with
    an explicit token: cached renders and the shared bare-repo cache would
    otherwise serve private content to any caller presenting a bogus token.
    Elsewhere only anonymous fetches are allowed (see module docstring).
    """
    if not ifc_url.host or not is_forgejo_host(ifc_url.host):
        if explicit_token is not None:
            raise HTTPException(
                status_code=400,
                detail="A token is only accepted for the co-located Forgejo host",
            )
        if ifc_url.transport == "ssh":
            raise HTTPException(
                status_code=403,
                detail="SSH transport is only permitted for the co-located Forgejo host",
            )
        return None
    if explicit_token is not None:
        authorization = f"token {explicit_token}"
    if not viewer_can_read(ifc_url, authorization, cookie):
        raise HTTPException(status_code=404, detail="Not found")
    if explicit_token is not None:
        return explicit_token
    return get_token_for_host(ifc_url.host)
