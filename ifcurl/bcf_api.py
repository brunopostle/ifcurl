# IFC URL — BCF 3.0 REST API routes
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

"""BCF 3.0 REST API — translation layer over Forgejo issues and comments.

Routes are mounted at /bcf/3.0 in service.py.  Authentication is forwarded
verbatim: the Authorization: Bearer header from the BCF client is passed to
Forgejo's REST API, so Forgejo's own auth middleware enforces per-user
permissions with no extra machinery.

Mapping:
  BCF Project   ← Forgejo repository  (project_id = "owner/repo")
  BCF Topic     ← Forgejo issue
  BCF Comment   ← Forgejo comment
  BCF Viewpoint ← first ifc:// URL found in a comment body

GUIDs are derived deterministically from the Forgejo resource IDs so that
no new storage is required. The issue number / comment ID is embedded in the
last 12 hex characters of the UUID and can be recovered without a lookup table.
"""

from __future__ import annotations

import hashlib
import os
import re

import httpx
from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from ifcurl.bcf import bcf_viewpoint_to_ifc_url, ifc_url_to_bcf_viewpoint
from ifcurl.url import IfcUrl

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_FORGEJO_URL: str = os.environ.get("IFCURL_FORGEJO_URL", "http://localhost:3000")
_PREVIEW_URL: str = os.environ.get("IFCURL_PREVIEW_URL", "http://localhost:8000")

# ---------------------------------------------------------------------------
# GUID encoding / decoding
#
# Each GUID embeds the Forgejo numeric ID in the last 12 hex characters so
# the mapping is reversible without a lookup table.  The first 20 hex chars
# come from a SHA-1 hash of "owner/repo".  A nibble at position 13 encodes
# the resource type: a=topic, b=comment, c=viewpoint.
# ---------------------------------------------------------------------------

def _repo_prefix(owner: str, repo: str) -> str:
    h = hashlib.sha1(f"{owner}/{repo}".encode()).digest().hex()
    variant = (int(h[16:20], 16) & 0x3FFF) | 0x8000
    return f"{h[:8]}-{h[8:12]}-{{t}}{h[12:15]}-{variant:04x}"


def make_topic_guid(owner: str, repo: str, number: int) -> str:
    return f"{_repo_prefix(owner, repo).format(t='a')}-{number:012x}"


def make_comment_guid(owner: str, repo: str, comment_id: int) -> str:
    return f"{_repo_prefix(owner, repo).format(t='b')}-{comment_id:012x}"


def make_viewpoint_guid(owner: str, repo: str, comment_id: int) -> str:
    return f"{_repo_prefix(owner, repo).format(t='c')}-{comment_id:012x}"


def _id_from_guid(guid: str) -> int:
    return int(guid.split("-")[-1], 16)


# ---------------------------------------------------------------------------
# ifc:// URL extraction
# ---------------------------------------------------------------------------

_IFC_URL_RE = re.compile(r"ifc://\S+")


def _first_ifc_url(text: str | None) -> str | None:
    if not text:
        return None
    m = _IFC_URL_RE.search(text)
    return m.group(0) if m else None


# ---------------------------------------------------------------------------
# Forgejo API client
# ---------------------------------------------------------------------------

def _headers(auth: str | None) -> dict[str, str]:
    h: dict[str, str] = {"Content-Type": "application/json"}
    if auth:
        h["Authorization"] = auth
    return h


def _fget(path: str, auth: str | None, params: dict | None = None) -> dict | list:
    r = httpx.get(f"{_FORGEJO_URL}{path}", headers=_headers(auth), params=params)
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="Not found")
    if r.status_code in (401, 403):
        raise HTTPException(status_code=r.status_code, detail="Unauthorized")
    r.raise_for_status()
    return r.json()


def _fpost(path: str, auth: str | None, body: dict) -> dict:
    r = httpx.post(f"{_FORGEJO_URL}{path}", headers=_headers(auth), json=body)
    if r.status_code in (401, 403):
        raise HTTPException(status_code=r.status_code, detail="Unauthorized")
    r.raise_for_status()
    return r.json()


def _fpatch(path: str, auth: str | None, body: dict) -> dict:
    r = httpx.patch(f"{_FORGEJO_URL}{path}", headers=_headers(auth), json=body)
    if r.status_code in (401, 403):
        raise HTTPException(status_code=r.status_code, detail="Unauthorized")
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------------------
# Conversion helpers
# ---------------------------------------------------------------------------

_STATUS_MAP = {"open": "Open", "closed": "Closed"}
_STATUS_REVERSE = {"Open": "open", "Closed": "closed"}


