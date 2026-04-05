"""Unit tests for web_search tool — OAuth auth resolution and 401 retry."""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest


# All patches target the web_search module where names are looked up
_MOD = "company.assets.tools.web_search.web_search"


class TestResolveAnthropicAuth:
    """_resolve_anthropic_auth should use stored token first, only refresh when needed."""

    def test_api_key_auth(self):
        """Non-OAuth: returns x-api-key header."""
        from company.assets.tools.web_search.web_search import _resolve_anthropic_auth

        with patch(f"{_MOD}._load_env_var") as mock_env:
            mock_env.side_effect = lambda name: {
                "ANTHROPIC_AUTH_METHOD": "api_key",
                "ANTHROPIC_API_KEY": "sk-test-key",
            }.get(name, "")
            token, headers = _resolve_anthropic_auth()

        assert token == "sk-test-key"
        assert headers == {"x-api-key": "sk-test-key"}

    def test_oauth_uses_stored_token_without_refresh(self):
        """OAuth with valid access token should NOT call _refresh_anthropic_token."""
        from company.assets.tools.web_search.web_search import _resolve_anthropic_auth

        with patch(f"{_MOD}._load_env_var") as mock_env, \
             patch(f"{_MOD}._refresh_anthropic_token") as mock_refresh:
            mock_env.side_effect = lambda name: {
                "ANTHROPIC_AUTH_METHOD": "oauth",
                "ANTHROPIC_API_KEY": "stored-access-token",
                "ANTHROPIC_REFRESH_TOKEN": "some-refresh-token",
            }.get(name, "")
            token, headers = _resolve_anthropic_auth()

        assert token == "stored-access-token"
        assert headers == {"Authorization": "Bearer stored-access-token"}
        mock_refresh.assert_not_called()

    def test_oauth_no_access_token_tries_refresh(self):
        """OAuth with no access token but refresh token should try to refresh."""
        from company.assets.tools.web_search.web_search import _resolve_anthropic_auth

        with patch(f"{_MOD}._load_env_var") as mock_env, \
             patch(f"{_MOD}._refresh_anthropic_token", return_value="fresh-token") as mock_refresh:
            mock_env.side_effect = lambda name: {
                "ANTHROPIC_AUTH_METHOD": "oauth",
                "ANTHROPIC_API_KEY": "",
                "ANTHROPIC_REFRESH_TOKEN": "my-refresh-token",
            }.get(name, "")
            token, headers = _resolve_anthropic_auth()

        assert token == "fresh-token"
        assert headers == {"Authorization": "Bearer fresh-token"}
        mock_refresh.assert_called_once_with("my-refresh-token")

    def test_oauth_no_tokens_returns_empty(self):
        """OAuth with no tokens at all returns empty."""
        from company.assets.tools.web_search.web_search import _resolve_anthropic_auth

        with patch(f"{_MOD}._load_env_var", return_value="") as mock_env:
            # First call for auth_method returns "oauth", rest return ""
            mock_env.side_effect = lambda name: "oauth" if name == "ANTHROPIC_AUTH_METHOD" else ""
            token, headers = _resolve_anthropic_auth()

        assert token == ""
        assert headers == {}


