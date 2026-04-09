"""Unit tests for web_search tool — DuckDuckGo-based implementation."""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest


_MOD = "company.assets.tools.web_search.web_search"


class TestWebSearchValidation:
    """Input validation."""

    def test_empty_query_returns_error(self):
        from company.assets.tools.web_search.web_search import web_search
        result = web_search.invoke({"query": ""})
        assert result["status"] == "error"
        assert "short" in result["message"]

    def test_single_char_query_returns_error(self):
        from company.assets.tools.web_search.web_search import web_search
        result = web_search.invoke({"query": "a"})
        assert result["status"] == "error"
        assert "minimum 2" in result["message"]

    def test_whitespace_query_returns_error(self):
        from company.assets.tools.web_search.web_search import web_search
        result = web_search.invoke({"query": "   "})
        assert result["status"] == "error"

    def test_two_char_query_is_valid(self):
        from company.assets.tools.web_search.web_search import web_search
        mock_ddgs = MagicMock()
        mock_ddgs.__enter__ = lambda s: mock_ddgs
        mock_ddgs.__exit__ = MagicMock(return_value=False)
        mock_ddgs.text.return_value = []
        with patch(f"{_MOD}.DDGS", return_value=mock_ddgs):
            result = web_search.invoke({"query": "hi"})
        assert result["status"] == "ok"


class TestWebSearchResults:
    """Result format and content."""

    def test_successful_search(self):
        from company.assets.tools.web_search.web_search import web_search
        mock_results = [
            {"title": "Python.org", "href": "https://python.org", "body": "Welcome to Python"},
            {"title": "Python Tutorial", "href": "https://docs.python.org", "body": "Learn Python"},
        ]
        mock_ddgs = MagicMock()
        mock_ddgs.__enter__ = lambda s: mock_ddgs
        mock_ddgs.__exit__ = MagicMock(return_value=False)
        mock_ddgs.text.return_value = mock_results

        with patch(f"{_MOD}.DDGS", return_value=mock_ddgs):
            result = web_search.invoke({"query": "Python programming"})

        assert result["status"] == "ok"
        assert result["source_count"] == 2
        assert result["sources"][0]["title"] == "Python.org"
        assert result["sources"][0]["url"] == "https://python.org"
        assert "Python.org" in result["answer"]

    def test_duration_tracked(self):
        from company.assets.tools.web_search.web_search import web_search
        mock_ddgs = MagicMock()
        mock_ddgs.__enter__ = lambda s: mock_ddgs
        mock_ddgs.__exit__ = MagicMock(return_value=False)
        mock_ddgs.text.return_value = []
        with patch(f"{_MOD}.DDGS", return_value=mock_ddgs):
            result = web_search.invoke({"query": "test query"})
        assert "duration_seconds" in result
        assert isinstance(result["duration_seconds"], float)

    def test_source_citation_in_answer(self):
        from company.assets.tools.web_search.web_search import web_search
        mock_results = [
            {"title": "Example", "href": "https://example.com", "body": "Test"},
        ]
        mock_ddgs = MagicMock()
        mock_ddgs.__enter__ = lambda s: mock_ddgs
        mock_ddgs.__exit__ = MagicMock(return_value=False)
        mock_ddgs.text.return_value = mock_results
        with patch(f"{_MOD}.DDGS", return_value=mock_ddgs):
            result = web_search.invoke({"query": "test query"})
        assert "Sources:" in result["answer"]
        assert "[Example](https://example.com)" in result["answer"]
        assert "REMINDER" in result["answer"]

    def test_max_results_clamped_to_8(self):
        from company.assets.tools.web_search.web_search import web_search
        mock_ddgs = MagicMock()
        mock_ddgs.__enter__ = lambda s: mock_ddgs
        mock_ddgs.__exit__ = MagicMock(return_value=False)
        mock_ddgs.text.return_value = []
        with patch(f"{_MOD}.DDGS", return_value=mock_ddgs):
            web_search.invoke({"query": "test", "max_results": 50})
            mock_ddgs.text.assert_called_with("test", max_results=8)

    def test_no_results_returns_ok(self):
        from company.assets.tools.web_search.web_search import web_search
        mock_ddgs = MagicMock()
        mock_ddgs.__enter__ = lambda s: mock_ddgs
        mock_ddgs.__exit__ = MagicMock(return_value=False)
        mock_ddgs.text.return_value = []
        with patch(f"{_MOD}.DDGS", return_value=mock_ddgs):
            result = web_search.invoke({"query": "xyznonexistent12345"})
        assert result["status"] == "ok"
        assert result["source_count"] == 0
        assert "No results found" in result["answer"]


class TestWebSearchErrors:
    """Error handling."""

    def test_exception_returns_error_with_duration(self):
        from company.assets.tools.web_search.web_search import web_search
        mock_ddgs = MagicMock()
        mock_ddgs.__enter__ = lambda s: mock_ddgs
        mock_ddgs.__exit__ = MagicMock(return_value=False)
        mock_ddgs.text.side_effect = Exception("Connection timeout")
        with patch(f"{_MOD}.DDGS", return_value=mock_ddgs):
            result = web_search.invoke({"query": "test"})
        assert result["status"] == "error"
        assert "Connection timeout" in result["message"]
        assert "duration_seconds" in result
