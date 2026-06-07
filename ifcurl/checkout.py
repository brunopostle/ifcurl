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

"""Full working-tree checkouts for ifc:// URLs.

The bare cache clone in :mod:`ifcurl.git` is read-only by design.  When a
tool (Bonsai, ifcviewer) needs an actual file path it can open and later
commit changes to, this module provides a writable checkout alongside the
bare clone.

Layout inside the per-URL cache directory::

    ~/.cache/ifcurl/<url-hash>/
        repo.git/      ← bare clone (fetch/read layer, managed by git.py)
        checkout/      ← full working tree (this module)

The checkout is cloned locally from the bare repo — no extra network
traffic — and placed at the requested commit as a **detached HEAD**.  A
detached HEAD forces the user to create a named branch before committing,
which is the intended workflow: edit in the checkout, branch, push to a
local working repo (see :mod:`ifcurl.discover` and the origin-remapping
issue ifcurl-oef).

Viewer artefacts (``*.ifcview``, ``*.rdbview``, etc.) are added to
``.git/info/exclude`` so they are never accidentally staged.
"""

from __future__ import annotations

import logging
from pathlib import Path

_logger = logging.getLogger(__name__)

_VIEWER_EXCLUDES = [
    "# ifcurl: viewer artefacts — do not commit",
    "*.ifcview",
    "*.rdbview",
    "*.rdb",
    "*.sqlite",
    "*.sqlite-wal",
    "*.sqlite-shm",
]

try:
    import git
    import git.exc

    _HAS_GITPYTHON = True
except ImportError:
    _HAS_GITPYTHON = False

from ifcurl.discover import find_local_repos
from ifcurl.git import _cache_dir_for, _compute_genesis, fetch_ifc
from ifcurl.url import IfcUrl


def get_checkout(ifc_url: IfcUrl, token: str | None = None) -> Path:
    """Return the path to the IFC file within a full working-tree checkout.

    Ensures the bare cache clone is up to date, then creates or updates a
    working checkout at ``<cache_dir>/checkout/``.  The checkout is placed
    at the resolved commit as a detached HEAD so the user must create a
    branch before committing any edits.

    Viewer artefact patterns are written to ``.git/info/exclude`` on first
    creation so they are locally ignored without modifying the repo's own
    ``.gitignore``.

    :param ifc_url: A parsed :class:`IfcUrl` with ``transport != 'local'``
        and a ``path`` parameter set.
    :param token: Optional bearer token for HTTPS authentication.
    :raises ImportError: If GitPython is not installed.
    :raises ValueError: For local-transport URLs (no checkout needed) or
        when ``ifc_url.path`` is unset.
    """
    if not _HAS_GITPYTHON:
        raise ImportError(
            "GitPython is not installed.  Install with: pip install gitpython"
        )
    if ifc_url.transport == "local":
        raise ValueError(
            "get_checkout is not applicable to local-transport URLs; "
            "access the file directly from the working tree."
        )
    if ifc_url.path is None:
        raise ValueError("URL has no 'path' parameter — cannot locate IFC file")

    hexsha, _, _ = fetch_ifc(ifc_url, token=token)
    cache_dir = _cache_dir_for(ifc_url.git_remote_url())
    checkout_dir = _ensure_checkout(cache_dir, hexsha)
    _ensure_viewer_excludes(checkout_dir)
    _remap_origin(checkout_dir, cache_dir)
    return checkout_dir / ifc_url.path


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _ensure_checkout(cache_dir: Path, hexsha: str) -> Path:
    """Create or update the working checkout to *hexsha*; return its path.

    Clones from the bare cache if the checkout does not yet exist.  Then
    checks out *hexsha* as a detached HEAD.  If *hexsha* is absent from the
    checkout's local objects, fetches from origin (the bare cache, a local
    path — no network) before retrying.
    """
    checkout_dir = cache_dir / "checkout"
    bare_dir = cache_dir / "repo.git"

    if not checkout_dir.exists():
        _logger.debug("Creating checkout at %s", checkout_dir)
        git.Repo.clone_from(str(bare_dir), str(checkout_dir))

    repo = git.Repo(str(checkout_dir))

    # Fast path: already detached at the right commit.
    try:
        if repo.head.is_detached and repo.head.commit.hexsha == hexsha:
            return checkout_dir
    except Exception:
        pass

    try:
        repo.git.checkout("--detach", hexsha)
    except git.exc.GitCommandError:
        # Commit may not be in the checkout yet (bare cache received it after
        # the checkout was cloned).  Fetch from origin (bare cache, local path
        # — no network).  Use default refspec so git maps heads/* to
        # remotes/origin/*, avoiding the "refusing to fetch into checked-out
        # branch" error when a branch is currently active in the checkout.
        _logger.debug("Commit %s absent from checkout; fetching from bare cache", hexsha[:8])
        repo.git.fetch("origin")
        repo.git.checkout("--detach", hexsha)

    return checkout_dir


def _remap_origin(checkout_dir: Path, cache_dir: Path) -> None:
    """Remap checkout remotes when a local working repo is found.

    If a local repo sharing the same genesis commit is found via
    :func:`ifcurl.discover.find_local_repos`, the checkout's ``origin``
    remote is renamed to ``upstream`` (pointing at the bare cache) and a new
    ``origin`` remote is added pointing at the local repo.  This mirrors the
    standard fork workflow: fetch from ``upstream``, push to ``origin``
    (local repo), then push to the shared remote and open a PR.

    If no local repo is found, ``origin`` remains the bare cache (read-only
    without credentials).  The function is idempotent: it skips remapping if
    the ``upstream`` remote already exists.
    """
    repo = git.Repo(str(checkout_dir))
    if "upstream" in {r.name for r in repo.remotes}:
        return

    genesis_file = cache_dir / "genesis_commit"
    if genesis_file.exists():
        genesis = genesis_file.read_text().strip()
    else:
        bare_repo = git.Repo(str(cache_dir / "repo.git"))
        genesis = _compute_genesis(bare_repo)

    local_repos = find_local_repos(genesis)
    if not local_repos:
        return

    _logger.debug("Remapping checkout origin → %s, upstream → bare cache", local_repos[0])
    repo.git.remote("rename", "origin", "upstream")
    repo.git.remote("add", "origin", str(local_repos[0]))


def _ensure_viewer_excludes(checkout_dir: Path) -> None:
    """Append missing viewer-artefact patterns to ``.git/info/exclude``.

    Uses the local exclude file rather than ``.gitignore`` so the patterns
    are never committed and do not conflict with the project's own ignore
    rules.
    """
    exclude_file = checkout_dir / ".git" / "info" / "exclude"
    exclude_file.parent.mkdir(parents=True, exist_ok=True)

    existing = exclude_file.read_text() if exclude_file.exists() else ""
    missing = [line for line in _VIEWER_EXCLUDES if line not in existing]
    if not missing:
        return

    with open(exclude_file, "a") as f:
        if existing and not existing.endswith("\n"):
            f.write("\n")
        for line in missing:
            f.write(line + "\n")
