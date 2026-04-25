"""Tests for ifcurl.git — git repository access."""

from __future__ import annotations

import time

import pytest

from ifcurl.git import fetch_ifc, fetch_ifc_bytes
from ifcurl.url import IfcUrl


def _local_url(repo_path: str, ref: str = "HEAD", path: str = "model.ifc") -> IfcUrl:
    """Build an ifc:// URL pointing at a local repo fixture."""
    return IfcUrl.parse(f"ifc://{repo_path}@{ref}?path={path}")


class TestFetchIfc:
    def test_returns_hexsha_and_bytes(self, local_ifc_repo):
        url = _local_url(local_ifc_repo["path"])
        hexsha, data, _ = fetch_ifc(url)
        assert len(hexsha) == 40
        assert all(c in "0123456789abcdef" for c in hexsha)
        assert isinstance(data, bytes)
        assert len(data) > 0

    def test_local_repo_never_stale(self, local_ifc_repo):
        url = _local_url(local_ifc_repo["path"])
        _, _, is_stale = fetch_ifc(url)
        assert is_stale is False

    def test_hexsha_matches_commit(self, local_ifc_repo):
        url = _local_url(local_ifc_repo["path"])
        hexsha, _, _ = fetch_ifc(url)
        assert hexsha == local_ifc_repo["hexsha"]

    def test_bytes_match_committed_file(self, local_ifc_repo):
        url = _local_url(local_ifc_repo["path"])
        _, data, _ = fetch_ifc(url)
        assert data == local_ifc_repo["bytes"]

    def test_explicit_commit_hash_ref(self, local_ifc_repo):
        url = _local_url(local_ifc_repo["path"], ref=local_ifc_repo["hexsha"])
        hexsha, data, _ = fetch_ifc(url)
        assert hexsha == local_ifc_repo["hexsha"]
        assert data == local_ifc_repo["bytes"]

    def test_tag_ref(self, local_ifc_repo):
        url = _local_url(local_ifc_repo["path"], ref="tags/v1.0")
        hexsha, data, _ = fetch_ifc(url)
        assert hexsha == local_ifc_repo["hexsha"]

    def test_branch_ref(self, local_ifc_repo):
        import git as gitpkg

        branch = gitpkg.Repo(local_ifc_repo["path"]).active_branch.name
        url = _local_url(local_ifc_repo["path"], ref=f"heads/{branch}")
        hexsha, data, _ = fetch_ifc(url)
        assert hexsha == local_ifc_repo["hexsha"]
        assert data == local_ifc_repo["bytes"]

    def test_bad_ref_raises(self, local_ifc_repo):
        url = _local_url(local_ifc_repo["path"], ref="heads/nonexistent")
        with pytest.raises(ValueError, match="not found"):
            fetch_ifc(url)

    def test_bad_path_raises(self, local_ifc_repo):
        url = _local_url(local_ifc_repo["path"], path="missing.ifc")
        with pytest.raises(ValueError, match="not found"):
            fetch_ifc(url)

    def test_directory_path_raises(self, local_ifc_repo):
        # Committing a subdir would be complex; test that asking for a
        # non-blob raises — here we ask for the root tree entry.
        url = _local_url(local_ifc_repo["path"], path=".")
        with pytest.raises((ValueError, KeyError)):
            fetch_ifc(url)

    def test_no_path_raises(self, local_ifc_repo):
        url = IfcUrl.parse(f"ifc://{local_ifc_repo['path']}@HEAD")
        with pytest.raises(ValueError, match="'path' parameter"):
            fetch_ifc(url)


class TestFetchIfcBytes:
    def test_returns_bytes(self, local_ifc_repo):
        url = _local_url(local_ifc_repo["path"])
        data = fetch_ifc_bytes(url)
        assert data == local_ifc_repo["bytes"]

    def test_backwards_compat_with_fetch_ifc(self, local_ifc_repo):
        url = _local_url(local_ifc_repo["path"])
        _, expected, _ = fetch_ifc(url)
        assert fetch_ifc_bytes(url) == expected


class TestInvalidRepo:
    def test_nonexistent_path_raises(self, tmp_path):
        url = IfcUrl.parse(f"ifc://{tmp_path / 'no_such_repo'}@HEAD?path=m.ifc")
        with pytest.raises(ValueError):
            fetch_ifc(url)

    def test_non_git_directory_raises(self, tmp_path):
        (tmp_path / "model.ifc").write_text("not a repo")
        url = IfcUrl.parse(f"ifc://{tmp_path}@HEAD?path=model.ifc")
        with pytest.raises(ValueError):
            fetch_ifc(url)


