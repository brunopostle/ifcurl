"""Tests for the OpenCDE select-documents picker endpoints."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from ifcurl.documents_api import encode_document_id
from ifcurl.service import _rate_hits, app

client = TestClient(app)

CALLBACK = "https://viewer.example.com/api/opencde/callback"


@pytest.fixture(autouse=True)
def reset_rate_limit():
    _rate_hits.clear()
    yield
    _rate_hits.clear()


# ---------------------------------------------------------------------------
# POST /documents/1.0/select-documents
# ---------------------------------------------------------------------------

class TestSelectDocuments:
    def _post(self, callback_url=CALLBACK, headers=None):
        return client.post(
            "/documents/1.0/select-documents",
            json={"callback": {"url": callback_url, "expires_in": 3600}},
            headers=headers or {},
        )

    def test_returns_200(self):
        r = self._post()
        assert r.status_code == 200

    def test_returns_select_documents_url(self):
        r = self._post()
        assert "select_documents_url" in r.json()

    def test_url_points_to_picker_ui(self):
        r = self._post()
        url = r.json()["select_documents_url"]
        assert "/documents/1.0/select-documents/ui" in url

    def test_callback_url_encoded_in_picker_url(self):
        r = self._post()
        picker_url = r.json()["select_documents_url"]
        parsed = urlparse(picker_url)
        params = parse_qs(parsed.query)
        assert "callback_url" in params
        assert params["callback_url"][0] == CALLBACK

    def test_picker_url_uses_forwarded_host(self):
        r = self._post(
            headers={"x-forwarded-host": "cde.example.com", "x-forwarded-proto": "https"}
        )
        url = r.json()["select_documents_url"]
        assert url.startswith("https://cde.example.com/")

    def test_missing_callback_url_returns_422(self):
        r = client.post(
            "/documents/1.0/select-documents",
            json={"callback": {"expires_in": 3600}},
        )
        assert r.status_code == 422

    def test_missing_callback_object_returns_422(self):
        r = client.post("/documents/1.0/select-documents", json={})
        assert r.status_code == 422

    def test_access_token_forwarded_to_picker_url(self):
        r = self._post(headers={"Authorization": "Bearer secret-tok-123"})
        url = r.json()["select_documents_url"]
        params = parse_qs(urlparse(url).query)
        assert params["access_token"][0] == "secret-tok-123"

    def test_token_scheme_forwarded_to_picker_url(self):
        # Forgejo's own "token <tok>" form is also accepted
        r = self._post(headers={"Authorization": "token abc-789"})
        url = r.json()["select_documents_url"]
        params = parse_qs(urlparse(url).query)
        assert params["access_token"][0] == "abc-789"

    def test_no_access_token_when_unauthenticated(self):
        r = self._post()
        url = r.json()["select_documents_url"]
        params = parse_qs(urlparse(url).query)
        assert "access_token" not in params


# ---------------------------------------------------------------------------
# GET /documents/1.0/select-documents/ui
# ---------------------------------------------------------------------------

class TestSelectDocumentsUi:
    def _get(self, callback_url=CALLBACK):
        from urllib.parse import quote
        return client.get(
            "/documents/1.0/select-documents/ui",
            params={"callback_url": callback_url},
        )

    def test_returns_200(self):
        r = self._get()
        assert r.status_code == 200

    def test_returns_html(self):
        r = self._get()
        assert "text/html" in r.headers["content-type"]

    def test_html_contains_callback_url(self):
        r = self._get()
        assert CALLBACK in r.text

    def test_html_contains_forgejo_base(self):
        r = self._get()
        # FORGEJO_BASE is embedded as a JS string literal
        assert "localhost:3000" in r.text or "forgejo" in r.text.lower()

    def test_html_has_repo_select(self):
        r = self._get()
        assert "repo-select" in r.text

    def test_html_has_file_list(self):
        r = self._get()
        assert "file-list" in r.text

    def test_missing_callback_url_returns_400(self):
        r = client.get("/documents/1.0/select-documents/ui")
        assert r.status_code in (400, 422)

    def test_callback_url_safely_embedded(self):
        # Ensure XSS-dangerous characters in callback URL are JSON-escaped
        evil = 'https://example.com/cb?x=1&y=</script><script>alert(1)</script>'
        r = self._get(callback_url=evil)
        assert r.status_code == 200
        # The raw </script> should not appear unescaped outside a JSON string
        assert "</script><script>" not in r.text

    def test_access_token_embedded_in_html(self):
        r = client.get(
            "/documents/1.0/select-documents/ui",
            params={"callback_url": CALLBACK, "access_token": "tok-xyz"},
        )
        assert r.status_code == 200
        assert "tok-xyz" in r.text
        assert "Bearer" in r.text

    def test_access_token_null_when_absent(self):
        r = self._get()
        # JS receives the literal null so it falls back to cookie auth
        assert "const ACCESS_TOKEN = null;" in r.text


# ---------------------------------------------------------------------------
# document_id encoding round-trip (JS parity)
# ---------------------------------------------------------------------------

class TestDocumentIdEncoding:
    """Verify the Python encoder matches what the picker JS produces.

    The picker uses btoa(unescape(encodeURIComponent(raw))) then swaps +→- /→_
    and strips padding — identical to base64.urlsafe_b64encode().rstrip(b'=').
    """
    def test_ascii_path(self):
        doc_id = encode_document_id("alice", "myproject", "models/building.ifc")
        # Decode and verify round-trip
        from ifcurl.documents_api import decode_document_id
        assert decode_document_id(doc_id) == ("alice", "myproject", "models/building.ifc")

    def test_no_padding_chars(self):
        doc_id = encode_document_id("alice", "proj", "a.ifc")
        assert "=" not in doc_id

    def test_url_safe_chars_only(self):
        doc_id = encode_document_id("alice", "proj", "deep/nested/file.ifc")
        assert "+" not in doc_id
        assert "/" not in doc_id
