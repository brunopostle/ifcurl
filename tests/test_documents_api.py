"""Tests for ifcurl.documents_api — OpenCDE Documents API routes."""

from __future__ import annotations

from unittest.mock import call, patch

import pytest
from fastapi.testclient import TestClient

from ifcurl.documents_api import decode_document_id, encode_document_id
from ifcurl.service import _rate_hits, app

client = TestClient(app)

OWNER, REPO, PATH = "alice", "myproject", "models/building.ifc"

TAG_V1 = {
    "name": "v1.0",
    "commit": {"sha": "1111aaaa2222bbbb3333cccc4444dddd5555eeee", "created": "2026-01-01T10:00:00Z"},
}
TAG_V2 = {
    "name": "v2.0",
    "commit": {"sha": "aaaa1111bbbb2222cccc3333dddd4444eeee5555", "created": "2026-02-01T10:00:00Z"},
}
HEAD_COMMIT = {
    "sha": "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
    "commit": {
        "author": {"name": "Alice", "email": "alice@example.com", "date": "2026-03-01T10:00:00Z"},
        "committer": {"name": "Alice", "email": "alice@example.com", "date": "2026-03-01T10:00:00Z"},
        "message": "WIP changes",
    },
}
# HEAD at same SHA as the latest tag — should not add a duplicate version
HEAD_AT_TAG = {
    "sha": TAG_V2["commit"]["sha"],
    "commit": {
        "author": {"name": "Alice", "email": "alice@example.com", "date": "2026-02-01T10:00:00Z"},
        "committer": {"name": "Alice", "email": "alice@example.com", "date": "2026-02-01T10:00:00Z"},
        "message": "Release v2.0",
    },
}


@pytest.fixture(autouse=True)
def reset_rate_limit():
    _rate_hits.clear()
    yield
    _rate_hits.clear()


def _post(doc_ids, tags=None, head_commits=None, headers=None):
    if tags is None:
        tags = [TAG_V1, TAG_V2]
    if head_commits is None:
        head_commits = [HEAD_COMMIT]

    def mock_fget(path, auth, params=None):
        if "/tags" in path:
            return tags
        if "/commits" in path:
            return head_commits
        return []

    with patch("ifcurl.documents_api._fget", side_effect=mock_fget):
        return client.post(
            "/documents/1.0/document-versions",
            json={"document_ids": doc_ids},
            headers=headers or {},
        )


# ---------------------------------------------------------------------------
# document_id encoding
# ---------------------------------------------------------------------------

class TestDocumentId:
    def test_roundtrip(self):
        doc_id = encode_document_id(OWNER, REPO, PATH)
        assert decode_document_id(doc_id) == (OWNER, REPO, PATH)

    def test_path_with_slashes(self):
        doc_id = encode_document_id("alice", "proj", "a/b/c.ifc")
        assert decode_document_id(doc_id) == ("alice", "proj", "a/b/c.ifc")

    def test_stable(self):
        assert encode_document_id(OWNER, REPO, PATH) == encode_document_id(OWNER, REPO, PATH)

    def test_different_inputs_differ(self):
        assert encode_document_id("alice", "a", "m.ifc") != encode_document_id("alice", "b", "m.ifc")

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            decode_document_id("!!!not-base64!!!")


# ---------------------------------------------------------------------------
# POST /documents/1.0/document-versions
# ---------------------------------------------------------------------------