# ---------------------------------------------------------------------------
# Cache management helpers
# ---------------------------------------------------------------------------


from ifcurl.git import _dir_size, _evict_if_needed, _repo_cache_entries, _touch_cache


def _make_fake_repo_cache(base: Path, url: str, size_bytes: int = 1024) -> Path:
    """Create a fake cached-repo directory structure under *base*."""
    import hashlib

    key = hashlib.sha256(url.encode()).hexdigest()[:24]
    entry = base / key
    git_dir = entry / "repo.git"
    git_dir.mkdir(parents=True)
    (git_dir / "dummy").write_bytes(b"\x00" * size_bytes)
    (entry / "remote_url").write_text(url)
    return entry


class TestCacheHelpers:
    def test_dir_size_counts_nested_files(self, tmp_path):
        (tmp_path / "a").write_bytes(b"x" * 100)
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "b").write_bytes(b"y" * 200)
        assert _dir_size(tmp_path) == 300

    def test_touch_cache_updates_mtime(self, tmp_path):
        import time

        old_mtime = tmp_path.stat().st_mtime
        time.sleep(0.05)
        _touch_cache(tmp_path)
        assert tmp_path.stat().st_mtime > old_mtime

    def test_repo_cache_entries_lists_repos(self, tmp_path, monkeypatch):
        monkeypatch.setattr("ifcurl.git.user_cache_dir", lambda *a, **kw: str(tmp_path))
        _make_fake_repo_cache(tmp_path, "https://example.com/org/repo")
        _make_fake_repo_cache(tmp_path, "https://github.com/org/other")
        entries = _repo_cache_entries()
        urls = {e[3] for e in entries}
        assert "https://example.com/org/repo" in urls
        assert "https://github.com/org/other" in urls

    def test_repo_cache_entries_ignores_non_repo_dirs(self, tmp_path, monkeypatch):
        monkeypatch.setattr("ifcurl.git.user_cache_dir", lambda *a, **kw: str(tmp_path))
        (tmp_path / "not_a_repo").mkdir()  # no repo.git subdir
        entries = _repo_cache_entries()
        assert len(entries) == 0

    def test_evict_removes_oldest_over_limit(self, tmp_path, monkeypatch):
        monkeypatch.setattr("ifcurl.git.user_cache_dir", lambda *a, **kw: str(tmp_path))
        monkeypatch.setenv("IFCURL_CACHE_MAX_GB", "0.000001")  # ~1 KB limit
        old = _make_fake_repo_cache(
            tmp_path, "https://old.example.com/repo", size_bytes=512
        )
        time.sleep(0.05)
        new = _make_fake_repo_cache(
            tmp_path, "https://new.example.com/repo", size_bytes=512
        )
        _evict_if_needed()
        assert not old.exists()
        assert new.exists()

    def test_evict_no_limit_does_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setattr("ifcurl.git.user_cache_dir", lambda *a, **kw: str(tmp_path))
        monkeypatch.delenv("IFCURL_CACHE_MAX_GB", raising=False)
        entry = _make_fake_repo_cache(tmp_path, "https://example.com/repo")
        _evict_if_needed()
        assert entry.exists()


# ---------------------------------------------------------------------------
# Cache CLI (_cmd_cache)
# ---------------------------------------------------------------------------


import argparse
import types


def _cache_args(**kwargs) -> argparse.Namespace:
    defaults = {"cache_cmd": None, "max_gb": None}
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


