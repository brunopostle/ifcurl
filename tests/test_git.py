"""Tests for ifcurl.git — git repository access."""

from __future__ import annotations

import time
from pathlib import Path

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
# Fetch-on-miss retry (PR / non-default-branch commits)
# ---------------------------------------------------------------------------


def _make_remote_with_branch_after_clone(tmp_path: Path, ifc_bytes: bytes, cache_dest: Path) -> dict:
    """Create a remote repo, bare-clone it, then add a branch commit to the remote.

    This simulates the real scenario: initial bare clone only has the default branch,
    then a PR is created (new commit appears in remote) that the clone doesn't know about.
    Returns metadata about both commits and the pre-populated bare cache.
    """
    import git as gitpkg

    remote = tmp_path / "remote"
    remote.mkdir()
    r = gitpkg.Repo.init(str(remote))
    with r.config_writer() as cw:
        cw.set_value("user", "name", "Test")
        cw.set_value("user", "email", "test@example.com")

    (remote / "model.ifc").write_bytes(ifc_bytes)
    r.index.add(["model.ifc"])
    main_commit = r.index.commit("main commit")

    # Clone while only the main commit exists
    cache = gitpkg.Repo.clone_from(str(remote), str(cache_dest), bare=True)

    # Now add a branch commit to the remote (simulates a PR being opened)
    r.create_head("pr-branch").checkout()
    modified = ifc_bytes + b"\n/* branch */"
    (remote / "model.ifc").write_bytes(modified)
    r.index.add(["model.ifc"])
    branch_commit = r.index.commit("pr commit")
    r.heads[r.active_branch.name].checkout()  # back to default

    return {
        "remote_path": str(remote),
        "cache": cache,
        "main_hexsha": main_commit.hexsha,
        "branch_hexsha": branch_commit.hexsha,
        "branch_bytes": modified,
    }


class TestFetchAllRefs:
    def test_fetches_non_default_branch_commit(self, tmp_path, local_ifc_repo):
        from ifcurl.git import _fetch_all_refs

        meta = _make_remote_with_branch_after_clone(
            tmp_path, local_ifc_repo["bytes"], tmp_path / "cache.git"
        )

        with pytest.raises(Exception):  # commit absent before fetch
            meta["cache"].commit(meta["branch_hexsha"])

        _fetch_all_refs(meta["cache"], meta["remote_path"], token=None)

        found = meta["cache"].commit(meta["branch_hexsha"])
        assert found.hexsha == meta["branch_hexsha"]

    def test_main_commit_still_accessible_after_fetch(self, tmp_path, local_ifc_repo):
        from ifcurl.git import _fetch_all_refs

        meta = _make_remote_with_branch_after_clone(
            tmp_path, local_ifc_repo["bytes"], tmp_path / "cache.git"
        )
        _fetch_all_refs(meta["cache"], meta["remote_path"], token=None)

        found = meta["cache"].commit(meta["main_hexsha"])
        assert found.hexsha == meta["main_hexsha"]

    def test_noop_when_fetch_fails(self, tmp_path, local_ifc_repo):
        from ifcurl.git import _fetch_all_refs

        meta = _make_remote_with_branch_after_clone(
            tmp_path, local_ifc_repo["bytes"], tmp_path / "cache.git"
        )
        # Bad remote URL — should silently fail without raising
        _fetch_all_refs(meta["cache"], "file:///nonexistent/repo.git", token=None)


class TestFetchIfcRetry:
    def _patch_url_as_remote(self, url, meta, monkeypatch):
        """Patch a local IfcUrl instance to look like a remote immutable ref."""
        import ifcurl.url as url_mod
        url.transport = "https"
        monkeypatch.setattr(url_mod.IfcUrl, "is_mutable_ref", lambda self: False)
        monkeypatch.setattr(url_mod.IfcUrl, "git_ref", lambda self: meta["branch_hexsha"])
        monkeypatch.setattr(url_mod.IfcUrl, "git_remote_url", lambda self: meta["remote_path"])

    def test_no_retry_for_local_transport(self, local_ifc_repo, monkeypatch):
        """For local transport, BadObject should propagate without triggering retry."""
        import git.exc
        import ifcurl.git as gmod

        fetch_calls = []
        monkeypatch.setattr(gmod, "_fetch_all_refs", lambda *a: fetch_calls.append(1))

        monkeypatch.setattr(
            gmod,
            "_read_commit_blob",
            lambda repo, git_ref, fp: (_ for _ in ()).throw(
                git.exc.BadObject(git_ref.encode(), b"missing")
            ),
        )

        url = IfcUrl.parse(
            f"ifc://{local_ifc_repo['path']}@{local_ifc_repo['hexsha']}?path=model.ifc"
        )
        with pytest.raises((ValueError, git.exc.BadObject)):
            fetch_ifc(url)

        assert fetch_calls == [], "retry must not fire for local transport"

    def test_retry_fires_for_remote_immutable_ref(self, tmp_path, local_ifc_repo, monkeypatch):
        """fetch_ifc calls _fetch_all_refs then retries when commit is absent from clone."""
        import ifcurl.git as gmod

        meta = _make_remote_with_branch_after_clone(
            tmp_path, local_ifc_repo["bytes"], tmp_path / "cache.git"
        )

        real_fetch_all = gmod._fetch_all_refs
        fetch_calls = []

        def recording_fetch_all(repo, remote_url, token):
            fetch_calls.append(remote_url)
            real_fetch_all(repo, meta["remote_path"], token)

        monkeypatch.setattr(gmod, "_get_repo", lambda ifc_url, token=None: (meta["cache"], False))
        monkeypatch.setattr(gmod, "_fetch_all_refs", recording_fetch_all)

        url = IfcUrl.parse(f"ifc:///ignored@{meta['branch_hexsha']}?path=model.ifc")
        self._patch_url_as_remote(url, meta, monkeypatch)

        hexsha, data, _ = fetch_ifc(url)

        assert hexsha == meta["branch_hexsha"]
        assert data == meta["branch_bytes"]
        assert len(fetch_calls) == 1

    def test_bad_object_exception_triggers_retry(self, tmp_path, local_ifc_repo, monkeypatch):
        """BadObject (even if a ValueError subclass) triggers the retry, not just ValueError."""
        import git.exc
        import ifcurl.git as gmod

        meta = _make_remote_with_branch_after_clone(
            tmp_path, local_ifc_repo["bytes"], tmp_path / "cache.git"
        )

        orig_read = gmod._read_commit_blob
        real_fetch_all = gmod._fetch_all_refs
        call_count = [0]

        def flaky_read(repo, git_ref, file_path):
            call_count[0] += 1
            if call_count[0] == 1:
                raise git.exc.BadObject(git_ref.encode(), b"missing")
            return orig_read(repo, git_ref, file_path)

        fetch_calls = []

        def recording_fetch_all(repo, remote_url, token):
            fetch_calls.append(remote_url)
            real_fetch_all(repo, meta["remote_path"], token)

        monkeypatch.setattr(gmod, "_read_commit_blob", flaky_read)
        monkeypatch.setattr(gmod, "_fetch_all_refs", recording_fetch_all)
        monkeypatch.setattr(gmod, "_get_repo", lambda ifc_url, token=None: (meta["cache"], False))

        url = IfcUrl.parse(f"ifc:///ignored@{meta['branch_hexsha']}?path=model.ifc")
        self._patch_url_as_remote(url, meta, monkeypatch)

        hexsha, data, _ = fetch_ifc(url)

        assert hexsha == meta["branch_hexsha"]
        assert call_count[0] == 2    # first raises BadObject, second succeeds
        assert len(fetch_calls) == 1  # _fetch_all_refs called once between the two


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


