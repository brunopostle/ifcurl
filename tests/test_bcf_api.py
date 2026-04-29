"""Tests for ifcurl.bcf_api — BCF 3.0 REST API routes."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from ifcurl.bcf_api import (
    _comment_to_bcf,
    _comment_to_viewpoint,
    _first_ifc_url,
    _id_from_guid,
    _issue_to_topic,
    make_comment_guid,
    make_topic_guid,
    make_viewpoint_guid,
)
from ifcurl.service import _rate_hits, app

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_rate_limit():
    _rate_hits.clear()
    yield
    _rate_hits.clear()

OWNER, REPO = "alice", "myproject"
AUTH = "Bearer test-token"

# Minimal Forgejo API fixture data
ISSUE_1 = {
    "number": 1,
    "title": "Crack in wall",
    "body": "ifc://example.com/org/repo@heads/main?path=model.ifc&camera=1.0,2.0,3.0,0.0,0.0,-1.0,0.0,1.0,0.0&fov=60.0",
    "state": "open",
    "created_at": "2026-01-01T10:00:00Z",
    "updated_at": "2026-01-02T10:00:00Z",
    "user": {"login": "alice"},
    "assignees": [],
    "labels": [],
}
ISSUE_2 = {
    "number": 2,
    "title": "Missing door",
    "body": "Please check",
    "state": "closed",
    "created_at": "2026-01-03T10:00:00Z",
    "updated_at": "2026-01-03T10:00:00Z",
    "user": {"login": "bob"},
    "assignees": [],
    "labels": [],
}
COMMENT_WITH_VP = {
    "id": 100,
    "body": "ifc://example.com/org/repo@heads/main?path=model.ifc&camera=1.0,2.0,3.0,0.0,0.0,-1.0,0.0,1.0,0.0&fov=60.0",
    "created_at": "2026-01-01T11:00:00Z",
    "user": {"login": "alice"},
}
COMMENT_NO_VP = {
    "id": 101,
    "body": "Looks like a structural issue.",
    "created_at": "2026-01-01T12:00:00Z",
    "user": {"login": "bob"},
}
REPO_INFO = {
    "full_name": "alice/myproject",
    "name": "myproject",
}
REPO_LIST = {"data": [REPO_INFO]}


# ---------------------------------------------------------------------------
# GUID functions
# ---------------------------------------------------------------------------

class TestGuidFunctions:
    def test_topic_guid_is_stable(self):
        assert make_topic_guid(OWNER, REPO, 1) == make_topic_guid(OWNER, REPO, 1)

    def test_topic_guid_differs_by_number(self):
        assert make_topic_guid(OWNER, REPO, 1) != make_topic_guid(OWNER, REPO, 2)

    def test_topic_guid_differs_by_repo(self):
        assert make_topic_guid("alice", "a", 1) != make_topic_guid("alice", "b", 1)

    def test_id_from_topic_guid_roundtrip(self):
        for n in (1, 42, 1000, 99999):
            assert _id_from_guid(make_topic_guid(OWNER, REPO, n)) == n

    def test_id_from_comment_guid_roundtrip(self):
        for cid in (100, 99999):
            assert _id_from_guid(make_comment_guid(OWNER, REPO, cid)) == cid

    def test_id_from_viewpoint_guid_roundtrip(self):
        for cid in (100, 99999):
            assert _id_from_guid(make_viewpoint_guid(OWNER, REPO, cid)) == cid

    def test_guids_are_uuid_shaped(self):
        g = make_topic_guid(OWNER, REPO, 1)
        parts = g.split("-")
        assert len(parts) == 5
        assert len(parts[0]) == 8
        assert len(parts[4]) == 12


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_first_ifc_url_found(self):
        assert _first_ifc_url("see ifc://example.com/o/r@HEAD?path=m.ifc here") == "ifc://example.com/o/r@HEAD?path=m.ifc"

    def test_first_ifc_url_none(self):
        assert _first_ifc_url("no url here") is None

    def test_first_ifc_url_empty(self):
        assert _first_ifc_url("") is None

    def test_issue_to_topic_fields(self):
        t = _issue_to_topic(ISSUE_1, OWNER, REPO)
        assert t["guid"] == make_topic_guid(OWNER, REPO, 1)
        assert t["title"] == "Crack in wall"
        assert t["topic_status"] == "Open"
        assert t["index"] == 1

    def test_issue_to_topic_closed(self):
        t = _issue_to_topic(ISSUE_2, OWNER, REPO)
        assert t["topic_status"] == "Closed"

    def test_comment_to_bcf_with_viewpoint(self):
        c = _comment_to_bcf(COMMENT_WITH_VP, OWNER, REPO)
        assert c["guid"] == make_comment_guid(OWNER, REPO, 100)
        assert c["viewpoint_guid"] == make_viewpoint_guid(OWNER, REPO, 100)

    def test_comment_to_bcf_without_viewpoint(self):
        c = _comment_to_bcf(COMMENT_NO_VP, OWNER, REPO)
        assert c["viewpoint_guid"] is None

    def test_comment_to_viewpoint_with_ifc_url(self):
        vp = _comment_to_viewpoint(COMMENT_WITH_VP, OWNER, REPO)
        assert vp is not None
        assert vp["guid"] == make_viewpoint_guid(OWNER, REPO, 100)
        assert "perspective_camera" in vp

    def test_comment_to_viewpoint_without_ifc_url(self):
        assert _comment_to_viewpoint(COMMENT_NO_VP, OWNER, REPO) is None


# ---------------------------------------------------------------------------
# BCF API routes (Forgejo calls mocked)
# ---------------------------------------------------------------------------

def _mock_fget(responses: dict):
    """Return a side_effect function that maps path → response."""
    def side_effect(path, auth, params=None):
        for key, val in responses.items():
            if path.endswith(key) or key in path:
                return val
        raise Exception(f"Unexpected fget: {path}")
    return side_effect


class TestProjectRoutes:
    def test_list_projects(self):
        with patch("ifcurl.bcf_api._fget", return_value=REPO_LIST):
            r = client.get("/bcf/3.0/projects", headers={"Authorization": AUTH})
        assert r.status_code == 200
        data = r.json()
        assert any(p["project_id"] == "alice/myproject" for p in data)

    def test_get_project(self):
        with patch("ifcurl.bcf_api._fget", return_value=REPO_INFO):
            r = client.get(f"/bcf/3.0/projects/{OWNER}/{REPO}", headers={"Authorization": AUTH})
        assert r.status_code == 200
        assert r.json()["project_id"] == "alice/myproject"

    def test_get_extensions(self):
        r = client.get(f"/bcf/3.0/projects/{OWNER}/{REPO}/extensions")
        assert r.status_code == 200
        data = r.json()
        assert "topic_type" in data
        assert "topic_status" in data


class TestTopicRoutes:
    def test_list_topics(self):
        with patch("ifcurl.bcf_api._fget", return_value=[ISSUE_1, ISSUE_2]):
            r = client.get(f"/bcf/3.0/projects/{OWNER}/{REPO}/topics", headers={"Authorization": AUTH})
        assert r.status_code == 200
        topics = r.json()
        assert len(topics) == 2
        assert topics[0]["title"] == "Crack in wall"

    def test_get_topic(self):
        tguid = make_topic_guid(OWNER, REPO, 1)
        with patch("ifcurl.bcf_api._fget", return_value=ISSUE_1):
            r = client.get(f"/bcf/3.0/projects/{OWNER}/{REPO}/topics/{tguid}", headers={"Authorization": AUTH})
        assert r.status_code == 200
        assert r.json()["guid"] == tguid

    def test_get_topic_wrong_guid_returns_404(self):
        # GUID that encodes issue number 999 but issue 1 is returned
        tguid = make_topic_guid(OWNER, REPO, 999)
        with patch("ifcurl.bcf_api._fget", return_value=ISSUE_1):
            r = client.get(f"/bcf/3.0/projects/{OWNER}/{REPO}/topics/{tguid}", headers={"Authorization": AUTH})
        assert r.status_code == 404

    def test_create_topic(self):
        with patch("ifcurl.bcf_api._fpost", return_value=ISSUE_1):
            r = client.post(
                f"/bcf/3.0/projects/{OWNER}/{REPO}/topics",
                json={"title": "Crack in wall", "description": ""},
                headers={"Authorization": AUTH},
            )
        assert r.status_code == 201
        assert r.json()["title"] == "Crack in wall"

    def test_update_topic_status(self):
        tguid = make_topic_guid(OWNER, REPO, 1)
        closed_issue = {**ISSUE_1, "state": "closed"}
        with (
            patch("ifcurl.bcf_api._fget", return_value=ISSUE_1),
            patch("ifcurl.bcf_api._fpatch", return_value=closed_issue),
        ):
            r = client.put(
                f"/bcf/3.0/projects/{OWNER}/{REPO}/topics/{tguid}",
                json={"topic_status": "Closed"},
                headers={"Authorization": AUTH},
            )
        assert r.status_code == 200
        assert r.json()["topic_status"] == "Closed"


class TestCommentRoutes:
    def test_list_comments(self):
        tguid = make_topic_guid(OWNER, REPO, 1)
        with patch("ifcurl.bcf_api._fget", side_effect=[ISSUE_1, [COMMENT_WITH_VP, COMMENT_NO_VP]]):
            r = client.get(f"/bcf/3.0/projects/{OWNER}/{REPO}/topics/{tguid}/comments", headers={"Authorization": AUTH})
        assert r.status_code == 200
        comments = r.json()
        assert len(comments) == 2
        assert comments[0]["viewpoint_guid"] is not None
        assert comments[1]["viewpoint_guid"] is None

    def test_create_comment(self):
        tguid = make_topic_guid(OWNER, REPO, 1)
        with (
            patch("ifcurl.bcf_api._fget", return_value=ISSUE_1),
            patch("ifcurl.bcf_api._fpost", return_value=COMMENT_NO_VP),
        ):
            r = client.post(
                f"/bcf/3.0/projects/{OWNER}/{REPO}/topics/{tguid}/comments",
                json={"comment": "Looks like a structural issue."},
                headers={"Authorization": AUTH},
            )
        assert r.status_code == 201
        assert r.json()["author"] == "bob"


class TestViewpointRoutes:
    def test_list_viewpoints(self):
        tguid = make_topic_guid(OWNER, REPO, 1)
        with patch("ifcurl.bcf_api._fget", side_effect=[ISSUE_1, [COMMENT_WITH_VP, COMMENT_NO_VP]]):
            r = client.get(f"/bcf/3.0/projects/{OWNER}/{REPO}/topics/{tguid}/viewpoints", headers={"Authorization": AUTH})
        assert r.status_code == 200
        viewpoints = r.json()
        assert len(viewpoints) == 1
        assert "perspective_camera" in viewpoints[0]

    def test_get_viewpoint(self):
        tguid = make_topic_guid(OWNER, REPO, 1)
        vpguid = make_viewpoint_guid(OWNER, REPO, 100)
        with patch("ifcurl.bcf_api._fget", side_effect=[ISSUE_1, COMMENT_WITH_VP]):
            r = client.get(f"/bcf/3.0/projects/{OWNER}/{REPO}/topics/{tguid}/viewpoints/{vpguid}", headers={"Authorization": AUTH})
        assert r.status_code == 200
        vp = r.json()
        assert vp["guid"] == vpguid
        assert "perspective_camera" in vp

    def test_create_viewpoint_posts_ifc_url(self):
        tguid = make_topic_guid(OWNER, REPO, 1)
        posted_body = {}

        def mock_fpost(path, auth, body):
            posted_body.update(body)
            return {**COMMENT_WITH_VP, "body": body["body"]}

        vp_body = {
            "perspective_camera": {
                "camera_view_point": {"x": 5.0, "y": 5.0, "z": 5.0},
                "camera_direction": {"x": 0.0, "y": 0.0, "z": -1.0},
                "camera_up_vector": {"x": 0.0, "y": 1.0, "z": 0.0},
                "field_of_view": 45.0,
            }
        }
        with (
            patch("ifcurl.bcf_api._fget", return_value=ISSUE_1),
            patch("ifcurl.bcf_api._fpost", side_effect=mock_fpost),
        ):
            r = client.post(
                f"/bcf/3.0/projects/{OWNER}/{REPO}/topics/{tguid}/viewpoints",
                json=vp_body,
                headers={"Authorization": AUTH},
            )
        assert r.status_code == 201
        assert posted_body.get("body", "").startswith("ifc://")

    def test_create_viewpoint_no_ifc_url_in_issue_returns_422(self):
        tguid = make_topic_guid(OWNER, REPO, 2)
        with patch("ifcurl.bcf_api._fget", return_value=ISSUE_2):
            r = client.post(
                f"/bcf/3.0/projects/{OWNER}/{REPO}/topics/{tguid}/viewpoints",
                json={},
                headers={"Authorization": AUTH},
            )
        assert r.status_code == 422
