"""Tests for ifcurl.git — git repository access."""

from __future__ import annotations

import pytest

from ifcurl.git import fetch_ifc, fetch_ifc_bytes
from ifcurl.url import IfcUrl


def _local_url(repo_path: str, ref: str = "HEAD", path: str = "model.ifc") -> IfcUrl:
    """Build an ifc:// URL pointing at a local repo fixture."""
    return IfcUrl.parse(f"ifc://{repo_path}@{ref}?path={path}")


class TestFetchIfc:
    def test_returns_hexsha_and_bytes(self, local_ifc_repo):
        url = _local_url(local_ifc_repo["path"])
        hexsha, data = fetch_ifc(url)
        assert len(hexsha) == 40
        assert all(c in "0123456789abcdef" for c in hexsha)
        assert isinstance(data, bytes)
        assert len(data) > 0

    def test_hexsha_matches_commit(self, local_ifc_repo):
        url = _local_url(local_ifc_repo["path"])
        hexsha, _ = fetch_ifc(url)
        assert hexsha == local_ifc_repo["hexsha"]

    def test_bytes_match_committed_file(self, local_ifc_repo):
        url = _local_url(local_ifc_repo["path"])
        _, data = fetch_ifc(url)
        assert data == local_ifc_repo["bytes"]

    def test_explicit_commit_hash_ref(self, local_ifc_repo):
        url = _local_url(local_ifc_repo["path"], ref=local_ifc_repo["hexsha"])
        hexsha, data = fetch_ifc(url)
        assert hexsha == local_ifc_repo["hexsha"]
        assert data == local_ifc_repo["bytes"]

    def test_tag_ref(self, local_ifc_repo):
        url = _local_url(local_ifc_repo["path"], ref="tags/v1.0")
        hexsha, data = fetch_ifc(url)
        assert hexsha == local_ifc_repo["hexsha"]

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
        _, expected = fetch_ifc(url)
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
