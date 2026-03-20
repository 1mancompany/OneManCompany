"""Unit tests for core/routine.py — auto-trigger quarterly review + CEO meetings."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from onemancompany.core.config import CEO_ID, FOUNDING_LEVEL, TASKS_PER_QUARTER


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


# ---------------------------------------------------------------------------
# CEO Meeting System
# ---------------------------------------------------------------------------

class TestCeoMeetingStart:
    @pytest.mark.asyncio
    async def test_start_all_hands(self):
        """start_ceo_meeting should book room and return participants."""
        from onemancompany.core import routine as routine_mod

        emp_data = {
            "00010": {"name": "Dev1", "level": 1, "nickname": "D1"},
            "00011": {"name": "Dev2", "level": 1, "nickname": "D2"},
        }
        mock_room = MagicMock(
            id="room1", name="Main Room", capacity=10,
            is_booked=False, booked_by="", participants=[],
        )
        mock_rooms = {"room1": mock_room}

        with (
            patch.object(routine_mod, "load_all_employees", return_value=emp_data),
            patch.object(routine_mod, "company_state", MagicMock(meeting_rooms=mock_rooms)),
            patch.object(routine_mod, "_store", MagicMock(save_room=AsyncMock())),
            patch.object(routine_mod, "_publish", new_callable=AsyncMock),
            patch.object(routine_mod, "_set_participants_status", new_callable=AsyncMock),
        ):
            result = await routine_mod.start_ceo_meeting("all_hands")

        assert result["type"] == "all_hands"
        assert result["status"] == "started"
        assert len(result["participants"]) == 2
        # Clean up
        routine_mod._active_ceo_meeting = None

    @pytest.mark.asyncio
    async def test_start_with_founding_only(self):
        """start_ceo_meeting should work with only founding employees (no regular hires)."""
        from onemancompany.core import routine as routine_mod
        from onemancompany.core.config import HR_ID, COO_ID, EA_ID, CSO_ID

        # Only founding employees — no regular hires
        emp_data = {
            HR_ID: {"name": "HR", "level": 1, "nickname": "HR"},
            COO_ID: {"name": "COO", "level": 1, "nickname": "COO"},
            EA_ID: {"name": "EA", "level": 1, "nickname": "EA"},
            CSO_ID: {"name": "CSO", "level": 1, "nickname": "CSO"},
        }
        mock_room = MagicMock(
            id="room1", name="Main Room", capacity=10,
            is_booked=False, booked_by="", participants=[],
        )

        with (
            patch.object(routine_mod, "load_all_employees", return_value=emp_data),
            patch.object(routine_mod, "company_state", MagicMock(meeting_rooms={"room1": mock_room})),
            patch.object(routine_mod, "_store", MagicMock(save_room=AsyncMock())),
            patch.object(routine_mod, "_publish", new_callable=AsyncMock),
            patch.object(routine_mod, "_set_participants_status", new_callable=AsyncMock),
        ):
            result = await routine_mod.start_ceo_meeting("discussion")

        assert result["status"] == "started"
        assert len(result["participants"]) == 4  # HR, COO, EA, CSO
        routine_mod._active_ceo_meeting = None

    @pytest.mark.asyncio
    async def test_start_returns_error_when_meeting_active(self):
        """Should return error dict if a CEO meeting is already in progress."""
        from onemancompany.core import routine as routine_mod

        routine_mod._active_ceo_meeting = {"type": "all_hands"}
        try:
            result = await routine_mod.start_ceo_meeting("discussion")
            assert "error" in result
        finally:
            routine_mod._active_ceo_meeting = None


class TestCeoMeetingChat:
    @pytest.mark.asyncio
    async def test_chat_no_active_meeting(self):
        """Should return error dict if no meeting is active."""
        from onemancompany.core import routine as routine_mod

        routine_mod._active_ceo_meeting = None
        result = await routine_mod.ceo_meeting_chat("hello")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_all_hands_chat_generates_responses(self):
        """All-hands chat: each employee absorbs CEO message and gives summary."""
        from onemancompany.core import routine as routine_mod

        routine_mod._active_ceo_meeting = {
            "type": "all_hands",
            "participants": ["00010"],
            "chat_history": [],
            "room_id": "room1",
        }

        mock_resp = MagicMock(content="Understood, will focus on quality.")
        emp_data = {
            "00010": {"name": "Dev1", "nickname": "D1", "level": 1,
                      "role": "Developer", "department": "Tech", "work_principles": ""},
        }

        with (
            patch.object(routine_mod, "_chat", new_callable=AsyncMock),
            patch.object(routine_mod, "_publish", new_callable=AsyncMock),
            patch.object(routine_mod, "load_all_employees", return_value=emp_data),
            patch.object(routine_mod, "make_llm", return_value=MagicMock()),
            patch.object(routine_mod, "tracked_ainvoke", new_callable=AsyncMock, return_value=mock_resp),
        ):
            try:
                result = await routine_mod.ceo_meeting_chat("Company update")
                assert len(result["responses"]) == 1
                assert result["responses"][0]["employee_id"] == "00010"
            finally:
                routine_mod._active_ceo_meeting = None


class TestCeoMeetingEnd:
    @pytest.mark.asyncio
    async def test_end_no_active_meeting(self):
        """Should return error dict if no meeting is active."""
        from onemancompany.core import routine as routine_mod

        routine_mod._active_ceo_meeting = None
        result = await routine_mod.end_ceo_meeting()
        assert "error" in result

    @pytest.mark.asyncio
    async def test_end_saves_guidance_and_extracts_action_points(self):
        """End meeting should save guidance notes and extract action points."""
        from onemancompany.core import routine as routine_mod

        routine_mod._active_ceo_meeting = {
            "type": "discussion",
            "room_id": "room1",
            "room_name": "Main Room",
            "participants": ["00010"],
            "chat_history": [
                {"speaker": "CEO", "message": "Let's improve testing"},
                {"speaker": "D1", "message": "I agree, I'll add more unit tests"},
            ],
        }

        emp_data = {
            "00010": {"name": "Dev1", "nickname": "D1", "level": 1,
                      "role": "Developer", "department": "Tech",
                      "work_principles": "Be thorough."},
        }

        mock_room = MagicMock(
            id="room1", name="Main Room",
            is_booked=True, booked_by="00001", participants=["00001", "00010"],
        )

        # LLM calls: reflection per employee + EA action points extraction
        reflection_resp = MagicMock(content="NO_UPDATE")
        ea_resp = MagicMock(content='["Improve unit test coverage"]')

        mock_store = MagicMock(
            load_employee_guidance=MagicMock(return_value=[]),
            save_guidance=AsyncMock(),
            save_work_principles=AsyncMock(),
            save_room=AsyncMock(),
        )

        with (
            patch.object(routine_mod, "load_all_employees", return_value=emp_data),
            patch.object(routine_mod, "_publish", new_callable=AsyncMock),
            patch.object(routine_mod, "_chat", new_callable=AsyncMock),
            patch.object(routine_mod, "_set_participants_status", new_callable=AsyncMock),
            patch.object(routine_mod, "_store", mock_store),
            patch.object(routine_mod, "company_state", MagicMock(meeting_rooms={"room1": mock_room})),
            patch.object(routine_mod, "make_llm", return_value=MagicMock()),
            patch.object(routine_mod, "tracked_ainvoke", new_callable=AsyncMock, side_effect=[
                reflection_resp,  # work principles reflection for emp 00010
                ea_resp,          # EA action point extraction
            ]),
            patch.object(routine_mod, "_save_report"),
            patch.object(routine_mod, "_create_project_from_action_points",
                         new_callable=AsyncMock, return_value="proj-123"),
        ):
            result = await routine_mod.end_ceo_meeting()

        assert result["status"] == "ended"
        assert result["action_points"] == ["Improve unit test coverage"]
        assert result["project_id"] == "proj-123"
        assert routine_mod._active_ceo_meeting is None
