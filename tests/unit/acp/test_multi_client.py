"""Unit tests for multi-client IDE port discovery."""
from __future__ import annotations


# ---------------------------------------------------------------------------
# Test 1: IDE endpoint recorded
# ---------------------------------------------------------------------------


class TestIdeEndpointRecorded:
    """test_ide_endpoint_recorded"""

    def test_ide_endpoint_recorded(self) -> None:
        """record_ide_endpoint should store the port, and get_ide_endpoint should retrieve it."""
        from onemancompany.acp.client import AcpConnectionManager

        mgr = AcpConnectionManager()
        mgr.record_ide_endpoint("00010", 9876)
        assert mgr.get_ide_endpoint("00010") == 9876


# ---------------------------------------------------------------------------
# Test 2: IDE endpoint None when not set
# ---------------------------------------------------------------------------


class TestIdeEndpointNoneWhenNotSet:
    """test_ide_endpoint_none_when_not_set"""

    def test_ide_endpoint_none_when_not_set(self) -> None:
        """get_ide_endpoint should return None if endpoint was never recorded."""
        from onemancompany.acp.client import AcpConnectionManager

        mgr = AcpConnectionManager()
        assert mgr.get_ide_endpoint("00010") is None
