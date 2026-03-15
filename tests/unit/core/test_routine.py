"""Unit tests for core/routine.py — auto-trigger quarterly review."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from onemancompany.core.config import FOUNDING_LEVEL, TASKS_PER_QUARTER


# ---------------------------------------------------------------------------
# Auto-trigger HR review when employee hits TASKS_PER_QUARTER
# ---------------------------------------------------------------------------

class TestAutoTriggerHRReview:
    @pytest.mark.asyncio
    async def test_auto_triggers_review_at_threshold(self):
        """When current_quarter_tasks reaches TASKS_PER_QUARTER, HR review should be auto-triggered."""
        from onemancompany.core import routine as routine_mod

        emp_data = {
            "00010": {
                "name": "Test Dev", "level": 1,
                "current_quarter_tasks": TASKS_PER_QUARTER - 1,  # One task away
                "performance_history": [],
            },
        }

        with (
            patch.object(routine_mod, "load_all_employees", return_value=emp_data),
            patch.object(routine_mod, "load_employee", side_effect=lambda eid: emp_data.get(eid)),
            patch.object(routine_mod, "_store", MagicMock(save_employee=AsyncMock())),
            patch.object(routine_mod, "_publish", new_callable=AsyncMock),
            patch("onemancompany.core.routine._auto_trigger_hr_review") as mock_trigger,
        ):
            await routine_mod.run_post_task_routine(
                "Test task", participants=["00010"], project_id="proj1",
            )

        # Should have been called for employee 00010 who just hit the threshold
        mock_trigger.assert_called_once_with("00010")

    @pytest.mark.asyncio
    async def test_no_auto_trigger_below_threshold(self):
        """Should NOT trigger review when tasks < TASKS_PER_QUARTER."""
        from onemancompany.core import routine as routine_mod

        emp_data = {
            "00010": {
                "name": "Test Dev", "level": 1,
                "current_quarter_tasks": 0,  # Will be 1 after increment, still below 3
                "performance_history": [],
            },
        }

        with (
            patch.object(routine_mod, "load_all_employees", return_value=emp_data),
            patch.object(routine_mod, "load_employee", side_effect=lambda eid: emp_data.get(eid)),
            patch.object(routine_mod, "_store", MagicMock(save_employee=AsyncMock())),
            patch.object(routine_mod, "_publish", new_callable=AsyncMock),
            patch("onemancompany.core.routine._auto_trigger_hr_review") as mock_trigger,
        ):
            await routine_mod.run_post_task_routine(
                "Test task", participants=["00010"], project_id="proj1",
            )

        mock_trigger.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_auto_trigger_for_founding(self):
        """Founding employees (Lv.4+) should NOT trigger review."""
        from onemancompany.core import routine as routine_mod

        emp_data = {
            "00002": {
                "name": "HR", "level": FOUNDING_LEVEL,
                "current_quarter_tasks": TASKS_PER_QUARTER - 1,
                "performance_history": [],
            },
        }

        with (
            patch.object(routine_mod, "load_all_employees", return_value=emp_data),
            patch.object(routine_mod, "load_employee", side_effect=lambda eid: emp_data.get(eid)),
            patch.object(routine_mod, "_store", MagicMock(save_employee=AsyncMock())),
            patch.object(routine_mod, "_publish", new_callable=AsyncMock),
            patch("onemancompany.core.routine._auto_trigger_hr_review") as mock_trigger,
        ):
            await routine_mod.run_post_task_routine(
                "Test task", participants=["00002"], project_id="proj1",
            )

        mock_trigger.assert_not_called()
