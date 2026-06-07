# IFC URL — OpenCDE Documents API routes
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
import json
import os
from urllib.parse import quote

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from ifcurl.bcf_api import _auth, _fget, _FORGEJO_URL

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


# ---------------------------------------------------------------------------
# POST /documents/1.0/select-documents  +  GET /select-documents/ui
# ---------------------------------------------------------------------------

class _Callback(BaseModel):
    url: str
    expires_in: int = 3600


class SelectDocumentsRequest(BaseModel):
    callback: _Callback


def _bearer_token(request: Request) -> str | None:
    """Extract the OAuth2 access token from the request's Authorization header.

    Accepts both ``Bearer <token>`` and Forgejo's ``token <token>`` forms and
    returns the bare token, or ``None`` when no Authorization header is present.
    """
    header = request.headers.get("authorization")
    if not header:
        return None
    scheme, _, value = header.partition(" ")
    if scheme.lower() in ("bearer", "token") and value:
        return value
    return header  # opaque value with no recognised scheme — forward verbatim


@router.post("/select-documents")
def select_documents(body: SelectDocumentsRequest, request: Request) -> JSONResponse:
    """Return a URL for the document-picker UI.

    Implements the OpenCDE Documents API select-documents flow.  The caller
    redirects the user's browser to ``select_documents_url``; the picker lets
    the user choose an IFC file from a Forgejo repository, then redirects back
    to ``callback.url`` with ``document_ids[]`` query parameters appended.

    The OAuth2 access token presented on this request (``Authorization:
    Bearer``) is threaded through to the picker so it can query the Forgejo API
    on the user's behalf — without it the picker only sees public repositories.
    """
    host = request.headers.get("x-forwarded-host") or request.headers.get("host", "localhost")
    proto = request.headers.get("x-forwarded-proto", "https")
    base = f"{proto}://{host}"
    picker_url = (
        f"{base}/documents/1.0/select-documents/ui"
        f"?callback_url={quote(body.callback.url, safe='')}"
    )
    token = _bearer_token(request)
    if token:
        picker_url += f"&access_token={quote(token, safe='')}"
    return JSONResponse({"select_documents_url": picker_url})


@router.get("/select-documents/ui", response_class=HTMLResponse)
def select_documents_ui(callback_url: str, access_token: str | None = None) -> HTMLResponse:
    """Serve the document-picker HTML page.

    The page uses the Forgejo REST API to let the user browse repositories and
    select an IFC file.  On selection it redirects to ``callback_url`` with
    ``document_ids[]`` appended.

    When ``access_token`` is supplied (forwarded from the select-documents
    flow) the picker sends it as ``Authorization: Bearer`` on every Forgejo API
    call so private repositories the user can access are listed.
    """
    if not callback_url:
        raise HTTPException(status_code=400, detail="callback_url is required")
    return HTMLResponse(_picker_html(callback_url, access_token))


# ---------------------------------------------------------------------------
# Picker HTML (self-contained, no external dependencies)
# ---------------------------------------------------------------------------

