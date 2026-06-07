"""Tests for ifcurl.checkout — working-tree checkout management."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from ifcurl.checkout import _ensure_checkout, _ensure_viewer_excludes, _remap_origin, _VIEWER_EXCLUDES, get_checkout
from ifcurl.url import IfcUrl


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_bare_cache(tmp_path: Path, remote_url: str, ifc_bytes: bytes = b"IFC4\n") -> dict:
    """Create a source repo + bare cache clone; return metadata dict."""
    import git as gitpkg

    src = tmp_path / "source"
    src.mkdir()
    (src / "model.ifc").write_bytes(ifc_bytes)
    repo = gitpkg.Repo.init(str(src))
    with repo.config_writer() as cw:
        cw.set_value("user", "name", "Test")
        cw.set_value("user", "email", "t@t.com")
    repo.index.add(["model.ifc"])
    commit = repo.index.commit("init")

    url_hash = hashlib.sha256(remote_url.encode()).hexdigest()[:24]
    cache_dir = tmp_path / url_hash
    bare_dir = cache_dir / "repo.git"
    gitpkg.Repo.clone_from(str(src), str(bare_dir), bare=True)
    (cache_dir / "remote_url").write_text(remote_url)

    return {
        "hexsha": commit.hexsha,
        "bytes": ifc_bytes,
        "cache_dir": cache_dir,
        "bare_dir": bare_dir,
        "src": src,
        "remote_url": remote_url,
    }


def _ifc_url(hexsha: str, remote_url: str, path: str = "model.ifc") -> IfcUrl:
    """Build an IfcUrl for a pre-populated cache (https transport, commit ref)."""
    host_path = remote_url.removeprefix("https://")
    return IfcUrl.parse(f"ifc://{host_path}@{hexsha}?path={path}")


# ---------------------------------------------------------------------------
# _ensure_viewer_excludes
# ---------------------------------------------------------------------------


class TestEnsureViewerExcludes:
    def test_creates_exclude_file(self, tmp_path):
        fake_checkout = tmp_path / "checkout"
        (fake_checkout / ".git" / "info").mkdir(parents=True)
        _ensure_viewer_excludes(fake_checkout)
        assert (fake_checkout / ".git" / "info" / "exclude").exists()

    def test_patterns_present(self, tmp_path):
        fake_checkout = tmp_path / "checkout"
        (fake_checkout / ".git" / "info").mkdir(parents=True)
        _ensure_viewer_excludes(fake_checkout)
        content = (fake_checkout / ".git" / "info" / "exclude").read_text()
        assert "*.ifcview" in content
        assert "*.rdbview" in content

    def test_idempotent(self, tmp_path):
        fake_checkout = tmp_path / "checkout"
        (fake_checkout / ".git" / "info").mkdir(parents=True)
        _ensure_viewer_excludes(fake_checkout)
        _ensure_viewer_excludes(fake_checkout)
        content = (fake_checkout / ".git" / "info" / "exclude").read_text()
        assert content.count("*.ifcview") == 1

    def test_appends_to_existing_file(self, tmp_path):
        fake_checkout = tmp_path / "checkout"
        (fake_checkout / ".git" / "info").mkdir(parents=True)
        exclude = fake_checkout / ".git" / "info" / "exclude"
        exclude.write_text("# existing\n*.log\n")
        _ensure_viewer_excludes(fake_checkout)
        content = exclude.read_text()
        assert "*.log" in content
        assert "*.ifcview" in content

    def test_does_not_duplicate_existing_pattern(self, tmp_path):
        fake_checkout = tmp_path / "checkout"
        (fake_checkout / ".git" / "info").mkdir(parents=True)
        exclude = fake_checkout / ".git" / "info" / "exclude"
        exclude.write_text("*.ifcview\n")
        _ensure_viewer_excludes(fake_checkout)
        assert exclude.read_text().count("*.ifcview") == 1


# ---------------------------------------------------------------------------
# _ensure_checkout
# ---------------------------------------------------------------------------


class TestEnsureCheckout:
    def test_creates_checkout_dir(self, tmp_path):
        meta = _make_bare_cache(tmp_path, "https://example.com/org/repo")
        checkout_dir = _ensure_checkout(meta["cache_dir"], meta["hexsha"])
        assert checkout_dir.is_dir()

    def test_checkout_is_detached_head(self, tmp_path):
        import git as gitpkg

        meta = _make_bare_cache(tmp_path, "https://example.com/org/repo")
        checkout_dir = _ensure_checkout(meta["cache_dir"], meta["hexsha"])
        repo = gitpkg.Repo(str(checkout_dir))
        assert repo.head.is_detached

    def test_checkout_at_correct_commit(self, tmp_path):
        import git as gitpkg

        meta = _make_bare_cache(tmp_path, "https://example.com/org/repo")
        checkout_dir = _ensure_checkout(meta["cache_dir"], meta["hexsha"])
        repo = gitpkg.Repo(str(checkout_dir))
        assert repo.head.commit.hexsha == meta["hexsha"]

    def test_file_present_in_checkout(self, tmp_path):
        meta = _make_bare_cache(tmp_path, "https://example.com/org/repo")
        checkout_dir = _ensure_checkout(meta["cache_dir"], meta["hexsha"])
        assert (checkout_dir / "model.ifc").exists()

    def test_idempotent_same_commit(self, tmp_path):
        meta = _make_bare_cache(tmp_path, "https://example.com/org/repo")
        d1 = _ensure_checkout(meta["cache_dir"], meta["hexsha"])
        d2 = _ensure_checkout(meta["cache_dir"], meta["hexsha"])
        assert d1 == d2

    def test_updates_to_new_commit(self, tmp_path):
        import git as gitpkg

        meta = _make_bare_cache(tmp_path, "https://example.com/org/repo")
        _ensure_checkout(meta["cache_dir"], meta["hexsha"])

        # Add a new commit to the bare cache
        src_repo = gitpkg.Repo(str(meta["src"]))
        (meta["src"] / "model.ifc").write_bytes(b"IFC4\nV2\n")
        src_repo.index.add(["model.ifc"])
        new_commit = src_repo.index.commit("update")
        bare = gitpkg.Repo(str(meta["bare_dir"]))
        bare.git.fetch(str(meta["src"]), "+refs/*:refs/*")

        checkout_dir = _ensure_checkout(meta["cache_dir"], new_commit.hexsha)
        repo = gitpkg.Repo(str(checkout_dir))
        assert repo.head.commit.hexsha == new_commit.hexsha
        assert (checkout_dir / "model.ifc").read_bytes() == b"IFC4\nV2\n"


# ---------------------------------------------------------------------------
# get_checkout
# ---------------------------------------------------------------------------


class TestGetCheckout:
    def _url_and_cache(self, tmp_path, monkeypatch, remote_url="https://example.com/org/repo"):
        monkeypatch.setattr("ifcurl.git.user_cache_dir", lambda *a, **kw: str(tmp_path))
        meta = _make_bare_cache(tmp_path, remote_url)
        url = _ifc_url(meta["hexsha"], remote_url)
        return url, meta

    def test_returns_path(self, tmp_path, monkeypatch):
        url, _ = self._url_and_cache(tmp_path, monkeypatch)
        result = get_checkout(url)
        assert isinstance(result, Path)

    def test_file_exists(self, tmp_path, monkeypatch):
        url, _ = self._url_and_cache(tmp_path, monkeypatch)
        result = get_checkout(url)
        assert result.exists()

    def test_file_content_correct(self, tmp_path, monkeypatch):
        url, meta = self._url_and_cache(tmp_path, monkeypatch)
        result = get_checkout(url)
        assert result.read_bytes() == meta["bytes"]

    def test_path_ends_with_ifc_filename(self, tmp_path, monkeypatch):
        url, _ = self._url_and_cache(tmp_path, monkeypatch)
        result = get_checkout(url)
        assert result.name == "model.ifc"

    def test_checkout_is_detached_head(self, tmp_path, monkeypatch):
        import git as gitpkg

        url, meta = self._url_and_cache(tmp_path, monkeypatch)
        get_checkout(url)
        checkout_dir = meta["cache_dir"] / "checkout"
        repo = gitpkg.Repo(str(checkout_dir))
        assert repo.head.is_detached

    def test_viewer_excludes_written(self, tmp_path, monkeypatch):
        url, meta = self._url_and_cache(tmp_path, monkeypatch)
        get_checkout(url)
        exclude = meta["cache_dir"] / "checkout" / ".git" / "info" / "exclude"
        content = exclude.read_text()
        assert "*.ifcview" in content
        assert "*.rdbview" in content

    def test_idempotent(self, tmp_path, monkeypatch):
        url, _ = self._url_and_cache(tmp_path, monkeypatch)
        p1 = get_checkout(url)
        p2 = get_checkout(url)
        assert p1 == p2

    def test_nested_ifc_path(self, tmp_path, monkeypatch):
        import git as gitpkg

        remote_url = "https://example.com/org/repo2"
        monkeypatch.setattr("ifcurl.git.user_cache_dir", lambda *a, **kw: str(tmp_path))

        src = tmp_path / "source2"
        (src / "models").mkdir(parents=True)
        (src / "models" / "structure.ifc").write_bytes(b"IFC4\n")
        repo = gitpkg.Repo.init(str(src))
        with repo.config_writer() as cw:
            cw.set_value("user", "name", "Test")
            cw.set_value("user", "email", "t@t.com")
        repo.index.add(["models/structure.ifc"])
        commit = repo.index.commit("init")

        url_hash = hashlib.sha256(remote_url.encode()).hexdigest()[:24]
        cache_dir = tmp_path / url_hash
        gitpkg.Repo.clone_from(str(src), str(cache_dir / "repo.git"), bare=True)
        (cache_dir / "remote_url").write_text(remote_url)

        url = IfcUrl.parse(f"ifc://example.com/org/repo2@{commit.hexsha}?path=models/structure.ifc")
        result = get_checkout(url)
        assert result.name == "structure.ifc"
        assert result.exists()

    def test_local_transport_raises(self, tmp_path):
        url = IfcUrl.parse(f"ifc://{tmp_path}@HEAD?path=model.ifc")
        with pytest.raises(ValueError, match="local"):
            get_checkout(url)

    def test_no_path_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr("ifcurl.git.user_cache_dir", lambda *a, **kw: str(tmp_path))
        url = IfcUrl.parse("ifc://example.com/org/repo@abc123def")
        with pytest.raises(ValueError, match="'path'"):
            get_checkout(url)


# ---------------------------------------------------------------------------
# _remap_origin
# ---------------------------------------------------------------------------


class TestRemapOrigin:
    """Tests for origin-remapping when a local working repo is discovered."""

    def _make_checkout_with_cache(self, tmp_path):
        """Return (checkout_dir, cache_dir, genesis_hexsha, bare_dir)."""
        import git as gitpkg

        src = tmp_path / "source"
        src.mkdir()
        (src / "model.ifc").write_bytes(b"IFC4\n")
        repo = gitpkg.Repo.init(str(src))
        with repo.config_writer() as cw:
            cw.set_value("user", "name", "Test")
            cw.set_value("user", "email", "t@t.com")
        repo.index.add(["model.ifc"])
        commit = repo.index.commit("init")

        cache_dir = tmp_path / "cache"
        bare_dir = cache_dir / "repo.git"
        gitpkg.Repo.clone_from(str(src), str(bare_dir), bare=True)

        checkout_dir = cache_dir / "checkout"
        gitpkg.Repo.clone_from(str(bare_dir), str(checkout_dir))
        gitpkg.Repo(str(checkout_dir)).git.checkout("--detach", commit.hexsha)

        genesis = commit.hexsha  # single-commit repo — genesis == head
        (cache_dir / "genesis_commit").write_text(genesis)

        return checkout_dir, cache_dir, genesis, src

    def test_no_local_repo_origin_unchanged(self, tmp_path, monkeypatch):
        import git as gitpkg

        monkeypatch.setattr("ifcurl.checkout.find_local_repos", lambda _: [])
        checkout_dir, cache_dir, _, _ = self._make_checkout_with_cache(tmp_path)
        _remap_origin(checkout_dir, cache_dir)
        repo = gitpkg.Repo(str(checkout_dir))
        assert {r.name for r in repo.remotes} == {"origin"}

    def test_local_repo_found_origin_becomes_local(self, tmp_path, monkeypatch):
        import git as gitpkg

        checkout_dir, cache_dir, genesis, src = self._make_checkout_with_cache(tmp_path)

        local_repo = tmp_path / "local_work"
        gitpkg.Repo.clone_from(str(src), str(local_repo))

        monkeypatch.setattr("ifcurl.checkout.find_local_repos", lambda _: [local_repo])
        _remap_origin(checkout_dir, cache_dir)

        repo = gitpkg.Repo(str(checkout_dir))
        remote_names = {r.name for r in repo.remotes}
        assert "origin" in remote_names
        assert "upstream" in remote_names
        assert repo.remote("origin").url == str(local_repo)

    def test_upstream_is_bare_cache(self, tmp_path, monkeypatch):
        import git as gitpkg

        checkout_dir, cache_dir, genesis, src = self._make_checkout_with_cache(tmp_path)

        local_repo = tmp_path / "local_work"
        gitpkg.Repo.clone_from(str(src), str(local_repo))

        monkeypatch.setattr("ifcurl.checkout.find_local_repos", lambda _: [local_repo])
        _remap_origin(checkout_dir, cache_dir)

        repo = gitpkg.Repo(str(checkout_dir))
        # upstream should point at the bare cache
        assert str(cache_dir / "repo.git") in repo.remote("upstream").url

    def test_idempotent(self, tmp_path, monkeypatch):
        import git as gitpkg

        checkout_dir, cache_dir, genesis, src = self._make_checkout_with_cache(tmp_path)

        local_repo = tmp_path / "local_work"
        gitpkg.Repo.clone_from(str(src), str(local_repo))

        monkeypatch.setattr("ifcurl.checkout.find_local_repos", lambda _: [local_repo])
        _remap_origin(checkout_dir, cache_dir)
        _remap_origin(checkout_dir, cache_dir)  # second call must not error

        repo = gitpkg.Repo(str(checkout_dir))
        remote_names = [r.name for r in repo.remotes]
        assert remote_names.count("origin") == 1
        assert remote_names.count("upstream") == 1

    def test_genesis_from_bare_repo_when_file_missing(self, tmp_path, monkeypatch):
        import git as gitpkg

        checkout_dir, cache_dir, genesis, src = self._make_checkout_with_cache(tmp_path)
        (cache_dir / "genesis_commit").unlink()  # remove cached file

        local_repo = tmp_path / "local_work"
        gitpkg.Repo.clone_from(str(src), str(local_repo))

        monkeypatch.setattr("ifcurl.checkout.find_local_repos", lambda _: [local_repo])
        _remap_origin(checkout_dir, cache_dir)

        repo = gitpkg.Repo(str(checkout_dir))
        assert "upstream" in {r.name for r in repo.remotes}
