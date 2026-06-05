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

"""Discover local git repositories that are clones or forks of a given project.

Repositories are identified by their genesis commit (root commit hexsha),
which is stable across all forks and across HTTPS/SSH URL forms of the same
project.  See :func:`ifcurl.git.get_genesis_commit`.

Search paths are read from ``~/.config/ifcurl/config.toml``:

.. code-block:: toml

    [repo_search]
    paths = ["~/projects/", "~/work/"]

Discovery results are cached per genesis commit in
``~/.cache/ifcurl/genesis_repos/<genesis_commit>.json`` for
``IFCURL_REPO_CACHE_TTL`` seconds (default 3600).
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Iterator
from pathlib import Path

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]

from platformdirs import user_cache_dir, user_config_dir

_logger = logging.getLogger(__name__)

_CACHE_TTL: int = int(os.environ.get("IFCURL_REPO_CACHE_TTL", "3600"))
_DEFAULT_MAX_DEPTH: int = 4


def find_local_repos(genesis_commit: str) -> list[Path]:
    """Return paths of local git repos whose genesis commit matches *genesis_commit*.

    Scans each directory listed under ``[repo_search] paths`` in
    ``~/.config/ifcurl/config.toml``.  Returns an empty list when no search
    paths are configured or no matching repo is found.

    Results are cached on disk for ``IFCURL_REPO_CACHE_TTL`` seconds
    (default 3600) to avoid repeated filesystem scans within a session.

    :param genesis_commit: 40-character root commit hexsha to match against.
    :returns: Absolute paths to matching working-tree git repositories.
    """
    cached = _load_cache(genesis_commit)
    if cached is not None:
        return cached

    search_paths = _get_search_paths()
    matches: list[Path] = []
    for search_path in search_paths:
        for repo_path in _scan_for_git_repos(search_path):
            genesis = _genesis_for_local(repo_path)
            if genesis == genesis_commit:
                matches.append(repo_path)

    _save_cache(genesis_commit, matches)
    return matches


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def _load_config() -> dict:
    """Return the parsed config.toml, or an empty dict if it does not exist."""
    config_file = Path(user_config_dir("ifcurl")) / "config.toml"
    if not config_file.exists():
        return {}
    try:
        with open(config_file, "rb") as f:
            return tomllib.load(f)
    except Exception as exc:
        _logger.warning("Could not read ifcurl config: %s", exc)
        return {}


def _get_search_paths() -> list[Path]:
    """Return expanded search paths from config, skipping non-existent ones."""
    config = _load_config()
    raw = config.get("repo_search", {}).get("paths", [])
    paths = []
    for p in raw:
        expanded = Path(p).expanduser()
        if expanded.is_dir():
            paths.append(expanded)
        else:
            _logger.debug("Search path does not exist, skipping: %s", expanded)
    return paths


# ---------------------------------------------------------------------------
# Filesystem scan
# ---------------------------------------------------------------------------


def _scan_for_git_repos(search_path: Path) -> Iterator[Path]:
    """Yield working-tree git repo directories found under *search_path*.

    Recursion stops at repo boundaries (nested repos are not descended into)
    and at hidden directories (names starting with ``.``).  Depth is capped
    at ``_DEFAULT_MAX_DEPTH`` levels below *search_path*.
    """
    for root, dirs, _ in os.walk(str(search_path)):
        root_path = Path(root)
        depth = len(root_path.relative_to(search_path).parts)

        if (root_path / ".git").is_dir():
            yield root_path
            dirs.clear()  # don't recurse into sub-repos
        elif depth >= _DEFAULT_MAX_DEPTH:
            dirs.clear()
        else:
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))


def _genesis_for_local(repo_path: Path) -> str | None:
    """Return the genesis commit hexsha of the repo at *repo_path*, or None."""
    try:
        import git
        from ifcurl.git import _compute_genesis

        repo = git.Repo(str(repo_path))
        return _compute_genesis(repo)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Disk cache
# ---------------------------------------------------------------------------


def _cache_file(genesis_commit: str) -> Path:
    cache_dir = Path(user_cache_dir("ifcurl")) / "genesis_repos"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"{genesis_commit}.json"


def _load_cache(genesis_commit: str) -> list[Path] | None:
    """Return cached paths if present and unexpired, else None."""
    cf = _cache_file(genesis_commit)
    if not cf.exists():
        return None
    try:
        data = json.loads(cf.read_text())
        if time.time() - data["timestamp"] > _CACHE_TTL:
            return None
        return [Path(p) for p in data["paths"]]
    except Exception:
        return None


def _save_cache(genesis_commit: str, paths: list[Path]) -> None:
    """Write discovery results to disk; failure is silently ignored."""
    try:
        _cache_file(genesis_commit).write_text(
            json.dumps({"timestamp": time.time(), "paths": [str(p) for p in paths]})
        )
    except OSError:
        pass