def _issue_to_topic(issue: dict, owner: str, repo: str) -> dict:
    number = issue["number"]
    return {
        "guid": make_topic_guid(owner, repo, number),
        "topic_type": "Issue",
        "topic_status": _STATUS_MAP.get(issue.get("state", "open"), "Open"),
        "title": issue.get("title", ""),
        "description": issue.get("body", "") or "",
        "creation_date": issue.get("created_at", ""),
        "creation_author": (issue.get("user") or {}).get("login", ""),
        "modified_date": issue.get("updated_at", ""),
        "modified_author": (issue.get("user") or {}).get("login", ""),
        "assigned_to": ((issue.get("assignees") or [{}])[0] or {}).get("login") or None,
        "labels": [lbl["name"] for lbl in issue.get("labels", [])],
        "index": number,
    }


def _comment_to_bcf(comment: dict, owner: str, repo: str) -> dict:
    cid = comment["id"]
    has_viewpoint = bool(_first_ifc_url(comment.get("body")))
    return {
        "guid": make_comment_guid(owner, repo, cid),
        "date": comment.get("created_at", ""),
        "author": (comment.get("user") or {}).get("login", ""),
        "comment": comment.get("body", "") or "",
        "viewpoint_guid": make_viewpoint_guid(owner, repo, cid) if has_viewpoint else None,
    }


def _comment_to_viewpoint(comment: dict, owner: str, repo: str) -> dict | None:
    ifc_url_str = _first_ifc_url(comment.get("body"))
    if not ifc_url_str:
        return None
    try:
        parsed = IfcUrl.parse(ifc_url_str)
    except ValueError:
        return None
    return ifc_url_to_bcf_viewpoint(parsed, make_viewpoint_guid(owner, repo, comment["id"]))


def _find_issue(owner: str, repo: str, tguid: str, auth: str | None) -> dict:
    number = _id_from_guid(tguid)
    issue = _fget(f"/api/v1/repos/{owner}/{repo}/issues/{number}", auth)
    if not isinstance(issue, dict) or make_topic_guid(owner, repo, issue["number"]) != tguid:
        raise HTTPException(status_code=404, detail="Topic not found")
    return issue


def _find_comment(owner: str, repo: str, vpguid: str, auth: str | None) -> dict:
    cid = _id_from_guid(vpguid)
    comment = _fget(f"/api/v1/repos/{owner}/{repo}/issues/comments/{cid}", auth)
    if not isinstance(comment, dict) or make_viewpoint_guid(owner, repo, comment["id"]) != vpguid:
        raise HTTPException(status_code=404, detail="Viewpoint not found")
    return comment


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/bcf/3.0")


def _auth(request: Request) -> str | None:
    return request.headers.get("authorization")


# --- Projects ---

@router.get("/projects")
def list_projects(request: Request) -> JSONResponse:
    auth = _auth(request)
    result = _fget("/api/v1/repos/search", auth, params={"limit": 50})
    items = result if isinstance(result, list) else result.get("data", [])
    return JSONResponse([{"project_id": r["full_name"], "name": r["name"]} for r in items])


@router.get("/projects/{owner}/{repo}")
def get_project(owner: str, repo: str, request: Request) -> JSONResponse:
    auth = _auth(request)
    r = _fget(f"/api/v1/repos/{owner}/{repo}", auth)
    return JSONResponse({"project_id": r["full_name"], "name": r["name"]})


@router.get("/projects/{owner}/{repo}/extensions")
def get_extensions(owner: str, repo: str) -> JSONResponse:
    return JSONResponse({
        "topic_type": ["Issue", "Request", "Fault", "Inquiry"],
        "topic_status": ["Open", "Closed"],
        "priority": [],
        "label": [],
        "stage": [],
        "user_id_type": [],
    })


# --- Topics ---

@router.get("/projects/{owner}/{repo}/topics")
def list_topics(owner: str, repo: str, request: Request) -> JSONResponse:
    auth = _auth(request)
    qp = request.query_params
    forgejo_params: dict = {"type": "issues", "limit": 50}
    status = qp.get("topic_status")
    forgejo_params["state"] = _STATUS_REVERSE.get(status, "open") if status else "open"
    if assigned_to := qp.get("assigned_to"):
        forgejo_params["assignee"] = assigned_to
    if label := qp.get("label"):
        forgejo_params["label"] = label
    if since := qp.get("modified_after"):
        forgejo_params["since"] = since
    if before := qp.get("modified_before"):
        forgejo_params["before"] = before
    issues = _fget(f"/api/v1/repos/{owner}/{repo}/issues", auth, params=forgejo_params)
    return JSONResponse([_issue_to_topic(i, owner, repo) for i in (issues if isinstance(issues, list) else [])])


@router.post("/projects/{owner}/{repo}/topics", status_code=201)
def create_topic(owner: str, repo: str, request: Request, body: dict = Body(default={})) -> JSONResponse:
    auth = _auth(request)
    issue = _fpost(f"/api/v1/repos/{owner}/{repo}/issues", auth, {
        "title": body.get("title", "BCF Topic"),
        "body": body.get("description", ""),
    })
    return JSONResponse(_issue_to_topic(issue, owner, repo), status_code=201)