class TestCacheCli:
    def _patch(self, monkeypatch, tmp_path):
        monkeypatch.setattr("ifcurl.git.user_cache_dir", lambda *a, **kw: str(tmp_path))
        monkeypatch.setattr("ifcurl.__main__.user_cache_dir", lambda *a, **kw: str(tmp_path), raising=False)

    def test_list_empty(self, tmp_path, monkeypatch, capsys):
        self._patch(monkeypatch, tmp_path)
        from ifcurl.__main__ import _cmd_cache
        _cmd_cache(_cache_args(cache_cmd="list"))
        out = capsys.readouterr().out
        assert "No cached repositories" in out

    def test_list_default_subcommand(self, tmp_path, monkeypatch, capsys):
        self._patch(monkeypatch, tmp_path)
        _make_fake_repo_cache(tmp_path, "https://example.com/org/repo", size_bytes=2048)
        from ifcurl.__main__ import _cmd_cache
        _cmd_cache(_cache_args(cache_cmd=None))
        out = capsys.readouterr().out
        assert "https://example.com/org/repo" in out
        assert "Total:" in out

    def test_list_shows_entries_and_total(self, tmp_path, monkeypatch, capsys):
        self._patch(monkeypatch, tmp_path)
        monkeypatch.delenv("IFCURL_CACHE_MAX_GB", raising=False)
        _make_fake_repo_cache(tmp_path, "https://example.com/a", size_bytes=1024 * 1024)
        _make_fake_repo_cache(tmp_path, "https://example.com/b", size_bytes=1024 * 1024)
        from ifcurl.__main__ import _cmd_cache
        _cmd_cache(_cache_args(cache_cmd="list"))
        out = capsys.readouterr().out
        assert "https://example.com/a" in out
        assert "https://example.com/b" in out
        assert "2 repos" in out

    def test_list_shows_limit_when_env_set(self, tmp_path, monkeypatch, capsys):
        self._patch(monkeypatch, tmp_path)
        monkeypatch.setenv("IFCURL_CACHE_MAX_GB", "5.0")
        _make_fake_repo_cache(tmp_path, "https://example.com/a", size_bytes=1024)
        from ifcurl.__main__ import _cmd_cache
        _cmd_cache(_cache_args(cache_cmd="list"))
        out = capsys.readouterr().out
        assert "limit:" in out
        assert "5.0 GB" in out

    def test_clear_removes_all(self, tmp_path, monkeypatch, capsys):
        self._patch(monkeypatch, tmp_path)
        e1 = _make_fake_repo_cache(tmp_path, "https://example.com/a")
        e2 = _make_fake_repo_cache(tmp_path, "https://example.com/b")
        from ifcurl.__main__ import _cmd_cache
        _cmd_cache(_cache_args(cache_cmd="clear"))
        assert not e1.exists()
        assert not e2.exists()
        out = capsys.readouterr().out
        assert "Cleared" in out
        assert "2 repos" in out

    def test_clear_empty(self, tmp_path, monkeypatch, capsys):
        self._patch(monkeypatch, tmp_path)
        from ifcurl.__main__ import _cmd_cache
        _cmd_cache(_cache_args(cache_cmd="clear"))
        out = capsys.readouterr().out
        assert "Nothing to clear" in out

    def test_prune_with_max_gb_arg(self, tmp_path, monkeypatch, capsys):
        self._patch(monkeypatch, tmp_path)
        monkeypatch.delenv("IFCURL_CACHE_MAX_GB", raising=False)
        # Two 1 MB entries; limit to 0.5 MB so both are evicted
        e1 = _make_fake_repo_cache(tmp_path, "https://example.com/old", size_bytes=1024 * 1024)
        time.sleep(0.05)
        e2 = _make_fake_repo_cache(tmp_path, "https://example.com/new", size_bytes=1024 * 1024)
        from ifcurl.__main__ import _cmd_cache
        _cmd_cache(_cache_args(cache_cmd="prune", max_gb=0.0000005))
        out = capsys.readouterr().out
        assert "Freed" in out
        assert "remaining" in out

    def test_prune_no_max_gb_and_no_env_exits(self, tmp_path, monkeypatch, capsys):
        self._patch(monkeypatch, tmp_path)
        monkeypatch.delenv("IFCURL_CACHE_MAX_GB", raising=False)
        from ifcurl.__main__ import _cmd_cache
        with pytest.raises(SystemExit):
            _cmd_cache(_cache_args(cache_cmd="prune", max_gb=None))
        err = capsys.readouterr().err
        assert "required" in err

    def test_prune_uses_env_when_no_arg(self, tmp_path, monkeypatch, capsys):
        self._patch(monkeypatch, tmp_path)
        monkeypatch.setenv("IFCURL_CACHE_MAX_GB", "0.0000005")
        _make_fake_repo_cache(tmp_path, "https://example.com/repo", size_bytes=1024 * 1024)
        from ifcurl.__main__ import _cmd_cache
        _cmd_cache(_cache_args(cache_cmd="prune", max_gb=None))
        out = capsys.readouterr().out
        assert "Freed" in out