class TestDocumentVersions:
    def test_returns_200(self):
        doc_id = encode_document_id(OWNER, REPO, PATH)
        r = _post([doc_id])
        assert r.status_code == 200

    def test_two_tags_plus_head_give_three_versions(self):
        doc_id = encode_document_id(OWNER, REPO, PATH)
        r = _post([doc_id])
        assert len(r.json()) == 3

    def test_tag_names_used_as_version_number(self):
        doc_id = encode_document_id(OWNER, REPO, PATH)
        r = _post([doc_id])
        version_numbers = {v["version_number"] for v in r.json()}
        assert "v1.0" in version_numbers
        assert "v2.0" in version_numbers

    def test_head_version_number_is_short_sha(self):
        doc_id = encode_document_id(OWNER, REPO, PATH)
        r = _post([doc_id])
        versions = sorted(r.json(), key=lambda v: v["version_index"])
        assert versions[-1]["version_number"] == HEAD_COMMIT["sha"][:8]

    def test_oldest_tag_is_version_index_1(self):
        doc_id = encode_document_id(OWNER, REPO, PATH)
        r = _post([doc_id])
        versions = sorted(r.json(), key=lambda v: v["version_index"])
        assert versions[0]["version_number"] == "v1.0"

    def test_head_at_same_sha_as_tag_not_duplicated(self):
        doc_id = encode_document_id(OWNER, REPO, PATH)
        r = _post([doc_id], head_commits=[HEAD_AT_TAG])
        assert len(r.json()) == 2  # only the two tags, HEAD deduplicated

    def test_no_tags_just_head(self):
        doc_id = encode_document_id(OWNER, REPO, PATH)
        r = _post([doc_id], tags=[])
        versions = r.json()
        assert len(versions) == 1
        assert versions[0]["version_number"] == HEAD_COMMIT["sha"][:8]

    def test_download_url_uses_commit_sha(self):
        doc_id = encode_document_id(OWNER, REPO, PATH)
        r = _post([doc_id])
        for v in r.json():
            assert "/raw/commit/" in v["document_version_download"]

    def test_download_url_contains_path(self):
        doc_id = encode_document_id(OWNER, REPO, PATH)
        r = _post([doc_id])
        for v in r.json():
            assert PATH in v["document_version_download"]

    def test_download_url_uses_forwarded_host(self):
        doc_id = encode_document_id(OWNER, REPO, PATH)
        r = _post([doc_id], headers={"x-forwarded-host": "git.example.com", "x-forwarded-proto": "https"})
        for v in r.json():
            assert v["document_version_download"].startswith("https://git.example.com/")

    def test_document_id_preserved(self):
        doc_id = encode_document_id(OWNER, REPO, PATH)
        r = _post([doc_id])
        for v in r.json():
            assert v["document_id"] == doc_id

    def test_invalid_document_id_skipped(self):
        valid_id = encode_document_id(OWNER, REPO, PATH)
        r = _post(["not-valid!!!!", valid_id])
        assert all(v["document_id"] == valid_id for v in r.json())

    def test_empty_document_ids(self):
        r = _post([])
        assert r.json() == []

    def test_no_commits_and_no_tags_gives_no_versions(self):
        doc_id = encode_document_id(OWNER, REPO, PATH)
        r = _post([doc_id], tags=[], head_commits=[])
        assert r.json() == []


# ---------------------------------------------------------------------------
# GET /documents/1.0/document-metadata/{document_id}
# ---------------------------------------------------------------------------

FORGEJO_FILE_INFO = {
    "name": "building.ifc",
    "path": PATH,
    "sha": "abcdef1234567890",
    "size": 98765,
    "type": "file",
}


class TestDocumentMetadata:
    def test_returns_200(self):
        doc_id = encode_document_id(OWNER, REPO, PATH)
        with patch("ifcurl.documents_api._fget", return_value=FORGEJO_FILE_INFO):
            r = client.get(f"/documents/1.0/document-metadata/{doc_id}")
        assert r.status_code == 200

    def test_returns_document_id(self):
        doc_id = encode_document_id(OWNER, REPO, PATH)
        with patch("ifcurl.documents_api._fget", return_value=FORGEJO_FILE_INFO):
            r = client.get(f"/documents/1.0/document-metadata/{doc_id}")
        assert r.json()["document_id"] == doc_id

    def test_returns_filename(self):
        doc_id = encode_document_id(OWNER, REPO, PATH)
        with patch("ifcurl.documents_api._fget", return_value=FORGEJO_FILE_INFO):
            r = client.get(f"/documents/1.0/document-metadata/{doc_id}")
        assert r.json()["name"] == "building.ifc"

    def test_invalid_document_id_returns_404(self):
        r = client.get("/documents/1.0/document-metadata/not-valid!!!!")
        assert r.status_code == 404

    def test_forgejo_404_propagates(self):
        from fastapi import HTTPException
        doc_id = encode_document_id(OWNER, REPO, PATH)
        with patch("ifcurl.documents_api._fget", side_effect=HTTPException(status_code=404)):
            r = client.get(f"/documents/1.0/document-metadata/{doc_id}")
        assert r.status_code == 404