class TestWebSearch401Retry:
    """web_search should retry once on 401 with refreshed token."""

    def test_401_triggers_refresh_and_retry(self):
        """On 401, web_search should refresh token and retry the API call."""
        from company.assets.tools.web_search.web_search import web_search

        success_response = {
            "content": [
                {"type": "text", "text": "Search results here"},
            ]
        }

        call_count = 0

        def mock_post_json(url, headers, payload, timeout=30):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call: 401 expired token
                return None, "HTTP 401: Unauthorized"
            # Second call: success with fresh token
            return success_response, None

        with patch(f"{_MOD}._resolve_anthropic_auth", return_value=("old-token", {"Authorization": "Bearer old-token"})), \
             patch(f"{_MOD}._post_json", side_effect=mock_post_json), \
             patch(f"{_MOD}._load_env_var") as mock_env, \
             patch(f"{_MOD}._refresh_anthropic_token", return_value="fresh-token") as mock_refresh:
            mock_env.side_effect = lambda name: {
                "ANTHROPIC_AUTH_METHOD": "oauth",
                "ANTHROPIC_REFRESH_TOKEN": "my-refresh",
            }.get(name, "")

            result = web_search.invoke({"query": "test query"})

        assert result["status"] == "ok"
        assert result["answer"] == "Search results here"
        mock_refresh.assert_called_once_with("my-refresh")
        assert call_count == 2

    def test_401_no_refresh_token_returns_error(self):
        """On 401 without refresh token, returns the original error."""
        from company.assets.tools.web_search.web_search import web_search

        with patch(f"{_MOD}._resolve_anthropic_auth", return_value=("old-token", {"Authorization": "Bearer old-token"})), \
             patch(f"{_MOD}._post_json", return_value=(None, "HTTP 401: Unauthorized")), \
             patch(f"{_MOD}._load_env_var") as mock_env:
            mock_env.side_effect = lambda name: {
                "ANTHROPIC_AUTH_METHOD": "oauth",
                "ANTHROPIC_REFRESH_TOKEN": "",
            }.get(name, "")

            result = web_search.invoke({"query": "test query"})

        assert result["status"] == "error"
        assert "401" in result["message"]

    def test_non_401_error_no_retry(self):
        """Non-401 errors should not trigger refresh/retry."""
        from company.assets.tools.web_search.web_search import web_search

        with patch(f"{_MOD}._resolve_anthropic_auth", return_value=("token", {"Authorization": "Bearer token"})), \
             patch(f"{_MOD}._post_json", return_value=(None, "HTTP 500: Internal Server Error")), \
             patch(f"{_MOD}._refresh_anthropic_token") as mock_refresh:

            result = web_search.invoke({"query": "test query"})

        assert result["status"] == "error"
        assert "500" in result["message"]
        mock_refresh.assert_not_called()

    def test_api_key_401_no_retry(self):
        """API key auth with 401 should not attempt OAuth refresh."""
        from company.assets.tools.web_search.web_search import web_search

        with patch(f"{_MOD}._resolve_anthropic_auth", return_value=("bad-key", {"x-api-key": "bad-key"})), \
             patch(f"{_MOD}._post_json", return_value=(None, "HTTP 401: Invalid API key")), \
             patch(f"{_MOD}._load_env_var") as mock_env, \
             patch(f"{_MOD}._refresh_anthropic_token") as mock_refresh:
            mock_env.side_effect = lambda name: {
                "ANTHROPIC_AUTH_METHOD": "api_key",
            }.get(name, "")

            result = web_search.invoke({"query": "test query"})

        assert result["status"] == "error"
        mock_refresh.assert_not_called()


class TestRefreshTokenFormat:
    """_refresh_anthropic_token should use form-urlencoded, not JSON."""

    def test_refresh_sends_form_urlencoded(self):
        """Token refresh request must use application/x-www-form-urlencoded."""
        from company.assets.tools.web_search.web_search import _refresh_anthropic_token

        captured_req = {}

        def mock_urlopen(req, timeout=None):
            captured_req["content_type"] = req.get_header("Content-type")
            captured_req["data"] = req.data.decode("utf-8")
            captured_req["url"] = req.full_url

            resp = MagicMock()
            resp.read.return_value = b'{"access_token": "new-tok", "refresh_token": "new-ref"}'
            resp.__enter__ = lambda s: resp
            resp.__exit__ = MagicMock(return_value=False)
            return resp

        with patch("urllib.request.urlopen", side_effect=mock_urlopen), \
             patch("onemancompany.core.config.update_env_var"):
            result = _refresh_anthropic_token("my-refresh-token")

        assert result == "new-tok"
        assert captured_req["content_type"] == "application/x-www-form-urlencoded"
        assert "grant_type=refresh_token" in captured_req["data"]
        assert "refresh_token=my-refresh-token" in captured_req["data"]

    def test_refresh_failure_returns_empty(self):
        """On network error, returns empty string."""
        from company.assets.tools.web_search.web_search import _refresh_anthropic_token

        with patch("urllib.request.urlopen", side_effect=Exception("network error")):
            result = _refresh_anthropic_token("some-token")

        assert result == ""