_PICKER_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <!-- access_token may ride in this page's URL; keep it out of Referer headers -->
  <meta name="referrer" content="no-referrer">
  <title>Select IFC Document</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; }
    body { font-family: system-ui, sans-serif; max-width: 640px; margin: 2rem auto; padding: 0 1rem; color: #222; }
    h2 { margin-top: 0; }
    select { display: block; width: 100%; padding: 0.4em; font-size: 1em; margin-bottom: 0.8em; }
    #breadcrumb { font-size: 0.9em; margin-bottom: 0.4em; color: #555; }
    #breadcrumb span { cursor: pointer; color: #0070f3; text-decoration: underline; }
    ul { list-style: none; padding: 0; margin: 0; border: 1px solid #ddd; border-radius: 4px; }
    li { padding: 0.4em 0.7em; cursor: pointer; border-bottom: 1px solid #eee; font-size: 0.95em; }
    li:last-child { border-bottom: none; }
    li:hover { background: #f5f5f5; }
    li.up { color: #555; }
    li.dir::before { content: "\\1F4C1  "; }
    li.ifc { color: #005a9c; font-weight: 500; }
    li.ifc::before { content: "\\1F4C4  "; }
    #status { color: #666; font-size: 0.9em; min-height: 1.4em; }
    #error { color: #c00; font-size: 0.9em; }
  </style>
</head>
<body>
  <h2>Select IFC Document</h2>
  <p id="status">Loading repositories…</p>
  <p id="error"></p>
  <select id="repo-select" style="display:none" onchange="onRepoChange()">
    <option value="">— Select a repository —</option>
  </select>
  <div id="breadcrumb"></div>
  <ul id="file-list"></ul>
<script>
const CALLBACK_URL = __CALLBACK_URL__;
const FORGEJO_BASE = __FORGEJO_BASE__;
const ACCESS_TOKEN = __ACCESS_TOKEN__;
let currentOwner = '', currentRepo = '', currentPath = '';

function setStatus(msg) { document.getElementById('status').textContent = msg; }
function setError(msg) { document.getElementById('error').textContent = msg; }

async function apiFetch(path) {
  // Prefer the OAuth2 access token (so private repos are visible); fall back to
  // the browser's Forgejo session cookie when no token was supplied.
  const opts = ACCESS_TOKEN
    ? {headers: {Authorization: 'Bearer ' + ACCESS_TOKEN}}
    : {credentials: 'include'};
  const r = await fetch(FORGEJO_BASE + path, opts);
  if (!r.ok) throw new Error(r.status + ' ' + r.statusText);
  return r.json();
}

async function loadRepos() {
  try {
    const data = await apiFetch('/api/v1/repos/search?limit=50&sort=updated');
    const sel = document.getElementById('repo-select');
    for (const repo of (data.data || [])) {
      const opt = document.createElement('option');
      opt.value = repo.full_name;
      opt.textContent = repo.full_name;
      sel.appendChild(opt);
    }
    sel.style.display = '';
    setStatus('Select a repository.');
  } catch(e) {
    setError('Failed to load repositories: ' + e.message);
  }
}

function onRepoChange() {
  const val = document.getElementById('repo-select').value;
  if (!val) return;
  [currentOwner, currentRepo] = val.split('/', 2);
  browseDir('');
}

async function browseDir(path) {
  currentPath = path;
  renderBreadcrumb(path);
  setStatus('Loading…');
  try {
    const items = await apiFetch(
      '/api/v1/repos/' + currentOwner + '/' + currentRepo + '/contents/' + path
    );
    renderItems(Array.isArray(items) ? items : []);
    setStatus('');
  } catch(e) {
    setError('Failed to load directory: ' + e.message);
    setStatus('');
  }
}

function renderItems(items) {
  const list = document.getElementById('file-list');
  list.innerHTML = '';
  if (currentPath) {
    const parent = currentPath.includes('/')
      ? currentPath.substring(0, currentPath.lastIndexOf('/'))
      : '';
    addListItem(list, '⬆ ..', 'up', () => browseDir(parent));
  }
  const dirs = items.filter(i => i.type === 'dir').sort((a, b) => a.name.localeCompare(b.name));
  const ifcs = items.filter(i => i.type === 'file' && i.name.toLowerCase().endsWith('.ifc'))
                    .sort((a, b) => a.name.localeCompare(b.name));
  for (const d of dirs) addListItem(list, d.name, 'dir', () => browseDir(d.path));
  for (const f of ifcs) addListItem(list, f.name, 'ifc', () => selectFile(f.path));
  if (!dirs.length && !ifcs.length && currentPath) {
    addListItem(list, '(no IFC files in this directory)', '', null);
  }
}

function addListItem(list, text, cls, onclick) {
  const li = document.createElement('li');
  if (cls) li.className = cls;
  li.textContent = text;
  if (onclick) li.onclick = onclick;
  else li.style.cursor = 'default';
  list.appendChild(li);
}

function renderBreadcrumb(path) {
  const bc = document.getElementById('breadcrumb');
  bc.textContent = '';
  const parts = path ? path.split('/') : [];
  bc.appendChild(makeCrumb(currentRepo, ''));
  let built = '';
  for (const p of parts) {
    built = built ? built + '/' + p : p;
    const sep = document.createTextNode(' / ');
    bc.appendChild(sep);
    const captured = built;
    bc.appendChild(makeCrumb(p, captured));
  }
}

function makeCrumb(label, target) {
  const s = document.createElement('span');
  s.textContent = label;
  s.style.cursor = 'pointer';
  s.onclick = () => browseDir(target);
  return s;
}

function selectFile(path) {
  const raw = currentOwner + '/' + currentRepo + '/' + path;
  // base64url encode (matches Python encode_document_id)
  const b64 = btoa(unescape(encodeURIComponent(raw)))
    .replace(/\\+/g, '-').replace(/\\//g, '_').replace(/=+$/, '');
  const sep = CALLBACK_URL.includes('?') ? '&' : '?';
  window.location.href = CALLBACK_URL + sep + 'document_ids[]=' + encodeURIComponent(b64);
}

loadRepos();
</script>
</body>
</html>
"""


def _picker_html(callback_url: str, access_token: str | None = None) -> str:
    def _js(v: str | None) -> str:
        # json.dumps is valid JSON but </script> inside <script> breaks HTML
        # parsing; <\/ is a legal JSON escape that browsers handle correctly.
        # json.dumps(None) yields "null" — the JS treats that as "no token".
        return json.dumps(v).replace("</", "<\\/")

    return (
        _PICKER_TEMPLATE
        .replace("__CALLBACK_URL__", _js(callback_url))
        .replace("__FORGEJO_BASE__", _js(_FORGEJO_URL))
        .replace("__ACCESS_TOKEN__", _js(access_token))
    )