@router.get("/projects/{owner}/{repo}/topics/{tguid}")
def get_topic(owner: str, repo: str, tguid: str, request: Request) -> JSONResponse:
    auth = _auth(request)
    issue = _find_issue(owner, repo, tguid, auth)
    return JSONResponse(_issue_to_topic(issue, owner, repo))


@router.put("/projects/{owner}/{repo}/topics/{tguid}")
def update_topic(owner: str, repo: str, tguid: str, request: Request, body: dict = Body(default={})) -> JSONResponse:
    auth = _auth(request)
    issue = _find_issue(owner, repo, tguid, auth)
    patch: dict = {}
    if "title" in body:
        patch["title"] = body["title"]
    if "description" in body:
        patch["body"] = body["description"]
    if "topic_status" in body:
        patch["state"] = _STATUS_REVERSE.get(body["topic_status"], "open")
    updated = _fpatch(f"/api/v1/repos/{owner}/{repo}/issues/{issue['number']}", auth, patch)
    return JSONResponse(_issue_to_topic(updated, owner, repo))


# --- Comments ---

@router.get("/projects/{owner}/{repo}/topics/{tguid}/comments")
def list_comments(owner: str, repo: str, tguid: str, request: Request) -> JSONResponse:
    auth = _auth(request)
    issue = _find_issue(owner, repo, tguid, auth)
    comments = _fget(f"/api/v1/repos/{owner}/{repo}/issues/{issue['number']}/comments", auth)
    return JSONResponse([_comment_to_bcf(c, owner, repo) for c in (comments if isinstance(comments, list) else [])])


@router.post("/projects/{owner}/{repo}/topics/{tguid}/comments", status_code=201)
def create_comment(owner: str, repo: str, tguid: str, request: Request, body: dict = Body(default={})) -> JSONResponse:
    auth = _auth(request)
    issue = _find_issue(owner, repo, tguid, auth)
    comment = _fpost(f"/api/v1/repos/{owner}/{repo}/issues/{issue['number']}/comments", auth, {
        "body": body.get("comment", ""),
    })
    return JSONResponse(_comment_to_bcf(comment, owner, repo), status_code=201)


# --- Viewpoints ---

@router.get("/projects/{owner}/{repo}/topics/{tguid}/viewpoints")
def list_viewpoints(owner: str, repo: str, tguid: str, request: Request) -> JSONResponse:
    auth = _auth(request)
    issue = _find_issue(owner, repo, tguid, auth)
    comments = _fget(f"/api/v1/repos/{owner}/{repo}/issues/{issue['number']}/comments", auth)
    viewpoints = [vp for c in (comments if isinstance(comments, list) else []) if (vp := _comment_to_viewpoint(c, owner, repo))]
    return JSONResponse(viewpoints)


@router.post("/projects/{owner}/{repo}/topics/{tguid}/viewpoints", status_code=201)
def create_viewpoint(owner: str, repo: str, tguid: str, request: Request, body: dict = Body(default={})) -> JSONResponse:
    auth = _auth(request)
    issue = _find_issue(owner, repo, tguid, auth)
    ifc_url_str = _first_ifc_url(issue.get("body"))
    if not ifc_url_str:
        raise HTTPException(status_code=422, detail="Issue body contains no ifc:// URL to use as repo/ref/path context")
    try:
        base = IfcUrl.parse(ifc_url_str)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    ifc_url = bcf_viewpoint_to_ifc_url(base, body)
    comment = _fpost(f"/api/v1/repos/{owner}/{repo}/issues/{issue['number']}/comments", auth, {"body": ifc_url})
    vp = _comment_to_viewpoint(comment, owner, repo)
    return JSONResponse(vp, status_code=201)


@router.get("/projects/{owner}/{repo}/topics/{tguid}/viewpoints/{vpguid}")
def get_viewpoint(owner: str, repo: str, tguid: str, vpguid: str, request: Request) -> JSONResponse:
    auth = _auth(request)
    issue = _find_issue(owner, repo, tguid, auth)
    comment = _find_comment(owner, repo, vpguid, auth)
    vp = _comment_to_viewpoint(comment, owner, repo)
    if not vp:
        raise HTTPException(status_code=404, detail="Viewpoint not found")
    return JSONResponse(vp)


@router.get("/projects/{owner}/{repo}/topics/{tguid}/viewpoints/{vpguid}/snapshot")
def get_snapshot(owner: str, repo: str, tguid: str, vpguid: str, request: Request) -> Response:
    auth = _auth(request)
    issue = _find_issue(owner, repo, tguid, auth)
    comment = _find_comment(owner, repo, vpguid, auth)
    ifc_url_str = _first_ifc_url(comment.get("body"))
    if not ifc_url_str:
        raise HTTPException(status_code=404, detail="No ifc:// URL in viewpoint comment")
    headers = {}
    if auth:
        headers["Authorization"] = auth
    r = httpx.get(f"{_PREVIEW_URL}/preview", params={"url": ifc_url_str}, headers=headers, timeout=120)
    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail="Preview service error")
    return Response(content=r.content, media_type="image/png")
