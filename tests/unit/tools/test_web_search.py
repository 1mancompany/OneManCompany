"""Unit tests for web_search tool — DuckDuckGo-based implementation."""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest


_MOD = "company.assets.tools.web_search.web_search"


class TestWebSearchBasic:
    """Basic web_search behavior."""

    def test_empty_query_returns_error(self):
        """Empty query should return error status."""
        from company.assets.tools.web_search.web_search import web_search

        result = web_search.invoke({"query": ""})
        assert result["status"] == "error"
        assert "empty" in result["message"]

    def test_whitespace_query_returns_error(self):
        """Whitespace-only query should return error status."""
        from company.assets.tools.web_search.web_search import web_search

        result = web_search.invoke({"query": "   "})
        assert result["status"] == "error"

    def test_successful_search(self):
        """Mocked DDGS.text should return structured results."""
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
        assert len(result["sources"]) == 2
        assert result["sources"][0]["title"] == "Python.org"
        assert result["sources"][0]["url"] == "https://python.org"
        assert "Python.org" in result["answer"]

    def test_max_results_clamped(self):
        """max_results should be clamped between 1 and 20."""
        from company.assets.tools.web_search.web_search import web_search

        mock_ddgs = MagicMock()
        mock_ddgs.__enter__ = lambda s: mock_ddgs
        mock_ddgs.__exit__ = MagicMock(return_value=False)
        mock_ddgs.text.return_value = []

        with patch(f"{_MOD}.DDGS", return_value=mock_ddgs):
            web_search.invoke({"query": "test", "max_results": 50})
            mock_ddgs.text.assert_called_with("test", max_results=20)

        with patch(f"{_MOD}.DDGS", return_value=mock_ddgs):
            web_search.invoke({"query": "test", "max_results": 0})
            mock_ddgs.text.assert_called_with("test", max_results=1)

    def test_no_results_returns_ok_with_empty(self):
        """Zero results should still return ok status with 'No results found'."""
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
    """Error handling in web_search."""

    def test_ddgs_exception_returns_error(self):
        """Network/API failure should return error status."""
        from company.assets.tools.web_search.web_search import web_search

        mock_ddgs = MagicMock()
        mock_ddgs.__enter__ = lambda s: mock_ddgs
        mock_ddgs.__exit__ = MagicMock(return_value=False)
        mock_ddgs.text.side_effect = Exception("Connection timeout")

        with patch(f"{_MOD}.DDGS", return_value=mock_ddgs):
            result = web_search.invoke({"query": "test"})

        assert result["status"] == "error"
        assert "Connection timeout" in result["message"]

    def test_import_error_returns_error(self):
        """Missing ddgs package should return helpful error."""
        from company.assets.tools.web_search.web_search import web_search

        with patch(f"{_MOD}.DDGS", side_effect=ImportError("No module named 'ddgs'")):
            # The ImportError is caught in the except block
            result = web_search.invoke({"query": "test"})

        assert result["status"] == "error"
