"""Tests for idempotent CEO report confirmation and error cleanup."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def manager():
    """Create a minimal EmployeeManager-like object with required attributes."""
    from onemancompany.core.vessel import EmployeeManager

    mgr = MagicMock(spec=EmployeeManager)
    mgr._pending_ceo_reports = {}
    # Bind the real methods
    mgr._confirm_ceo_report = EmployeeManager._confirm_ceo_report.__get__(mgr)
    mgr._ceo_report_auto_confirm = EmployeeManager._ceo_report_auto_confirm.__get__(mgr)
    mgr._full_cleanup = AsyncMock()
    mgr.CEO_REPORT_CONFIRM_DELAY = 0
    return mgr


@pytest.mark.asyncio
async def test_confirm_returns_true_if_already_archived(manager):
    """No pending entry but project is archived on disk -> idempotent True."""
    assert "proj-001" not in manager._pending_ceo_reports

    with patch(
        "onemancompany.core.project_archive.load_named_project",
        return_value={"status": "archived"},
    ) as mock_load:
        result = await manager._confirm_ceo_report("proj-001")

    assert result is True
    mock_load.assert_called_once_with("proj-001")


@pytest.mark.asyncio
async def test_confirm_returns_true_archived_with_slash(manager):
    """Project ID with slash extracts base_pid correctly."""
    with patch(
        "onemancompany.core.project_archive.load_named_project",
        return_value={"status": "archived"},
    ) as mock_load:
        result = await manager._confirm_ceo_report("proj-001/iter-2")

    assert result is True
    mock_load.assert_called_once_with("proj-001")


@pytest.mark.asyncio
async def test_confirm_returns_false_if_no_pending_and_not_archived(manager):
    """No pending entry, project is active (not archived) -> False."""
    with patch(
        "onemancompany.core.project_archive.load_named_project",
        return_value={"status": "active"},
    ):
        result = await manager._confirm_ceo_report("proj-002")

    assert result is False


@pytest.mark.asyncio
async def test_confirm_returns_false_if_no_pending_and_no_project(manager):
    """No pending entry, project doesn't exist on disk -> False."""
    with patch(
        "onemancompany.core.project_archive.load_named_project",
        return_value=None,
    ):
        result = await manager._confirm_ceo_report("proj-999")

    assert result is False


@pytest.mark.asyncio
async def test_auto_confirm_exception_cleans_pending(manager):
    """If _confirm_ceo_report raises, _pending_ceo_reports is cleaned up."""
    manager._pending_ceo_reports["proj-err"] = {
        "timer_task": None,
        "cleanup_ctx": {},
    }
    # Make _confirm_ceo_report raise an exception
    manager._confirm_ceo_report = AsyncMock(side_effect=RuntimeError("boom"))

    await manager._ceo_report_auto_confirm("proj-err", {})

    assert "proj-err" not in manager._pending_ceo_reports


@pytest.mark.asyncio
async def test_auto_confirm_cancel_reraises(manager):
    """CancelledError during auto-confirm must be re-raised, not swallowed."""
    manager.CEO_REPORT_CONFIRM_DELAY = 999  # Long sleep, will be cancelled

    task = asyncio.create_task(manager._ceo_report_auto_confirm("proj-cancel", {}))
    await asyncio.sleep(0.01)  # Let the task start sleeping
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
