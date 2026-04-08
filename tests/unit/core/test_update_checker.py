"""Tests for update_checker — version comparison logic."""
from __future__ import annotations

import pytest


class TestIsNewer:
    def test_newer_patch(self):
        from onemancompany.core.update_checker import _is_newer
        assert _is_newer("0.4.62", "0.4.61") is True

    def test_same_version(self):
        from onemancompany.core.update_checker import _is_newer
        assert _is_newer("0.4.61", "0.4.61") is False

    def test_older_version(self):
        from onemancompany.core.update_checker import _is_newer
        assert _is_newer("0.4.60", "0.4.61") is False

    def test_newer_minor(self):
        from onemancompany.core.update_checker import _is_newer
        assert _is_newer("0.5.0", "0.4.99") is True

    def test_newer_major(self):
        from onemancompany.core.update_checker import _is_newer
        assert _is_newer("1.0.0", "0.99.99") is True

    def test_invalid_version(self):
        from onemancompany.core.update_checker import _is_newer
        assert _is_newer("abc", "0.4.61") is False
        assert _is_newer("0.4.61", "") is False
