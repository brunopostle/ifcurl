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

"""Token-based authentication for git hosts.

Configuration file
------------------
``~/.config/ifcurl/tokens.json`` (Linux/macOS) or
``%APPDATA%\\ifcurl\\tokens.json`` (Windows):

.. code-block:: json

    {
        "hosts": {
            "github.com": "ghp_your_token_here",
            "gitlab.example.com": "glpat_your_token_here"
        }
    }

The token is injected into HTTPS remote URLs as ``https://<token>@host/path``.
SSH transport is unaffected; SSH authentication uses the platform key store.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from platformdirs import user_config_dir


def config_path() -> Path:
    """Return the path to the ifcurl tokens config file."""
    return Path(user_config_dir("ifcurl")) / "tokens.json"


def get_token_for_host(host: str) -> str | None:
    """Return the configured token for *host*, or ``None`` if not set.

    Reads ``tokens.json`` from the OS config directory.  Silently returns
    ``None`` on any read or parse error so callers fall back to anonymous.
    """
    try:
        data = json.loads(config_path().read_text())
        return data.get("hosts", {}).get(host) or None
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return None


def inject_token(https_url: str, token: str) -> str:
    """Return *https_url* with *token* injected as the username credential.

    ``https://host/org/repo`` → ``https://<token>@host/org/repo``

    This format is accepted by GitHub, GitLab, Gitea, and most other git
    hosting platforms.  The token is never stored on disk — it only lives
    in the authenticated URL passed to git clone/fetch as a command-line
    argument.

    **Known limitation**: the token is visible in the OS process list
    (``ps aux``) for the duration of the git subprocess.  On shared servers
    this is a potential credential leak.  The standard mitigation is to use
    ``GIT_ASKPASS`` or a credential helper, which this implementation does
    not yet do.  As a workaround, configure SSH transport (``git@host:…``)
    rather than HTTPS for private repositories on shared hosts.
    """
    parsed = urlparse(https_url)
    port_suffix = f":{parsed.port}" if parsed.port else ""
    netloc = f"{token}@{parsed.hostname}{port_suffix}"
    return urlunparse(parsed._replace(netloc=netloc))
