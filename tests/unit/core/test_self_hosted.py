"""Unit tests for core/self_hosted.py — self-hosted employee utilities."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from onemancompany.core.self_hosted import get_session_summary, is_self_hosted_ready


# ---------------------------------------------------------------------------
# is_self_hosted_ready
# ---------------------------------------------------------------------------

class TestIsSelfHostedReady:
    def test_always_returns_true(self):
        assert is_self_hosted_ready("00010") is True

    def test_any_employee_id(self):
        assert is_self_hosted_ready("") is True
        assert is_self_hosted_ready("nonexistent") is True


# ---------------------------------------------------------------------------
# get_session_summary
# ---------------------------------------------------------------------------

class TestGetSessionSummary:
    def test_no_sessions(self):
        with patch("onemancompany.core.self_hosted.list_sessions", return_value=[]):
            summary = get_session_summary("00010")
            assert summary["employee_id"] == "00010"
            assert summary["session_count"] == 0
            assert summary["sessions"] == []

    def test_with_sessions(self):
        sessions = [
            {"project_id": "p1", "session_id": "s1", "used": True},
            {"project_id": "p2", "session_id": "s2", "used": False},
        ]
        with patch("onemancompany.core.self_hosted.list_sessions", return_value=sessions):
            summary = get_session_summary("00010")
            assert summary["session_count"] == 2
            assert summary["sessions"] == sessions
