# IFC URL — OpenCDE Documents API routes
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

"""OpenCDE Documents API 1.0 — read-only, minimal implementation.

Routes are mounted at /documents/1.0 in service.py and proxied at
/documents/ on the Forgejo hostname via nginx/Caddy.

document_id is a base64url encoding of "owner/repo/path", making it stable
and opaque to callers.

Versions exposed: one per git tag (sorted chronologically, oldest = index 1),
plus the most recent HEAD commit if it is not already covered by a tag.  This
mirrors CDE conventions where only released/tagged versions are visible, while
still surfacing the current work-in-progress state.

version_number is the tag name for tagged versions and the short SHA for HEAD.

Download URLs point at Forgejo's existing raw file endpoint:
  {forgejo_host}/{owner}/{repo}/raw/commit/{sha}/{path}

Auth is forwarded verbatim so Forgejo enforces per-user permissions.
"""

from __future__ import annotations

import base64

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import JSONResponse

from ifcurl.bcf_api import _auth, _fget

router = APIRouter(prefix="/documents/1.0")

# ---------------------------------------------------------------------------
# document_id encoding / decoding
# ---------------------------------------------------------------------------

def encode_document_id(owner: str, repo: str, path: str) -> str:
    """Encode owner/repo/path as a stable base64url document_id."""
    return base64.urlsafe_b64encode(f"{owner}/{repo}/{path}".encode()).rstrip(b"=").decode()


def decode_document_id(doc_id: str) -> tuple[str, str, str]:
    """Decode a document_id back to (owner, repo, path).  Raises ValueError if malformed."""
    padding = "=" * (4 - len(doc_id) % 4) if len(doc_id) % 4 else ""
    try:
        decoded = base64.urlsafe_b64decode(doc_id + padding).decode()
    except Exception as exc:
        raise ValueError(f"Invalid document_id: {doc_id!r}") from exc
    parts = decoded.split("/", 2)
    if len(parts) != 3 or not all(parts):
        raise ValueError(f"Invalid document_id: {doc_id!r}")
    return parts[0], parts[1], parts[2]


# ---------------------------------------------------------------------------
# Version enumeration
# ---------------------------------------------------------------------------

def _versions_for_file(owner: str, repo: str, path: str, auth: str | None, forgejo_base: str) -> list[dict]:
    """Return a list of BCF version dicts for the file, tagged releases + HEAD."""
    # Tagged versions — one entry per tag, sorted oldest-first by commit date.
    tags = _fget(f"/api/v1/repos/{owner}/{repo}/tags", auth, params={"limit": 50})
    tags = tags if isinstance(tags, list) else []

    seen_shas: set[str] = set()
    versions: list[dict] = []

    for tag in sorted(tags, key=lambda t: t.get("commit", {}).get("created", "")):
        sha = (tag.get("commit") or {}).get("sha", "")
        if not sha or sha in seen_shas:
            continue
        seen_shas.add(sha)
        versions.append({
            "sha": sha,
            "date": (tag.get("commit") or {}).get("created", ""),
            "version_number": tag.get("name", sha[:8]),
            "author_name": "",
            "author_email": "",
        })

    # HEAD — add only if its commit SHA is not already represented by a tag.
    head_commits = _fget(
        f"/api/v1/repos/{owner}/{repo}/commits",
        auth,
        params={"path": path, "limit": 1, "sha": "HEAD"},
    )
    if isinstance(head_commits, list) and head_commits:
        head = head_commits[0]
        sha = head.get("sha", "")
        if sha and sha not in seen_shas:
            commit_meta = head.get("commit") or {}
            author = commit_meta.get("author") or {}
            committer = commit_meta.get("committer") or {}
            versions.append({
                "sha": sha,
                "date": committer.get("date") or author.get("date", ""),
                "version_number": sha[:8],
                "author_name": author.get("name", ""),
                "author_email": author.get("email", ""),
            })

    return [
        {
            "version_index": i,
            "version_number": v["version_number"],
            "creation_date": v["date"],
            "created_by": {"id": v["author_email"], "name": v["author_name"]},
            "document_version_download": f"{forgejo_base}/{owner}/{repo}/raw/commit/{v['sha']}/{path}",
            "document_version_upload": None,
        }
        for i, v in enumerate(versions, start=1)
    ]


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------

@router.get("/document-metadata/{document_id}")
def get_document_metadata(document_id: str, request: Request) -> JSONResponse:
    """Return metadata for a single document_id."""
    auth = _auth(request)
    try:
        owner, repo, path = decode_document_id(document_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Invalid document_id")
    info = _fget(f"/api/v1/repos/{owner}/{repo}/contents/{path}", auth)
    if not isinstance(info, dict):
        raise HTTPException(status_code=404, detail="File not found")
    return JSONResponse({
        "document_id": document_id,
        "name": info.get("name", path.rsplit("/", 1)[-1]),
        "description": f"{owner}/{repo}/{path}",
    })


@router.post("/document-versions")
def document_versions(request: Request, body: dict = Body(default={})) -> JSONResponse:
    """Return tagged releases + HEAD for each requested document_id.

    Implements the OpenCDE Documents API resolution step (SPECIFICATION.md §9
    step 3).  Tagged versions are indexed oldest=1, newest=N; HEAD is appended
    as N+1 when it is not already tagged.  version_number is the tag name for
    releases and the short SHA for HEAD.
    """
    auth = _auth(request)
    host = request.headers.get("x-forwarded-host") or request.headers.get("host", "localhost")
    proto = request.headers.get("x-forwarded-proto", "https")
    forgejo_base = f"{proto}://{host}"

    result = []
    for doc_id in body.get("document_ids", []):
        try:
            owner, repo, path = decode_document_id(doc_id)
        except ValueError:
            continue
        for v in _versions_for_file(owner, repo, path, auth, forgejo_base):
            result.append({"document_id": doc_id, **v})

    return JSONResponse(result)
