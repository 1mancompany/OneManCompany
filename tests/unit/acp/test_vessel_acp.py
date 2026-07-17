"""Tests for ACP integration in EmployeeManager (vessel.py)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture()
def _stub_store():
    """Stub out store helpers that touch disk."""
    with (
        patch("onemancompany.core.vessel._store") as mock_store,
        patch("onemancompany.core.vessel._load_task_history", return_value=([], "")),
    ):
        mock_store.load_employee.return_value = {"role": "Engineer"}
        mock_store.save_employee_runtime = AsyncMock()
        yield mock_store


def test_register_acp_creates_connection(_stub_store):
    """register_acp() should delegate to AcpConnectionManager and create vessel."""
    from onemancompany.core.vessel import EmployeeManager

    mgr = EmployeeManager()

    mock_acp_mgr = MagicMock()
    mock_acp_mgr.register_employee = AsyncMock()
    mock_acp_mgr._sessions = {"00010": "sess-00010"}
    mgr._acp_manager = mock_acp_mgr

    loop = asyncio.new_event_loop()
    try:
        vessel = loop.run_until_complete(
            mgr.register_acp("00010", "langchain", {"KEY": "val"})
        )
    finally:
        loop.close()

    mock_acp_mgr.register_employee.assert_called_once_with(
        "00010", "langchain", {"KEY": "val"}
    )
    assert "00010" in mgr.vessels
    assert vessel is mgr.vessels["00010"]
    assert vessel.employee_id == "00010"


def test_register_acp_lazy_creates_manager(_stub_store):
    """register_acp() should lazily import and create AcpConnectionManager."""
    from onemancompany.core.vessel import EmployeeManager

    mgr = EmployeeManager()
    assert mgr._acp_manager is None

    with patch(
        "onemancompany.core.vessel.AcpConnectionManager",
        create=True,
    ) as MockCls:
        mock_instance = MagicMock()
        mock_instance.register_employee = AsyncMock()
        MockCls.return_value = mock_instance

        # Patch the lazy import inside register_acp
        with patch.dict(
            "sys.modules",
            {"onemancompany.acp.client": MagicMock(AcpConnectionManager=MockCls)},
        ):
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(
                    mgr.register_acp("00010", "langchain", {})
                )
            finally:
                loop.close()

    assert mgr._acp_manager is not None


def test_register_acp_with_config(_stub_store):
    """register_acp() should store config when provided."""
    from onemancompany.core.vessel import EmployeeManager, VesselConfig

    mgr = EmployeeManager()

    mock_acp_mgr = MagicMock()
    mock_acp_mgr.register_employee = AsyncMock()
    mgr._acp_manager = mock_acp_mgr

    cfg = VesselConfig()

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(
            mgr.register_acp("00010", "langchain", {}, config=cfg)
        )
    finally:
        loop.close()

    assert mgr.configs["00010"] is cfg


def test_get_acp_mode_review():
    """_get_acp_mode should return 'review' for REVIEW nodes."""
    from onemancompany.core.task_lifecycle import NodeType
    from onemancompany.core.vessel import EmployeeManager

    mgr = EmployeeManager()

    node = MagicMock()
    node.node_type = NodeType.REVIEW
    assert mgr._get_acp_mode(node) == "review"


def test_get_acp_mode_task():
    """_get_acp_mode should return 'execute' for regular task nodes."""
    from onemancompany.core.task_lifecycle import NodeType
    from onemancompany.core.vessel import EmployeeManager

    mgr = EmployeeManager()

    node = MagicMock()
    node.node_type = NodeType.TASK
    assert mgr._get_acp_mode(node) == "execute"


def test_get_acp_mode_none():
    """_get_acp_mode should return None for None node."""
    from onemancompany.core.vessel import EmployeeManager

    mgr = EmployeeManager()
    assert mgr._get_acp_mode(None) is None
