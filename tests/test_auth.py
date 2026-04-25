"""Tests for ifcurl.auth — token config and URL injection."""

from __future__ import annotations

import json

import pytest

from ifcurl.auth import get_token_for_host, inject_token


class TestGetTokenForHost:
    def test_returns_token_for_known_host(self, tmp_path, monkeypatch):
        config = tmp_path / "tokens.json"
        config.write_text(json.dumps({"hosts": {"github.com": "ghp_abc123"}}))
        monkeypatch.setattr("ifcurl.auth.config_path", lambda: config)
        assert get_token_for_host("github.com") == "ghp_abc123"

    def test_returns_none_for_unknown_host(self, tmp_path, monkeypatch):
        config = tmp_path / "tokens.json"
        config.write_text(json.dumps({"hosts": {"github.com": "ghp_abc123"}}))
        monkeypatch.setattr("ifcurl.auth.config_path", lambda: config)
        assert get_token_for_host("gitlab.com") is None

    def test_returns_none_when_file_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "ifcurl.auth.config_path", lambda: tmp_path / "nonexistent.json"
        )
        assert get_token_for_host("github.com") is None

    def test_returns_none_on_malformed_json(self, tmp_path, monkeypatch):
        config = tmp_path / "tokens.json"
        config.write_text("not json {{{")
        monkeypatch.setattr("ifcurl.auth.config_path", lambda: config)
        assert get_token_for_host("github.com") is None

    def test_returns_none_for_empty_token(self, tmp_path, monkeypatch):
        config = tmp_path / "tokens.json"
        config.write_text(json.dumps({"hosts": {"github.com": ""}}))
        monkeypatch.setattr("ifcurl.auth.config_path", lambda: config)
        assert get_token_for_host("github.com") is None

    def test_multiple_hosts(self, tmp_path, monkeypatch):
        config = tmp_path / "tokens.json"
        config.write_text(
            json.dumps(
                {
                    "hosts": {
                        "github.com": "ghp_token",
                        "gitlab.example.com": "glpat_token",
                    }
                }
            )
        )
        monkeypatch.setattr("ifcurl.auth.config_path", lambda: config)
        assert get_token_for_host("github.com") == "ghp_token"
        assert get_token_for_host("gitlab.example.com") == "glpat_token"


class TestInjectToken:
    def test_basic_https_url(self):
        result = inject_token("https://github.com/org/repo", "mytoken")
        assert result == "https://mytoken@github.com/org/repo"

    def test_preserves_path(self):
        result = inject_token("https://example.com/org/repo", "tok")
        assert result.endswith("/org/repo")

    def test_preserves_custom_port(self):
        result = inject_token("https://example.com:8080/org/repo", "tok")
        assert "8080" in result
        assert result == "https://tok@example.com:8080/org/repo"

    def test_token_appears_before_host(self):
        result = inject_token("https://github.com/org/repo", "ghp_xyz")
        assert "ghp_xyz@github.com" in result

    def test_scheme_preserved(self):
        result = inject_token("https://github.com/org/repo", "tok")
        assert result.startswith("https://")
