# IFC URL — OpenCDE Foundation API 1.1 OAuth2 proxy routes
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

"""OpenCDE Foundation API 1.1 — OAuth2 proxy routes.

Routes are mounted at /foundation/1.1 in service.py.

Forgejo is used as the OAuth2 provider. A one-time setup step is required:
register an OAuth2 application in Forgejo admin settings (Admin → Applications
→ OAuth2 Applications) and set the following environment variables:

    IFCURL_OAUTH2_CLIENT_ID     — OAuth2 application client ID
    IFCURL_OAUTH2_CLIENT_SECRET — OAuth2 application client secret

The auth_url endpoint constructs and returns the Forgejo authorization URL.
The token and token_refresh endpoints proxy to Forgejo's token endpoint,
injecting the client credentials from env vars so callers never handle them.
"""

from __future__ import annotations

import os
from urllib.parse import quote

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter(prefix="/foundation/1.1")

_FORGEJO_URL: str = os.environ.get("IFCURL_FORGEJO_URL", "http://localhost:3000")
_CLIENT_ID: str | None = os.environ.get("IFCURL_OAUTH2_CLIENT_ID")
_CLIENT_SECRET: str | None = os.environ.get("IFCURL_OAUTH2_CLIENT_SECRET")


def _credentials() -> tuple[str, str]:
    """Return (client_id, client_secret) or raise 503 if not configured."""
    if not _CLIENT_ID or not _CLIENT_SECRET:
        raise HTTPException(
            status_code=503,
            detail=(
                "OAuth2 not configured — set IFCURL_OAUTH2_CLIENT_ID "
                "and IFCURL_OAUTH2_CLIENT_SECRET"
            ),
        )
    return _CLIENT_ID, _CLIENT_SECRET


# ---------------------------------------------------------------------------
# GET /foundation/1.1/oauth2/auth_url
# ---------------------------------------------------------------------------

@router.get("/oauth2/auth_url")
def oauth2_auth_url(request: Request, redirect_uri: str | None = None) -> JSONResponse:
    """Return the Forgejo OAuth2 authorization URL.

    The client should redirect the user to the returned URL to begin the
    authorization code flow.  The optional ``redirect_uri`` query parameter
    is forwarded to Forgejo; it must match a URI registered with the OAuth2
    application.
    """
    client_id, _ = _credentials()
    params = f"client_id={quote(client_id, safe='')}&response_type=code"
    if redirect_uri:
        params += f"&redirect_uri={quote(redirect_uri, safe='')}"
    return JSONResponse(
        {"url": f"{_FORGEJO_URL}/login/oauth/authorize?{params}", "expires_in": 3600}
    )


# ---------------------------------------------------------------------------
# POST /foundation/1.1/oauth2/token
# ---------------------------------------------------------------------------

class TokenRequest(BaseModel):
    code: str
    redirect_uri: str | None = None


@router.post("/oauth2/token")
def oauth2_token(body: TokenRequest) -> JSONResponse:
    """Exchange an authorization code for an access + refresh token pair.

    Proxies to Forgejo's token endpoint, injecting the stored client
    credentials.  The caller never sees or handles client_id/client_secret.
    """
    client_id, client_secret = _credentials()
    payload: dict = {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": body.code,
        "grant_type": "authorization_code",
    }
    if body.redirect_uri:
        payload["redirect_uri"] = body.redirect_uri
    return _proxy_token(payload)


# ---------------------------------------------------------------------------
# POST /foundation/1.1/oauth2/token_refresh
# ---------------------------------------------------------------------------

class RefreshRequest(BaseModel):
    refresh_token: str


@router.post("/oauth2/token_refresh")
def oauth2_token_refresh(body: RefreshRequest) -> JSONResponse:
    """Exchange a refresh token for a new access + refresh token pair.

    Proxies to Forgejo's token endpoint with grant_type=refresh_token.
    """
    client_id, client_secret = _credentials()
    payload: dict = {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": body.refresh_token,
        "grant_type": "refresh_token",
    }
    return _proxy_token(payload)


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

def _proxy_token(payload: dict) -> JSONResponse:
    """POST payload to Forgejo's token endpoint and return the JSON response."""
    try:
        resp = httpx.post(
            f"{_FORGEJO_URL}/login/oauth/access_token",
            json=payload,
            headers={"Accept": "application/json"},
            timeout=10,
        )
    except httpx.TransportError as exc:
        raise HTTPException(status_code=503, detail=f"Forgejo unreachable: {exc}") from exc
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return JSONResponse(resp.json())
