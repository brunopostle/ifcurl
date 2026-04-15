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

"""Fetch IFC file bytes from a git repository described by an IfcUrl.

Remote repositories are cloned as bare repos to a per-URL cache directory
under the OS cache directory.  Mutable refs (HEAD, branches) trigger a fetch
on each call to pick up upstream changes; immutable refs (commit hashes, tags)
reuse the cached clone without fetching.

Authentication
--------------
Pass a *token* string to inject it into HTTPS remote URLs as a credential.
SSH transport uses the platform key store and ignores the token.
See :mod:`ifcurl.auth` for loading tokens from the config file.
"""

from __future__ import annotations

import hashlib

# allows git import even if git executable isn't found during import
import os
from pathlib import Path

from platformdirs import user_cache_dir

os.environ.setdefault("GIT_PYTHON_REFRESH", "quiet")
try:
    import git
    import git.exc

    _HAS_GITPYTHON = True
except ImportError:
    _HAS_GITPYTHON = False

from ifcurl.url import IfcUrl


def fetch_ifc(ifc_url: IfcUrl, token: str | None = None) -> tuple[str, bytes]:
    """Return ``(commit_hexsha, ifc_bytes)`` for the file addressed by *ifc_url*.

    The commit hexsha is the resolved, immutable identifier for the ref —
    useful as a cache key even when the URL uses a mutable ref like a branch.

    :param ifc_url: A parsed :class:`IfcUrl`.
    :param token: Optional bearer token for HTTPS authentication.  Injected
        as ``https://<token>@host/path``.  Ignored for SSH and local repos.
    :raises ImportError: If GitPython is not installed.
    :raises ValueError: If ``ifc_url.path`` is unset, the repo cannot be
        reached, or the file is not found at the specified ref.
    """
    if not _HAS_GITPYTHON:
        raise ImportError("GitPython is not installed.  Install with: pip install gitpython")
    if ifc_url.path is None:
        raise ValueError("URL has no 'path' parameter — cannot fetch IFC file")

    repo = _get_repo(ifc_url, token=token)
    return _read_commit_blob(repo, ifc_url.git_ref(), ifc_url.path)


def fetch_ifc_bytes(ifc_url: IfcUrl, token: str | None = None) -> bytes:
    """Return the raw bytes of the IFC file addressed by *ifc_url*.

    :param ifc_url: A parsed :class:`IfcUrl`.
    :param token: Optional bearer token for HTTPS authentication.
    :raises ImportError: If GitPython is not installed.
    :raises ValueError: If ``ifc_url.path`` is unset, the repo cannot be
        reached, or the file is not found at the specified ref.
    """
    _, data = fetch_ifc(ifc_url, token=token)
    return data


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_repo(ifc_url: IfcUrl, token: str | None = None) -> git.Repo:
    """Open or fetch the repository for *ifc_url*."""
    if ifc_url.transport == "local":
        return _open_local(ifc_url.repo_path)
    return _open_remote(ifc_url.git_remote_url(), ifc_url.is_mutable_ref(), token=token)


def _open_local(repo_path: str) -> git.Repo:
    """Open a local git repository."""
    try:
        return git.Repo(repo_path, search_parent_directories=True)
    except git.exc.InvalidGitRepositoryError as exc:
        raise ValueError(f"Not a git repository: {repo_path!r}") from exc
    except git.exc.NoSuchPathError as exc:
        raise ValueError(f"Repository path does not exist: {repo_path!r}") from exc


def _cache_dir_for(remote_url: str) -> Path:
    """Return (and create) the per-URL cache directory for *remote_url*.

    Uses the OS-appropriate cache location:
      Linux   ~/.cache/ifcurl/<hash>
      macOS   ~/Library/Caches/ifcurl/<hash>
      Windows %LOCALAPPDATA%\\ifcurl\\Cache\\<hash>

    The cache key is derived from the clean URL without any token so the same
    cached clone is shared regardless of which credential was used to fetch it.
    """
    url_hash = hashlib.sha256(remote_url.encode()).hexdigest()[:24]
    cache_dir = Path(user_cache_dir("ifcurl")) / url_hash
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def _auth_url(remote_url: str, token: str | None) -> str:
    """Return *remote_url* with the token injected, or the original URL."""
    if token and (remote_url.startswith("https://") or remote_url.startswith("http://")):
        from ifcurl.auth import inject_token
        return inject_token(remote_url, token)
    return remote_url


def _http_fallback(url: str) -> str | None:
    """Return the http:// equivalent of an https:// URL, or None."""
    if url.startswith("https://"):
        return "http" + url[5:]
    return None


def _clone_bare(auth_url: str, git_dir: Path, remote_url: str) -> git.Repo:
    """Clone *auth_url* as a bare repo to *git_dir*, falling back to http
    when the server does not speak HTTPS (common for local dev instances).
    """
    try:
        return git.Repo.clone_from(auth_url, str(git_dir), bare=True)
    except git.exc.GitCommandError as exc:
        fallback = _http_fallback(auth_url)
        if fallback:
            try:
                return git.Repo.clone_from(fallback, str(git_dir), bare=True)
            except git.exc.GitCommandError:
                pass  # report the original error
        raise ValueError(f"Failed to clone {remote_url!r}: {exc.stderr.strip()}") from exc


def _open_remote(remote_url: str, is_mutable: bool, token: str | None = None) -> git.Repo:
    """Return a GitPython Repo for *remote_url*, cloning it if necessary.

    Bare clones are stored under the OS cache dir keyed on the clean URL.
    For mutable refs (branches, HEAD) the remote is fetched on every call.
    Immutable refs (commit hashes, tags) skip the fetch.

    If *token* is provided it is injected into the URL for clone and fetch
    operations but is never written to the on-disk git config.

    HTTPS URLs automatically fall back to HTTP when the server does not
    speak TLS — this handles local Gitea instances running on HTTP.
    """
    cache_dir = _cache_dir_for(remote_url)
    git_dir = cache_dir / "repo.git"
    auth = _auth_url(remote_url, token)

    if not git_dir.exists():
        repo = _clone_bare(auth, git_dir, remote_url)
    else:
        repo = git.Repo(str(git_dir))
        if is_mutable:
            try:
                if auth != remote_url:
                    # Fetch directly from the authenticated URL so the token
                    # is never stored in the repo config.
                    fetch_url = auth
                    fallback = _http_fallback(auth)
                    try:
                        repo.git.fetch(fetch_url, "+refs/*:refs/*")
                    except git.exc.GitCommandError:
                        if fallback:
                            try:
                                repo.git.fetch(fallback, "+refs/*:refs/*")
                            except git.exc.GitCommandError:
                                pass
                else:
                    repo.git.fetch("origin")
            except git.exc.GitCommandError:
                pass  # offline — use cached data

    return repo


def _read_commit_blob(repo: git.Repo, git_ref: str, file_path: str) -> tuple[str, bytes]:
    """Return ``(commit_hexsha, bytes)`` for *file_path* at *git_ref* in *repo*."""
    try:
        commit = repo.commit(git_ref)
    except (git.exc.BadName, git.exc.BadObject) as exc:
        raise ValueError(f"Ref {git_ref!r} not found in repository") from exc

    try:
        obj = commit.tree
        for part in file_path.strip("/").split("/"):
            obj = obj[part]
    except KeyError as exc:
        raise ValueError(f"File {file_path!r} not found at ref {git_ref!r}") from exc

    if obj.type != "blob":
        raise ValueError(f"{file_path!r} is a directory, not a file")

    return commit.hexsha, obj.data_stream.read()
