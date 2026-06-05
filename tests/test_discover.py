"""Tests for ifcurl.discover — local git repository discovery."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from ifcurl.discover import (
    _cache_file,
    _genesis_for_local,
    _get_search_paths,
    _load_cache,
    _save_cache,
    _scan_for_git_repos,
    find_local_repos,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_repo(path: Path, filename: str = "model.ifc") -> str:
    """Init a git repo at *path* with one commit; return genesis hexsha."""
    import git as gitpkg

    path.mkdir(parents=True, exist_ok=True)
    (path / filename).write_bytes(b"IFC4\n")
    repo = gitpkg.Repo.init(str(path))
    with repo.config_writer() as cw:
        cw.set_value("user", "name", "Test")
        cw.set_value("user", "email", "t@t.com")
    repo.index.add([filename])
    commit = repo.index.commit("init")
    return commit.hexsha


def _write_config(config_dir: Path, paths: list[str]) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.toml").write_text(
        "[repo_search]\npaths = [" + ", ".join(f'"{p}"' for p in paths) + "]\n"
    )


# ---------------------------------------------------------------------------
# _scan_for_git_repos
# ---------------------------------------------------------------------------


class TestScanForGitRepos:
    def test_finds_repo_at_top_level(self, tmp_path):
        _make_repo(tmp_path / "proj")
        found = list(_scan_for_git_repos(tmp_path))
        assert tmp_path / "proj" in found

    def test_finds_nested_repo(self, tmp_path):
        _make_repo(tmp_path / "client" / "proj")
        found = list(_scan_for_git_repos(tmp_path))
        assert tmp_path / "client" / "proj" in found

    def test_does_not_descend_into_sub_repo(self, tmp_path):
        _make_repo(tmp_path / "outer")
        _make_repo(tmp_path / "outer" / "inner")
        found = list(_scan_for_git_repos(tmp_path))
        assert tmp_path / "outer" in found
        assert tmp_path / "outer" / "inner" not in found

    def test_skips_hidden_directories(self, tmp_path):
        _make_repo(tmp_path / ".hidden" / "proj")
        found = list(_scan_for_git_repos(tmp_path))
        assert not any(".hidden" in str(p) for p in found)

    def test_empty_search_path_yields_nothing(self, tmp_path):
        assert list(_scan_for_git_repos(tmp_path)) == []

    def test_nonexistent_path_yields_nothing(self, tmp_path):
        assert list(_scan_for_git_repos(tmp_path / "no_such_dir")) == []

    def test_plain_directory_not_yielded(self, tmp_path):
        (tmp_path / "not_a_repo").mkdir()
        assert list(_scan_for_git_repos(tmp_path)) == []

    def test_multiple_repos_all_found(self, tmp_path):
        _make_repo(tmp_path / "a")
        _make_repo(tmp_path / "b")
        found = list(_scan_for_git_repos(tmp_path))
        assert tmp_path / "a" in found
        assert tmp_path / "b" in found


# ---------------------------------------------------------------------------
# _genesis_for_local
# ---------------------------------------------------------------------------


class TestGenesisForLocal:
    def test_returns_hexsha(self, tmp_path):
        hexsha = _make_repo(tmp_path / "repo")
        result = _genesis_for_local(tmp_path / "repo")
        assert result == hexsha

    def test_returns_none_for_non_repo(self, tmp_path):
        (tmp_path / "not_a_repo").mkdir()
        assert _genesis_for_local(tmp_path / "not_a_repo") is None

    def test_returns_none_for_nonexistent_path(self, tmp_path):
        assert _genesis_for_local(tmp_path / "missing") is None


# ---------------------------------------------------------------------------
# _get_search_paths
# ---------------------------------------------------------------------------


class TestGetSearchPaths:
    def test_returns_configured_paths(self, tmp_path, monkeypatch):
        monkeypatch.setattr("ifcurl.discover.user_config_dir", lambda *a, **kw: str(tmp_path))
        search_dir = tmp_path / "projects"
        search_dir.mkdir()
        _write_config(tmp_path, [str(search_dir)])
        paths = _get_search_paths()
        assert search_dir in paths

    def test_skips_nonexistent_paths(self, tmp_path, monkeypatch):
        monkeypatch.setattr("ifcurl.discover.user_config_dir", lambda *a, **kw: str(tmp_path))
        _write_config(tmp_path, [str(tmp_path / "does_not_exist")])
        assert _get_search_paths() == []

    def test_no_config_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr("ifcurl.discover.user_config_dir", lambda *a, **kw: str(tmp_path))
        assert _get_search_paths() == []


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------


class TestCache:
    def test_save_and_load(self, tmp_path, monkeypatch):
        monkeypatch.setattr("ifcurl.discover.user_cache_dir", lambda *a, **kw: str(tmp_path))
        paths = [Path("/home/user/proj"), Path("/home/user/other")]
        _save_cache("abc123", paths)
        result = _load_cache("abc123")
        assert result == paths

    def test_missing_cache_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr("ifcurl.discover.user_cache_dir", lambda *a, **kw: str(tmp_path))
        assert _load_cache("deadbeef" * 5) is None

    def test_expired_cache_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr("ifcurl.discover.user_cache_dir", lambda *a, **kw: str(tmp_path))
        cf = _cache_file("abc123")
        cf.parent.mkdir(parents=True, exist_ok=True)
        cf.write_text(json.dumps({"timestamp": time.time() - 7200, "paths": []}))
        assert _load_cache("abc123") is None

    def test_fresh_cache_returned(self, tmp_path, monkeypatch):
        monkeypatch.setattr("ifcurl.discover.user_cache_dir", lambda *a, **kw: str(tmp_path))
        _save_cache("abc123", [Path("/some/repo")])
        assert _load_cache("abc123") == [Path("/some/repo")]

    def test_corrupt_cache_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr("ifcurl.discover.user_cache_dir", lambda *a, **kw: str(tmp_path))
        cf = _cache_file("abc123")
        cf.parent.mkdir(parents=True, exist_ok=True)
        cf.write_text("not json {{{")
        assert _load_cache("abc123") is None


# ---------------------------------------------------------------------------
# find_local_repos
# ---------------------------------------------------------------------------


class TestFindLocalRepos:
    def _setup(self, tmp_path, monkeypatch):
        monkeypatch.setattr("ifcurl.discover.user_config_dir", lambda *a, **kw: str(tmp_path / "config"))
        monkeypatch.setattr("ifcurl.discover.user_cache_dir", lambda *a, **kw: str(tmp_path / "cache"))
        search_dir = tmp_path / "projects"
        search_dir.mkdir()
        _write_config(tmp_path / "config", [str(search_dir)])
        return search_dir

    def test_finds_matching_repo(self, tmp_path, monkeypatch):
        search_dir = self._setup(tmp_path, monkeypatch)
        genesis = _make_repo(search_dir / "building")
        result = find_local_repos(genesis)
        assert search_dir / "building" in result

    def test_ignores_unrelated_repo(self, tmp_path, monkeypatch):
        import git as gitpkg

        search_dir = self._setup(tmp_path, monkeypatch)
        # Give each repo distinct content so their genesis hashes differ
        _make_repo(search_dir / "building")
        other = search_dir / "other"
        other.mkdir()
        (other / "model.ifc").write_bytes(b"IFC4\nDISTINCT CONTENT\n")
        repo = gitpkg.Repo.init(str(other))
        with repo.config_writer() as cw:
            cw.set_value("user", "name", "Test")
            cw.set_value("user", "email", "t@t.com")
        repo.index.add(["model.ifc"])
        unrelated_genesis = repo.index.commit("init other").hexsha

        result = find_local_repos(unrelated_genesis)
        assert search_dir / "building" not in result
        assert search_dir / "other" in result

    def test_finds_fork_by_genesis(self, tmp_path, monkeypatch):
        import git as gitpkg

        search_dir = self._setup(tmp_path, monkeypatch)
        genesis = _make_repo(tmp_path / "upstream")
        gitpkg.Repo.clone_from(str(tmp_path / "upstream"), str(search_dir / "myfork"))
        result = find_local_repos(genesis)
        assert search_dir / "myfork" in result

    def test_no_search_paths_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr("ifcurl.discover.user_config_dir", lambda *a, **kw: str(tmp_path / "config"))
        monkeypatch.setattr("ifcurl.discover.user_cache_dir", lambda *a, **kw: str(tmp_path / "cache"))
        assert find_local_repos("a" * 40) == []

    def test_result_is_cached(self, tmp_path, monkeypatch):
        search_dir = self._setup(tmp_path, monkeypatch)
        genesis = _make_repo(search_dir / "proj")

        find_local_repos(genesis)  # populate cache

        # Remove the actual repo — second call must still return a result from cache
        import shutil
        shutil.rmtree(str(search_dir / "proj"))

        result = find_local_repos(genesis)
        assert search_dir / "proj" in result

    def test_stale_cache_rescans(self, tmp_path, monkeypatch):
        monkeypatch.setattr("ifcurl.discover.user_config_dir", lambda *a, **kw: str(tmp_path / "config"))
        monkeypatch.setattr("ifcurl.discover.user_cache_dir", lambda *a, **kw: str(tmp_path / "cache"))
        monkeypatch.setattr("ifcurl.discover._CACHE_TTL", 0)

        search_dir = tmp_path / "projects"
        search_dir.mkdir()
        _write_config(tmp_path / "config", [str(search_dir)])
        genesis = _make_repo(search_dir / "proj")

        # Write a stale cache that claims no repos
        cf = _cache_file(genesis)
        cf.parent.mkdir(parents=True, exist_ok=True)
        cf.write_text(json.dumps({"timestamp": 0, "paths": []}))

        result = find_local_repos(genesis)
        assert search_dir / "proj" in result