# ---------------------------------------------------------------------------
# fetch_ifc_path — file materialisation
# ---------------------------------------------------------------------------


from ifcurl.git import fetch_ifc_path, _materialise


class TestMaterialise:
    def test_returns_path(self, tmp_path, local_ifc_repo):
        result = _materialise(tmp_path, "abc123", "model.ifc", b"data")
        assert isinstance(result, Path)

    def test_file_exists(self, tmp_path, local_ifc_repo):
        result = _materialise(tmp_path, "abc123", "model.ifc", b"data")
        assert result.exists()

    def test_file_contains_data(self, tmp_path):
        data = b"IFC4\x00\xFF"
        result = _materialise(tmp_path, "deadbeef", "structure.ifc", data)
        assert result.read_bytes() == data

    def test_hexsha_in_path(self, tmp_path):
        result = _materialise(tmp_path, "cafebabe", "model.ifc", b"x")
        assert "cafebabe" in str(result)

    def test_basename_preserved(self, tmp_path):
        result = _materialise(tmp_path, "abc", "models/sub/structure.ifc", b"x")
        assert result.name == "structure.ifc"

    def test_not_rewritten_if_exists(self, tmp_path):
        result = _materialise(tmp_path, "abc123", "model.ifc", b"original")
        result.write_bytes(b"modified")
        _materialise(tmp_path, "abc123", "model.ifc", b"original")
        assert result.read_bytes() == b"modified"


class TestFetchIfcPath:
    def test_returns_path_and_stale_flag(self, local_ifc_repo):
        url = _local_url(local_ifc_repo["path"])
        path, is_stale = fetch_ifc_path(url)
        assert isinstance(path, Path)
        assert is_stale is False

    def test_file_exists_with_correct_content(self, local_ifc_repo):
        url = _local_url(local_ifc_repo["path"])
        path, _ = fetch_ifc_path(url)
        assert path.exists()
        assert path.read_bytes() == local_ifc_repo["bytes"]

    def test_hexsha_in_path(self, local_ifc_repo):
        url = _local_url(local_ifc_repo["path"])
        path, _ = fetch_ifc_path(url)
        assert local_ifc_repo["hexsha"] in str(path)

    def test_basename_is_ifc_filename(self, local_ifc_repo):
        url = _local_url(local_ifc_repo["path"])
        path, _ = fetch_ifc_path(url)
        assert path.name == "model.ifc"

    def test_stable_across_calls(self, local_ifc_repo):
        url = _local_url(local_ifc_repo["path"])
        path1, _ = fetch_ifc_path(url)
        path2, _ = fetch_ifc_path(url)
        assert path1 == path2

    def test_new_commit_produces_new_path(self, tmp_path):
        import git as gitpkg

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        ifc_bytes = b"IFC4\n"
        (repo_dir / "model.ifc").write_bytes(ifc_bytes)

        repo = gitpkg.Repo.init(str(repo_dir))
        with repo.config_writer() as cw:
            cw.set_value("user", "name", "Test")
            cw.set_value("user", "email", "t@t.com")
        repo.index.add(["model.ifc"])
        repo.index.commit("first")

        url = _local_url(str(repo_dir), ref="HEAD")
        path1, _ = fetch_ifc_path(url)

        (repo_dir / "model.ifc").write_bytes(b"IFC4\nUPDATED\n")
        repo.index.add(["model.ifc"])
        repo.index.commit("second")

        path2, _ = fetch_ifc_path(url)
        assert path1 != path2
        assert path2.read_bytes() == b"IFC4\nUPDATED\n"
