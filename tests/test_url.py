"""Tests for IfcUrl.parse()."""

import pytest

from ifcurl.url import IfcUrl

# ---------------------------------------------------------------------------
# Transport inference
# ---------------------------------------------------------------------------


def test_https_transport():
    url = IfcUrl.parse("ifc://example.com/org/repo@heads/main?path=model.ifc")
    assert url.transport == "https"
    assert url.host == "example.com"
    assert url.user is None


def test_ssh_transport():
    url = IfcUrl.parse("ifc://git@example.com/org/repo@abc123?path=model.ifc")
    assert url.transport == "ssh"
    assert url.host == "example.com"
    assert url.user == "git"


def test_local_transport():
    url = IfcUrl.parse("ifc:///home/alice/project@HEAD?path=model.ifc")
    assert url.transport == "local"
    assert url.host == ""
    assert url.user is None


# ---------------------------------------------------------------------------
# Repo path and ref
# ---------------------------------------------------------------------------


def test_repo_path_and_branch_ref():
    url = IfcUrl.parse("ifc://example.com/org/repo@heads/main?path=model.ifc")
    assert url.repo_path == "org/repo"
    assert url.ref == "heads/main"


def test_tag_ref():
    url = IfcUrl.parse("ifc://example.com/org/repo@tags/v1.2?path=model.ifc")
    assert url.ref == "tags/v1.2"


def test_commit_hash_ref():
    url = IfcUrl.parse("ifc://example.com/org/repo@abc123def?path=model.ifc")
    assert url.ref == "abc123def"


def test_head_ref():
    url = IfcUrl.parse("ifc:///home/alice/project@HEAD?path=model.ifc")
    assert url.ref == "HEAD"
    assert url.repo_path == "/home/alice/project"


# ---------------------------------------------------------------------------
# git_ref() conversion
# ---------------------------------------------------------------------------


def test_git_ref_branch():
    url = IfcUrl.parse("ifc://example.com/org/repo@heads/main?path=model.ifc")
    assert url.git_ref() == "main"


def test_git_ref_tag():
    url = IfcUrl.parse("ifc://example.com/org/repo@tags/v2.0?path=model.ifc")
    assert url.git_ref() == "refs/tags/v2.0"


def test_git_ref_commit():
    url = IfcUrl.parse("ifc://example.com/org/repo@abc123def?path=model.ifc")
    assert url.git_ref() == "abc123def"


def test_git_ref_head():
    url = IfcUrl.parse("ifc:///home/alice/repo@HEAD?path=model.ifc")
    assert url.git_ref() == "HEAD"


# ---------------------------------------------------------------------------
# Query parameters
# ---------------------------------------------------------------------------


def test_path_parameter():
    url = IfcUrl.parse("ifc://example.com/org/repo@heads/main?path=models/building.ifc")
    assert url.path == "models/building.ifc"


def test_no_path():
    url = IfcUrl.parse("ifc://example.com/org/repo@heads/main")
    assert url.path is None


def test_selector():
    url = IfcUrl.parse("ifc://example.com/org/repo@HEAD?path=m.ifc&selector=IfcWall")
    assert url.selector == "IfcWall"


def test_selector_percent_encoded():
    url = IfcUrl.parse("ifc://example.com/org/repo@HEAD?path=m.ifc&selector=IfcWall%2C%2BName%3D%22X%22")
    assert url.selector == 'IfcWall,+Name="X"'


def test_camera_perspective():
    url = IfcUrl.parse(
        "ifc://example.com/org/repo@HEAD?path=m.ifc"
        "&camera=10,20,5,0,-1,0,0,0,1&fov=60"
    )
    assert url.camera == (10.0, 20.0, 5.0, 0.0, -1.0, 0.0, 0.0, 0.0, 1.0)
    assert url.fov == 60.0
    assert url.scale is None


def test_camera_orthographic():
    url = IfcUrl.parse(
        "ifc://example.com/org/repo@HEAD?path=m.ifc"
        "&camera=0,0,8,0,0,-1,0,1,0&scale=50"
    )
    assert url.scale == 50.0
    assert url.fov is None


def test_clip_single():
    url = IfcUrl.parse("ifc://example.com/org/repo@HEAD?path=m.ifc&clip=0,0,3,0,0,-1")
    assert url.clips == [(0.0, 0.0, 3.0, 0.0, 0.0, -1.0)]


def test_clip_multiple():
    url = IfcUrl.parse(
        "ifc://example.com/org/repo@HEAD?path=m.ifc"
        "&clip=0,0,3,0,0,-1&clip=0,0,0,0,0,1"
    )
    assert len(url.clips) == 2
    assert url.clips[0] == (0.0, 0.0, 3.0, 0.0, 0.0, -1.0)
    assert url.clips[1] == (0.0, 0.0, 0.0, 0.0, 0.0, 1.0)


def test_visibility_default():
    url = IfcUrl.parse("ifc://example.com/org/repo@HEAD?path=m.ifc")
    assert url.visibility == "highlight"


def test_visibility_ghost():
    url = IfcUrl.parse("ifc://example.com/org/repo@HEAD?path=m.ifc&visibility=ghost")
    assert url.visibility == "ghost"


def test_visibility_isolate():
    url = IfcUrl.parse("ifc://example.com/org/repo@HEAD?path=m.ifc&visibility=isolate")
    assert url.visibility == "isolate"


# ---------------------------------------------------------------------------
# git_remote_url()
# ---------------------------------------------------------------------------


def test_git_remote_url_https():
    url = IfcUrl.parse("ifc://example.com/org/repo@heads/main?path=m.ifc")
    assert url.git_remote_url() == "https://example.com/org/repo"


def test_git_remote_url_ssh():
    url = IfcUrl.parse("ifc://git@example.com/org/repo@heads/main?path=m.ifc")
    assert url.git_remote_url() == "ssh://git@example.com/org/repo"


def test_git_remote_url_local():
    url = IfcUrl.parse("ifc:///home/alice/project@HEAD?path=m.ifc")
    assert url.git_remote_url() == "/home/alice/project"


# ---------------------------------------------------------------------------
# is_mutable_ref()
# ---------------------------------------------------------------------------


def test_mutable_head():
    assert IfcUrl.parse("ifc://h/o/r@HEAD?path=m.ifc").is_mutable_ref() is True


def test_mutable_branch():
    assert IfcUrl.parse("ifc://h/o/r@heads/main?path=m.ifc").is_mutable_ref() is True


def test_immutable_commit():
    assert IfcUrl.parse("ifc://h/o/r@abc123def?path=m.ifc").is_mutable_ref() is False


def test_immutable_tag():
    assert IfcUrl.parse("ifc://h/o/r@tags/v1.0?path=m.ifc").is_mutable_ref() is False


# ---------------------------------------------------------------------------
# Spec examples
# ---------------------------------------------------------------------------


def test_spec_example_default_view():
    url = IfcUrl.parse("ifc://example.com/org/project@heads/main?path=models/building.ifc")
    assert url.transport == "https"
    assert url.ref == "heads/main"
    assert url.path == "models/building.ifc"
    assert url.selector is None
    assert url.camera is None


def test_spec_example_perspective():
    url = IfcUrl.parse(
        'ifc://git@example.com/org/project@abc123def'
        '?path=models/building.ifc'
        '&selector=IfcWall%2C%2BName%3D%22Core%2BWall%22'
        '&camera=10,20,5,0,-1,0,0,0,1&fov=60&visibility=ghost'
    )
    assert url.transport == "ssh"
    assert url.fov == 60.0
    assert url.visibility == "ghost"


def test_spec_example_orthographic_with_clip():
    url = IfcUrl.parse(
        "ifc://example.com/org/project@tags/v2.0"
        "?path=models/building.ifc"
        "&camera=0,0,8,0,0,-1,0,1,0&scale=50&clip=0,0,5,0,0,-1"
    )
    assert url.scale == 50.0
    assert url.clips == [(0.0, 0.0, 5.0, 0.0, 0.0, -1.0)]


def test_spec_example_local():
    url = IfcUrl.parse("ifc:///home/alice/projects/office@HEAD?path=model.ifc")
    assert url.transport == "local"
    assert url.repo_path == "/home/alice/projects/office"
    assert url.ref == "HEAD"


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


def test_wrong_scheme():
    with pytest.raises(ValueError, match="scheme"):
        IfcUrl.parse("https://example.com/org/repo@HEAD")


def test_missing_ref():
    with pytest.raises(ValueError, match="ref separator"):
        IfcUrl.parse("ifc://example.com/org/repo?path=model.ifc")


def test_camera_wrong_count():
    with pytest.raises(ValueError, match="9 values"):
        IfcUrl.parse("ifc://example.com/o/r@HEAD?path=m.ifc&camera=1,2,3&fov=60")


def test_camera_without_projection():
    with pytest.raises(ValueError, match="fov.*scale"):
        IfcUrl.parse("ifc://example.com/o/r@HEAD?path=m.ifc&camera=0,0,0,0,0,1,0,1,0")


def test_fov_and_scale_exclusive():
    with pytest.raises(ValueError, match="mutually exclusive"):
        IfcUrl.parse(
            "ifc://example.com/o/r@HEAD?path=m.ifc"
            "&camera=0,0,0,0,0,1,0,1,0&fov=60&scale=50"
        )


def test_clip_wrong_count():
    with pytest.raises(ValueError, match="6 values"):
        IfcUrl.parse("ifc://example.com/o/r@HEAD?path=m.ifc&clip=0,0,1")


def test_unknown_visibility():
    with pytest.raises(ValueError, match="visibility"):
        IfcUrl.parse("ifc://example.com/o/r@HEAD?path=m.ifc&visibility=wireframe")
