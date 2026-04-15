"""Unit tests for core/routine.py — full coverage."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, mock_open

import pytest

from onemancompany.core.config import (
    CEO_ID,
    COO_ID,
    EA_ID,
    HR_ID,
    FOUNDING_LEVEL,
    TASKS_PER_QUARTER,
)
from onemancompany.core.workflow_engine import WorkflowStep, WorkflowDefinition


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------

def _make_step(title: str = "Test Step", owner: str = "HR", index: int = 0,
               instructions: list[str] | None = None,
               output_description: str = "output") -> WorkflowStep:
    return WorkflowStep(
        index=index,
        title=title,
        owner=owner,
        instructions=instructions or [],
        output_description=output_description,
        raw_text="raw",
        depends_on=[],
    )


def _make_workflow(steps: list[WorkflowStep] | None = None,
                   name: str = "test_wf",
                   flow_id: str = "test_flow") -> WorkflowDefinition:
    return WorkflowDefinition(
        name=name, flow_id=flow_id, owner="HR",
        collaborators="", trigger="",
        steps=steps or [],
    )


def _emp(name: str = "Dev1", nickname: str = "D1", level: int = 1,
         role: str = "Developer", department: str = "Tech",
         work_principles: str = "", current_quarter_tasks: int = 0,
         performance_history: list | None = None) -> dict:
    return {
        "name": name, "nickname": nickname, "level": level,
        "role": role, "department": department,
        "work_principles": work_principles,
        "current_quarter_tasks": current_quarter_tasks,
        "performance_history": performance_history or [],
    }


def _mock_store(**overrides):
    defaults = dict(
        save_employee=AsyncMock(),
        save_employee_runtime=AsyncMock(),
        save_room=AsyncMock(),
        save_guidance=AsyncMock(),
        save_work_principles=AsyncMock(),
        load_employee_guidance=MagicMock(return_value=[]),
        load_culture=MagicMock(return_value=[]),
        load_rooms=MagicMock(return_value={}),
    )
    defaults.update(overrides)
    return MagicMock(**defaults)


def _mock_room(room_id: str = "room1", is_booked: bool = False):
    return MagicMock(
        id=room_id, name="Main Room", capacity=10,
        is_booked=is_booked, booked_by="", participants=[],
    )


def _llm_resp(content: str = "OK"):
    return MagicMock(content=content)


# ---------------------------------------------------------------------------
# Helpers: _format_workflow_context, _parse_json_array
# ---------------------------------------------------------------------------

class TestFormatWorkflowContext:
    def test_empty_instructions(self):
        from onemancompany.core.routine import _format_workflow_context
        step = _make_step(instructions=[])
        assert _format_workflow_context(step) == ""

    def test_with_instructions(self):
        from onemancompany.core.routine import _format_workflow_context
        step = _make_step(instructions=["Do A", "Do B"])
        result = _format_workflow_context(step)
        assert "[Workflow Requirements" in result
        assert "1. Do A" in result
        assert "2. Do B" in result


class TestParseJsonArray:
    def test_valid_json_array(self):
        from onemancompany.core.routine import _parse_json_array
        assert _parse_json_array('here is [1, 2, 3] done') == [1, 2, 3]

    def test_invalid_json_fallback(self):
        from onemancompany.core.routine import _parse_json_array
        assert _parse_json_array("no array here", ["default"]) == ["default"]

    def test_malformed_json(self):
        from onemancompany.core.routine import _parse_json_array
        # Regex matches [invalid json] but json.loads fails
        assert _parse_json_array("here [invalid, json] there", ["fb"]) == ["fb"]

    def test_no_brackets(self):
        from onemancompany.core.routine import _parse_json_array
        assert _parse_json_array("just text") == []

    def test_default_fallback_none(self):
        from onemancompany.core.routine import _parse_json_array
        assert _parse_json_array("no json") == []


# ---------------------------------------------------------------------------
# _set_participants_status
# ---------------------------------------------------------------------------

class TestSetParticipantsStatus:
    @pytest.mark.asyncio
    async def test_sets_status_for_all(self):
        from onemancompany.core import routine as mod
        mock_st = _mock_store()
        with patch.object(mod, "_store", mock_st):
            await mod._set_participants_status(["00010", "00011"], "idle")
        assert mock_st.save_employee_runtime.call_count == 2


# ---------------------------------------------------------------------------
# _publish and _chat
# ---------------------------------------------------------------------------

class TestPublish:
    @pytest.mark.asyncio
    async def test_publish_fires_event(self):
        from onemancompany.core import routine as mod
        mock_bus = AsyncMock()
        with patch.object(mod, "event_bus", mock_bus):
            await mod._publish(mod.EventType.ROUTINE_PHASE, {"phase": "test"})
        mock_bus.publish.assert_called_once()


class TestChat:
    @pytest.mark.asyncio
    async def test_chat_publishes_and_persists(self):
        from onemancompany.core import routine as mod
        mock_bus = AsyncMock()
        mock_append = AsyncMock()
        with (
            patch.object(mod, "event_bus", mock_bus),
            patch("onemancompany.core.store.append_room_chat", mock_append),
        ):
            await mod._chat("room1", "CEO", "CEO", "hello")
        mock_bus.publish.assert_called_once()
        mock_append.assert_called_once()

    @pytest.mark.asyncio
    async def test_chat_converts_non_string(self):
        from onemancompany.core import routine as mod
        mock_bus = AsyncMock()
        mock_append = AsyncMock()
        with (
            patch.object(mod, "event_bus", mock_bus),
            patch("onemancompany.core.store.append_room_chat", mock_append),
        ):
            await mod._chat("room1", "CEO", "CEO", ["list", "msg"])
        # Should not crash — non-string converted to str
        mock_bus.publish.assert_called_once()


# ---------------------------------------------------------------------------
# StepContext
# ---------------------------------------------------------------------------

class TestStepContext:
    def test_init_defaults(self):
        from onemancompany.core.routine import StepContext
        wf = _make_workflow()
        ctx = StepContext("summary", ["00010"], "room1", wf, {})
        assert ctx.task_summary == "summary"
        assert ctx.participants == ["00010"]
        assert ctx.self_evaluations == []
        assert ctx.project_record == {}

    def test_init_with_project_record(self):
        from onemancompany.core.routine import StepContext
        wf = _make_workflow()
        ctx = StepContext("summary", [], "room1", wf, {}, project_record={"id": "proj1"})
        assert ctx.project_record["id"] == "proj1"

    def test_format_project_timeline_empty(self):
        from onemancompany.core.routine import StepContext
        wf = _make_workflow()
        ctx = StepContext("summary", [], "room1", wf, {})
        assert ctx.format_project_timeline() == ""

    def test_format_project_timeline_with_entries(self):
        from onemancompany.core.routine import StepContext
        from onemancompany.core import routine as mod
        wf = _make_workflow()
        timeline = [
            {"employee_id": "00010", "action": "commit", "detail": "Fixed bug"},
        ]
        ctx = StepContext("summary", [], "room1", wf, {}, project_record={"timeline": timeline})
        emp = _emp()
        with patch.object(mod, "load_employee", return_value=emp):
            result = ctx.format_project_timeline()
        assert "commit" in result
        assert "Fixed bug" in result

    def test_format_project_timeline_no_employee(self):
        from onemancompany.core.routine import StepContext
        from onemancompany.core import routine as mod
        wf = _make_workflow()
        timeline = [{"employee_id": "99999", "action": "x", "detail": "y"}]
        ctx = StepContext("s", [], "r", wf, {}, project_record={"timeline": timeline})
        with patch.object(mod, "load_employee", return_value=None):
            result = ctx.format_project_timeline()
        assert "99999" in result

    def test_format_company_culture_empty(self):
        from onemancompany.core.routine import StepContext
        from onemancompany.core import routine as mod
        wf = _make_workflow()
        ctx = StepContext("s", [], "r", wf, {})
        with patch.object(mod, "_store", _mock_store(load_culture=MagicMock(return_value=[]))):
            assert ctx.format_company_culture() == ""

    def test_format_company_culture_with_items(self):
        from onemancompany.core.routine import StepContext
        from onemancompany.core import routine as mod
        wf = _make_workflow()
        ctx = StepContext("s", [], "r", wf, {})
        culture = [{"content": "Rule 1"}, {"content": "Rule 2"}]
        with patch.object(mod, "_store", _mock_store(load_culture=MagicMock(return_value=culture))):
            result = ctx.format_company_culture()
        assert "Rule 1" in result
        assert "Company Culture" in result

    def test_get_employee_actions_no_timeline(self):
        from onemancompany.core.routine import StepContext
        wf = _make_workflow()
        ctx = StepContext("s", [], "r", wf, {})
        assert "No action records" in ctx.get_employee_actions("00010")

    def test_get_employee_actions_no_match(self):
        from onemancompany.core.routine import StepContext
        wf = _make_workflow()
        ctx = StepContext("s", [], "r", wf, {},
                          project_record={"timeline": [{"employee_id": "00011", "action": "x", "detail": "y"}]})
        assert "No action records" in ctx.get_employee_actions("00010")

    def test_get_employee_actions_with_match(self):
        from onemancompany.core.routine import StepContext
        wf = _make_workflow()
        ctx = StepContext("s", [], "r", wf, {},
                          project_record={"timeline": [{"employee_id": "00010", "action": "commit", "detail": "did stuff"}]})
        result = ctx.get_employee_actions("00010")
        assert "commit" in result


# ---------------------------------------------------------------------------
# Handler registration and resolution
# ---------------------------------------------------------------------------

class TestHandlerRegistration:
    def test_title_handlers_registered(self):
        from onemancompany.core.routine import _STEP_HANDLERS_BY_TITLE
        assert "Self-Evaluation" in _STEP_HANDLERS_BY_TITLE
        assert "Action Plan" in _STEP_HANDLERS_BY_TITLE
        assert "EA Approval" in _STEP_HANDLERS_BY_TITLE

    def test_owner_handlers_registered(self):
        from onemancompany.core.routine import _STEP_HANDLERS_BY_OWNER
        assert "employees" in _STEP_HANDLERS_BY_OWNER
        assert "senior" in _STEP_HANDLERS_BY_OWNER
        assert "coo_hr" in _STEP_HANDLERS_BY_OWNER
        assert "ceo" in _STEP_HANDLERS_BY_OWNER


class TestResolveHandler:
    def test_title_match(self):
        from onemancompany.core.routine import _resolve_handler, _handle_self_evaluation
        step = _make_step(title="Employee Self-Evaluation")
        assert _resolve_handler(step) is _handle_self_evaluation

    def test_owner_fallback(self):
        from onemancompany.core.routine import _resolve_handler, _handle_self_evaluation
        step = _make_step(title="Unknown Title", owner="Each participating employee")
        with patch("onemancompany.core.routine.classify_step_owner", return_value="employees"):
            handler = _resolve_handler(step)
        assert handler is _handle_self_evaluation

    def test_generic_fallback(self):
        from onemancompany.core.routine import _resolve_handler, _handle_generic_step
        step = _make_step(title="Random Thing", owner="Nobody")
        with patch("onemancompany.core.routine.classify_step_owner", return_value="unknown"):
            handler = _resolve_handler(step)
        assert handler is _handle_generic_step


# ---------------------------------------------------------------------------
# Individual step handlers
# ---------------------------------------------------------------------------

class TestHandleMeetingPrep:
    @pytest.mark.asyncio
    async def test_meeting_prep(self):
        from onemancompany.core import routine as mod
        step = _make_step(title="Review Preparation")
        ctx = MagicMock()
        with patch.object(mod, "_publish", new_callable=AsyncMock):
            result = await mod._handle_meeting_prep(step, ctx)
        assert result["status"] == "prepared"


class TestHandleSelfEvaluation:
    @pytest.mark.asyncio
    async def test_self_evaluation_basic(self):
        from onemancompany.core import routine as mod
        step = _make_step(title="Self-Evaluation", instructions=["Evaluate honestly"])
        wf = _make_workflow()
        ctx = mod.StepContext("task summary", ["00010"], "room1", wf, {})

        emp = _emp(work_principles="Be honest")
        with (
            patch.object(mod, "load_employee", return_value=emp),
            patch.object(mod, "make_llm", return_value=MagicMock()),
            patch.object(mod, "tracked_ainvoke", new_callable=AsyncMock, return_value=_llm_resp("I did well")),
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_chat", new_callable=AsyncMock),
            patch.object(mod, "get_employee_skills_prompt", return_value=""),
            patch.object(mod, "get_employee_tools_prompt", return_value=""),
            patch.object(mod, "_store", _mock_store()),
        ):
            result = await mod._handle_self_evaluation(step, ctx)

        assert "self_evaluations" in result
        assert len(ctx.self_evaluations) == 1

    @pytest.mark.asyncio
    async def test_self_evaluation_with_timeline(self):
        from onemancompany.core import routine as mod
        step = _make_step(title="Self-Evaluation")
        wf = _make_workflow()
        timeline = [{"employee_id": "00010", "action": "commit", "detail": "Fixed bug"}]
        ctx = mod.StepContext("task", ["00010"], "room1", wf, {},
                              project_record={"timeline": timeline})

        emp = _emp()
        with (
            patch.object(mod, "load_employee", return_value=emp),
            patch.object(mod, "make_llm", return_value=MagicMock()),
            patch.object(mod, "tracked_ainvoke", new_callable=AsyncMock, return_value=_llm_resp("I fixed the bug")),
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_chat", new_callable=AsyncMock),
            patch.object(mod, "get_employee_skills_prompt", return_value=""),
            patch.object(mod, "get_employee_tools_prompt", return_value=""),
            patch.object(mod, "_store", _mock_store()),
        ):
            result = await mod._handle_self_evaluation(step, ctx)
        assert len(ctx.self_evaluations) == 1

    @pytest.mark.asyncio
    async def test_self_evaluation_skips_missing_employee(self):
        from onemancompany.core import routine as mod
        step = _make_step(title="Self-Evaluation")
        wf = _make_workflow()
        ctx = mod.StepContext("task", ["99999"], "room1", wf, {})

        with (
            patch.object(mod, "load_employee", return_value=None),
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_chat", new_callable=AsyncMock),
        ):
            result = await mod._handle_self_evaluation(step, ctx)
        assert result["self_evaluations"] == []


class TestHandleSeniorReview:
    @pytest.mark.asyncio
    async def test_senior_review_with_juniors(self):
        from onemancompany.core import routine as mod
        step = _make_step(title="Senior Peer Review")
        wf = _make_workflow()
        ctx = mod.StepContext("task", ["00010", "00011"], "room1", wf, {})
        ctx.self_evaluations = [
            {"employee_id": "00011", "name": "Junior", "nickname": "J", "level": 1, "evaluation": "I tried"},
        ]

        senior = _emp(name="Senior", nickname="S", level=3, role="Lead")
        junior = _emp(name="Junior", nickname="J", level=1)

        def _load(eid):
            return senior if eid == "00010" else junior

        review_json = '[{"name": "Junior", "review": "Good work"}]'

        with (
            patch.object(mod, "load_employee", side_effect=_load),
            patch.object(mod, "make_llm", return_value=MagicMock()),
            patch.object(mod, "tracked_ainvoke", new_callable=AsyncMock, return_value=_llm_resp(review_json)),
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_chat", new_callable=AsyncMock),
            patch.object(mod, "_store", _mock_store()),
        ):
            result = await mod._handle_senior_review(step, ctx)

        assert "senior_reviews" in result
        assert len(ctx.senior_reviews) >= 1

    @pytest.mark.asyncio
    async def test_senior_review_no_juniors(self):
        from onemancompany.core import routine as mod
        step = _make_step(title="Senior Peer Review")
        wf = _make_workflow()
        ctx = mod.StepContext("task", ["00010"], "room1", wf, {})

        emp = _emp(level=1)
        with (
            patch.object(mod, "load_employee", return_value=emp),
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_chat", new_callable=AsyncMock),
        ):
            result = await mod._handle_senior_review(step, ctx)
        assert result["senior_reviews"] == []


class TestHandleHrSummary:
    @pytest.mark.asyncio
    async def test_hr_summary(self):
        from onemancompany.core import routine as mod
        step = _make_step(title="HR Summary", instructions=["Summarize"])
        wf = _make_workflow()
        ctx = mod.StepContext("task", ["00010"], "room1", wf, {})
        ctx.self_evaluations = [{"name": "Dev1", "level": 1, "evaluation": "Good"}]
        ctx.senior_reviews = [{"reviewer": "Lead", "reviews": [{"name": "Dev1", "review": "OK"}]}]

        hr_json = '[{"employee": "Dev1", "improvements": ["improve X"]}]'
        with (
            patch.object(mod, "make_llm", return_value=MagicMock()),
            patch.object(mod, "tracked_ainvoke", new_callable=AsyncMock, return_value=_llm_resp(hr_json)),
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_chat", new_callable=AsyncMock),
            patch.object(mod, "_store", _mock_store()),
        ):
            result = await mod._handle_hr_summary(step, ctx)

        assert "hr_summary" in result
        assert len(ctx.hr_summary) == 1


class TestHandleCooReport:
    @pytest.mark.asyncio
    async def test_coo_report_no_cost(self):
        from onemancompany.core import routine as mod
        step = _make_step(title="COO Operations Report")
        wf = _make_workflow()
        ctx = mod.StepContext("task", [], "room1", wf, {})

        with (
            patch.object(mod, "load_all_employees", return_value={"00010": {}}),
            patch.object(mod, "company_state", MagicMock(tools={})),
            patch.object(mod, "_store", _mock_store(load_rooms=MagicMock(return_value={}))),
            patch.object(mod, "make_llm", return_value=MagicMock()),
            patch.object(mod, "tracked_ainvoke", new_callable=AsyncMock, return_value=_llm_resp("Report text")),
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_chat", new_callable=AsyncMock),
        ):
            result = await mod._handle_coo_report(step, ctx)

        assert result["coo_report"] == "Report text"
        assert ctx.coo_report == "Report text"

    @pytest.mark.asyncio
    async def test_coo_report_with_cost(self):
        from onemancompany.core import routine as mod
        step = _make_step(title="COO Operations Report")
        wf = _make_workflow()
        cost_data = {
            "actual_cost_usd": 0.5,
            "budget_estimate_usd": 1.0,
            "token_usage": {"input": 100, "output": 50},
            "breakdown": [{"employee_id": "00010", "model": "gpt-4", "total_tokens": 150, "cost_usd": 0.5}],
        }
        ctx = mod.StepContext("task", [], "room1", wf, {},
                              project_record={"cost": cost_data})

        emp = _emp()
        with (
            patch.object(mod, "load_all_employees", return_value={"00010": emp}),
            patch.object(mod, "load_employee", return_value=emp),
            patch.object(mod, "company_state", MagicMock(tools={})),
            patch.object(mod, "_store", _mock_store(load_rooms=MagicMock(return_value={}))),
            patch.object(mod, "make_llm", return_value=MagicMock()),
            patch.object(mod, "tracked_ainvoke", new_callable=AsyncMock, return_value=_llm_resp("Cost report")),
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_chat", new_callable=AsyncMock),
        ):
            result = await mod._handle_coo_report(step, ctx)
        assert result["coo_report"] == "Cost report"


class TestHandleAssetConsolidation:
    @pytest.mark.asyncio
    async def test_no_project_id(self):
        from onemancompany.core import routine as mod
        step = _make_step(title="Asset Consolidation")
        wf = _make_workflow()
        ctx = mod.StepContext("task", [], "room1", wf, {})
        with (
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_chat", new_callable=AsyncMock),
        ):
            result = await mod._handle_asset_consolidation(step, ctx)
        assert result["asset_suggestions"] == []

    @pytest.mark.asyncio
    async def test_no_files(self):
        from onemancompany.core import routine as mod
        step = _make_step(title="Asset Consolidation")
        wf = _make_workflow()
        ctx = mod.StepContext("task", [], "room1", wf, {}, project_record={"id": "proj1"})
        with (
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_chat", new_callable=AsyncMock),
            patch("onemancompany.core.project_archive.list_project_files", return_value=[]),
            patch("onemancompany.core.project_archive.get_project_dir", return_value="/tmp/proj"),
        ):
            result = await mod._handle_asset_consolidation(step, ctx)
        assert result["asset_suggestions"] == []

    @pytest.mark.asyncio
    async def test_with_files_and_suggestions(self):
        from onemancompany.core import routine as mod
        step = _make_step(title="Asset Consolidation")
        wf = _make_workflow()
        ctx = mod.StepContext("task", [], "room1", wf, {}, project_record={"id": "proj1"})

        suggestions_json = '[{"name": "script.py", "description": "Useful tool", "files": ["script.py"]}]'
        with (
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_chat", new_callable=AsyncMock),
            patch("onemancompany.core.project_archive.list_project_files", return_value=["script.py", "README.md"]),
            patch("onemancompany.core.project_archive.get_project_dir", return_value="/tmp/proj"),
            patch.object(mod, "make_llm", return_value=MagicMock()),
            patch.object(mod, "tracked_ainvoke", new_callable=AsyncMock, return_value=_llm_resp(suggestions_json)),
        ):
            result = await mod._handle_asset_consolidation(step, ctx)
        assert len(result["asset_suggestions"]) == 1
        assert ctx.asset_suggestions[0]["name"] == "script.py"

    @pytest.mark.asyncio
    async def test_with_files_no_suggestions(self):
        from onemancompany.core import routine as mod
        step = _make_step(title="Asset Consolidation")
        wf = _make_workflow()
        ctx = mod.StepContext("task", [], "room1", wf, {}, project_record={"id": "proj1"})

        with (
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_chat", new_callable=AsyncMock),
            patch("onemancompany.core.project_archive.list_project_files", return_value=["temp.log"]),
            patch("onemancompany.core.project_archive.get_project_dir", return_value="/tmp/proj"),
            patch.object(mod, "make_llm", return_value=MagicMock()),
            patch.object(mod, "tracked_ainvoke", new_callable=AsyncMock, return_value=_llm_resp("[]")),
        ):
            result = await mod._handle_asset_consolidation(step, ctx)
        assert result["asset_suggestions"] == []


class TestHandleEmployeeOpenFloor:
    @pytest.mark.asyncio
    async def test_open_floor(self):
        from onemancompany.core import routine as mod
        step = _make_step(title="Employee Open Floor", instructions=["Speak freely"])
        wf = _make_workflow()
        ctx = mod.StepContext("task", ["00010"], "room1", wf, {})

        emp = _emp(work_principles="Be honest")
        with (
            patch.object(mod, "load_employee", return_value=emp),
            patch.object(mod, "make_llm", return_value=MagicMock()),
            patch.object(mod, "tracked_ainvoke", new_callable=AsyncMock, return_value=_llm_resp("Need more tools")),
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_chat", new_callable=AsyncMock),
            patch.object(mod, "get_employee_skills_prompt", return_value=""),
            patch.object(mod, "get_employee_tools_prompt", return_value=""),
            patch.object(mod, "_store", _mock_store()),
        ):
            result = await mod._handle_employee_open_floor(step, ctx)

        assert "employee_feedback" in result
        assert len(ctx.employee_feedback) == 1

    @pytest.mark.asyncio
    async def test_open_floor_skips_missing(self):
        from onemancompany.core import routine as mod
        step = _make_step(title="Open Floor")
        wf = _make_workflow()
        ctx = mod.StepContext("task", ["99999"], "room1", wf, {})
        with (
            patch.object(mod, "load_employee", return_value=None),
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_chat", new_callable=AsyncMock),
        ):
            result = await mod._handle_employee_open_floor(step, ctx)
        assert result["employee_feedback"] == []


class TestHandleActionPlan:
    @pytest.mark.asyncio
    async def test_action_plan_basic(self):
        from onemancompany.core import routine as mod
        step = _make_step(title="Action Plan")
        wf = _make_workflow()
        ctx = mod.StepContext("task", [], "room1", wf, {})
        ctx.employee_feedback = [{"name": "Dev1", "feedback": "Need more tools"}]
        ctx.hr_summary = [{"employee": "Dev1", "improvements": ["learn more"]}]
        ctx.coo_report = "All good"

        action_json = '[{"source": "HR", "description": "Hire intern", "priority": "high"}]'
        with (
            patch.object(mod, "make_llm", return_value=MagicMock()),
            patch.object(mod, "tracked_ainvoke", new_callable=AsyncMock, return_value=_llm_resp(action_json)),
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_chat", new_callable=AsyncMock),
        ):
            result = await mod._handle_action_plan(step, ctx)

        assert "action_items" in result
        assert len(ctx.action_items) == 1

    @pytest.mark.asyncio
    async def test_action_plan_with_asset_suggestions(self):
        from onemancompany.core import routine as mod
        step = _make_step(title="Action Plan")
        wf = _make_workflow()
        ctx = mod.StepContext("task", [], "room1", wf, {},
                              project_record={"id": "proj1"})
        ctx.employee_feedback = []
        ctx.hr_summary = []
        ctx.coo_report = "OK"
        ctx.asset_suggestions = [{"name": "tool.py", "description": "Useful", "files": ["tool.py"]}]

        action_json = '[{"source": "COO", "description": "Do something", "priority": "medium"}]'
        with (
            patch.object(mod, "make_llm", return_value=MagicMock()),
            patch.object(mod, "tracked_ainvoke", new_callable=AsyncMock, return_value=_llm_resp(action_json)),
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_chat", new_callable=AsyncMock),
            patch("onemancompany.core.project_archive.get_project_dir", return_value="/tmp/proj"),
        ):
            result = await mod._handle_action_plan(step, ctx)

        # Should merge asset suggestion as an action item
        assert len(ctx.action_items) == 2
        assert ctx.action_items[1]["type"] == "asset_consolidation"


class TestHandleEaApproval:
    @pytest.mark.asyncio
    async def test_no_action_items(self):
        from onemancompany.core import routine as mod
        step = _make_step(title="EA Approval")
        wf = _make_workflow()
        ctx = mod.StepContext("task", [], "room1", wf, {})
        ctx.action_items = []

        with (
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_chat", new_callable=AsyncMock),
        ):
            result = await mod._handle_ea_approval(step, ctx)
        assert result["status"] == "no_actions"

    @pytest.mark.asyncio
    async def test_all_duplicates(self):
        from onemancompany.core import routine as mod
        step = _make_step(title="EA Approval")
        wf = _make_workflow()
        ctx = mod.StepContext("task", [], "room1", wf, {})
        ctx.action_items = [{"description": "improve testing"}]

        # All items are duplicates
        with (
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_chat", new_callable=AsyncMock),
            patch.object(mod, "_dedup_action_items", return_value=([], [{"description": "dup"}], [])),
        ):
            result = await mod._handle_ea_approval(step, ctx)
        assert result["status"] == "all_duplicates"

    @pytest.mark.asyncio
    async def test_ea_approves_some(self):
        from onemancompany.core import routine as mod
        step = _make_step(title="EA Approval")
        wf = _make_workflow()
        ctx = mod.StepContext("task", [], "room1", wf, {})
        ctx.action_items = [
            {"source": "HR", "description": "Hire intern", "priority": "high"},
            {"source": "COO", "description": "Vague improvement", "priority": "low"},
        ]
        ctx.coo_report = "OK"

        ea_json = '{"approved_indices": [0], "rejected_indices": [1], "reason": "Second is vague"}'
        mock_coo_loop = MagicMock()
        with (
            patch.object(mod, "_dedup_action_items", return_value=(ctx.action_items, [], [])),
            patch.object(mod, "make_llm", return_value=MagicMock()),
            patch.object(mod, "tracked_ainvoke", new_callable=AsyncMock, return_value=_llm_resp(ea_json)),
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_chat", new_callable=AsyncMock),
            patch("onemancompany.core.agent_loop.get_agent_loop", return_value=mock_coo_loop),
        ):
            result = await mod._handle_ea_approval(step, ctx)

        assert result["status"] == "ea_approved"
        assert len(result["approved"]) == 1
        assert len(result["rejected"]) == 1
        mock_coo_loop.push_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_ea_no_json_defaults_approve_all(self):
        from onemancompany.core import routine as mod
        step = _make_step(title="EA Approval")
        wf = _make_workflow()
        ctx = mod.StepContext("task", [], "room1", wf, {})
        ctx.action_items = [{"source": "COO", "description": "Action", "priority": "high"}]
        ctx.coo_report = "OK"

        mock_coo_loop = MagicMock()
        with (
            patch.object(mod, "_dedup_action_items", return_value=(ctx.action_items, [], [])),
            patch.object(mod, "make_llm", return_value=MagicMock()),
            patch.object(mod, "tracked_ainvoke", new_callable=AsyncMock, return_value=_llm_resp("No JSON here")),
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_chat", new_callable=AsyncMock),
            patch("onemancompany.core.agent_loop.get_agent_loop", return_value=mock_coo_loop),
        ):
            result = await mod._handle_ea_approval(step, ctx)
        assert result["status"] == "ea_approved"
        assert len(result["approved"]) == 1

    @pytest.mark.asyncio
    async def test_ea_json_decode_error_defaults_approve_all(self):
        from onemancompany.core import routine as mod
        step = _make_step(title="EA Approval")
        wf = _make_workflow()
        ctx = mod.StepContext("task", [], "room1", wf, {})
        ctx.action_items = [{"source": "COO", "description": "Do X", "priority": "high"}]
        ctx.coo_report = "OK"

        mock_coo_loop = MagicMock()
        with (
            patch.object(mod, "_dedup_action_items", return_value=(ctx.action_items, [], [])),
            patch.object(mod, "make_llm", return_value=MagicMock()),
            patch.object(mod, "tracked_ainvoke", new_callable=AsyncMock, return_value=_llm_resp("{invalid json}")),
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_chat", new_callable=AsyncMock),
            patch("onemancompany.core.agent_loop.get_agent_loop", return_value=mock_coo_loop),
        ):
            result = await mod._handle_ea_approval(step, ctx)
        assert result["status"] == "ea_approved"

    @pytest.mark.asyncio
    async def test_ea_with_recurring_and_dup_items(self):
        from onemancompany.core import routine as mod
        step = _make_step(title="EA Approval")
        wf = _make_workflow()
        ctx = mod.StepContext("task", [], "room1", wf, {})
        ctx.action_items = [
            {"source": "HR", "description": "Recurring issue", "priority": "high"},
            {"source": "COO", "description": "Duplicate item", "priority": "low"},
            {"source": "COO", "description": "Unique item", "priority": "medium"},
        ]
        ctx.coo_report = "OK"

        unique = [{"source": "COO", "description": "Unique item", "priority": "medium"}]
        dups = [{"description": "Duplicate item"}]
        recurring = [{"description": "Recurring issue"}]

        ea_json = '{"approved_indices": [0], "rejected_indices": [], "reason": "OK"}'
        mock_coo_loop = MagicMock()
        with (
            patch.object(mod, "_dedup_action_items", return_value=(unique, dups, recurring)),
            patch.object(mod, "make_llm", return_value=MagicMock()),
            patch.object(mod, "tracked_ainvoke", new_callable=AsyncMock, return_value=_llm_resp(ea_json)),
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_chat", new_callable=AsyncMock),
            patch("onemancompany.core.agent_loop.get_agent_loop", return_value=mock_coo_loop),
        ):
            result = await mod._handle_ea_approval(step, ctx)
        assert "recurring_escalated" in result

    @pytest.mark.asyncio
    async def test_ea_none_approved(self):
        from onemancompany.core import routine as mod
        step = _make_step(title="EA Approval")
        wf = _make_workflow()
        ctx = mod.StepContext("task", [], "room1", wf, {})
        ctx.action_items = [{"source": "COO", "description": "Bad", "priority": "low"}]
        ctx.coo_report = "OK"

        ea_json = '{"approved_indices": [], "rejected_indices": [0], "reason": "All vague"}'
        with (
            patch.object(mod, "_dedup_action_items", return_value=(ctx.action_items, [], [])),
            patch.object(mod, "make_llm", return_value=MagicMock()),
            patch.object(mod, "tracked_ainvoke", new_callable=AsyncMock, return_value=_llm_resp(ea_json)),
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_chat", new_callable=AsyncMock),
        ):
            result = await mod._handle_ea_approval(step, ctx)
        assert result["status"] == "none_approved"

    @pytest.mark.asyncio
    async def test_ea_with_asset_consolidation_action(self):
        from onemancompany.core import routine as mod
        step = _make_step(title="EA Approval")
        wf = _make_workflow()
        ctx = mod.StepContext("task", [], "room1", wf, {})
        ctx.action_items = [
            {"type": "asset_consolidation", "source": "COO", "description": "Asset",
             "priority": "medium", "name": "tool", "asset_description": "desc",
             "project_dir": "/tmp", "files": ["x.py"]},
        ]
        ctx.coo_report = "OK"

        ea_json = '{"approved_indices": [0], "rejected_indices": [], "reason": "OK"}'
        with (
            patch.object(mod, "_dedup_action_items", return_value=(ctx.action_items, [], [])),
            patch.object(mod, "make_llm", return_value=MagicMock()),
            patch.object(mod, "tracked_ainvoke", new_callable=AsyncMock, return_value=_llm_resp(ea_json)),
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_chat", new_callable=AsyncMock),
            patch("onemancompany.agents.coo_agent.register_asset", MagicMock(invoke=MagicMock(return_value="registered"))),
        ):
            result = await mod._handle_ea_approval(step, ctx)
        assert result["status"] == "ea_approved"
        assert len(result["asset_results"]) == 1

    @pytest.mark.asyncio
    async def test_ea_coo_loop_not_found(self):
        from onemancompany.core import routine as mod
        step = _make_step(title="EA Approval")
        wf = _make_workflow()
        ctx = mod.StepContext("task", [], "room1", wf, {})
        ctx.action_items = [{"source": "COO", "description": "Do X", "priority": "high"}]
        ctx.coo_report = "OK"

        ea_json = '{"approved_indices": [0], "rejected_indices": [], "reason": "OK"}'
        with (
            patch.object(mod, "_dedup_action_items", return_value=(ctx.action_items, [], [])),
            patch.object(mod, "make_llm", return_value=MagicMock()),
            patch.object(mod, "tracked_ainvoke", new_callable=AsyncMock, return_value=_llm_resp(ea_json)),
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_chat", new_callable=AsyncMock),
            patch("onemancompany.core.agent_loop.get_agent_loop", return_value=None),
        ):
            result = await mod._handle_ea_approval(step, ctx)
        assert result["status"] == "ea_approved"


class TestHandleGenericStep:
    @pytest.mark.asyncio
    async def test_generic_step(self):
        from onemancompany.core import routine as mod
        step = _make_step(title="Custom Step", owner="Someone", instructions=["Do it"])

        ctx = MagicMock(task_summary="task")
        with (
            patch.object(mod, "make_llm", return_value=MagicMock()),
            patch.object(mod, "tracked_ainvoke", new_callable=AsyncMock, return_value=_llm_resp("Done")),
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_chat", new_callable=AsyncMock),
        ):
            result = await mod._handle_generic_step(step, ctx)
        assert result["generic_output"] == "Done"


# ---------------------------------------------------------------------------
# _run_workflow
# ---------------------------------------------------------------------------

class TestRunWorkflow:
    @pytest.mark.asyncio
    async def test_run_workflow_executes_steps(self):
        from onemancompany.core import routine as mod
        step1 = _make_step(title="Review Preparation", index=0)
        step2 = _make_step(title="Self-Evaluation", index=1)
        wf = _make_workflow(steps=[step1, step2])
        ctx = MagicMock()
        ctx.results = {}

        mock_handler = AsyncMock(return_value={"status": "ok"})
        with (
            patch.object(mod, "_resolve_handler", return_value=mock_handler),
            patch.object(mod, "_publish", new_callable=AsyncMock),
        ):
            result = await mod._run_workflow(wf, ctx)
        assert len(result) == 2
        assert mock_handler.call_count == 2


# ---------------------------------------------------------------------------
# _auto_trigger_hr_review
# ---------------------------------------------------------------------------

class TestAutoTriggerHrReview:
    def test_trigger_pushes_task(self):
        from onemancompany.core import routine as mod
        emp = _emp(name="Dev", nickname="D", level=1, current_quarter_tasks=3,
                   performance_history=[{"score": 3.5}])
        mock_push = MagicMock()
        with (
            patch.object(mod, "load_employee", return_value=emp),
            patch("onemancompany.api.routes._push_adhoc_task", mock_push),
            patch("onemancompany.core.state.LEVEL_NAMES", {1: "Junior"}),
            patch("onemancompany.core.state.make_title", return_value="Junior Developer"),
        ):
            mod._auto_trigger_hr_review("00010")
        mock_push.assert_called_once()

    def test_trigger_no_employee(self):
        from onemancompany.core import routine as mod
        with patch.object(mod, "load_employee", return_value=None):
            mod._auto_trigger_hr_review("99999")
            # Should return without error

    def test_trigger_exception_logged(self):
        from onemancompany.core import routine as mod
        with patch.object(mod, "load_employee", side_effect=Exception("boom")):
            mod._auto_trigger_hr_review("00010")
            # Should not raise


# ---------------------------------------------------------------------------
# Auto-trigger HR review — run_post_task_routine integration
# ---------------------------------------------------------------------------

class TestAutoTriggerHRReview:
    @pytest.mark.asyncio
    async def test_auto_triggers_review_at_threshold(self):
        from onemancompany.core import routine as routine_mod

        emp_data = {
            "00010": {
                "name": "Test Dev", "level": 1,
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
                "Test task", participants=["00010"], project_id="proj1",
            )

        mock_trigger.assert_called_once_with("00010")

    @pytest.mark.asyncio
    async def test_no_auto_trigger_below_threshold(self):
        from onemancompany.core import routine as routine_mod

        emp_data = {
            "00010": {
                "name": "Test Dev", "level": 1,
                "current_quarter_tasks": 0,
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
# run_post_task_routine — main workflow path
# ---------------------------------------------------------------------------

class TestRunPostTaskRoutine:
    @pytest.mark.asyncio
    async def test_returns_early_if_no_employees(self):
        from onemancompany.core import routine as mod
        with patch.object(mod, "load_all_employees", return_value={}):
            await mod.run_post_task_routine("task")
            # Should return without error

    @pytest.mark.asyncio
    async def test_returns_early_if_single_participant(self):
        from onemancompany.core import routine as mod
        emp_data = {"00010": _emp()}
        with (
            patch.object(mod, "load_all_employees", return_value=emp_data),
            patch.object(mod, "load_employee", return_value=_emp()),
            patch.object(mod, "_store", _mock_store()),
            patch.object(mod, "_publish", new_callable=AsyncMock),
        ):
            await mod.run_post_task_routine("task", participants=["00010"])

    @pytest.mark.asyncio
    async def test_fallback_when_no_workflow_doc(self):
        from onemancompany.core import routine as mod
        emp_data = {"00010": _emp(), "00011": _emp(name="Dev2")}

        with (
            patch.object(mod, "load_all_employees", return_value=emp_data),
            patch.object(mod, "load_employee", side_effect=lambda eid: emp_data.get(eid)),
            patch.object(mod, "_store", _mock_store()),
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "load_workflows", return_value={}),
            patch.object(mod, "_run_post_task_routine_fallback", new_callable=AsyncMock) as mock_fb,
        ):
            await mod.run_post_task_routine("task", participants=["00010", "00011"])
        mock_fb.assert_called_once()

    @pytest.mark.asyncio
    async def test_fallback_when_empty_steps(self):
        from onemancompany.core import routine as mod
        emp_data = {"00010": _emp(), "00011": _emp(name="Dev2")}

        empty_wf = _make_workflow(steps=[])
        with (
            patch.object(mod, "load_all_employees", return_value=emp_data),
            patch.object(mod, "load_employee", side_effect=lambda eid: emp_data.get(eid)),
            patch.object(mod, "_store", _mock_store()),
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "load_workflows", return_value={"project_retrospective_workflow": "some md"}),
            patch.object(mod, "parse_workflow", return_value=empty_wf),
            patch.object(mod, "_run_post_task_routine_fallback", new_callable=AsyncMock) as mock_fb,
        ):
            await mod.run_post_task_routine("task", participants=["00010", "00011"])
        mock_fb.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_room_available(self):
        from onemancompany.core import routine as mod
        emp_data = {"00010": _emp(), "00011": _emp(name="Dev2")}
        step = _make_step(title="Self-Evaluation")
        wf = _make_workflow(steps=[step])

        with (
            patch.object(mod, "load_all_employees", return_value=emp_data),
            patch.object(mod, "load_employee", side_effect=lambda eid: emp_data.get(eid)),
            patch.object(mod, "_store", _mock_store()),
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "load_workflows", return_value={"project_retrospective_workflow": "md"}),
            patch.object(mod, "parse_workflow", return_value=wf),
            patch.object(mod, "company_state", MagicMock(meeting_rooms={})),
        ):
            await mod.run_post_task_routine("task", participants=["00010", "00011"])

    @pytest.mark.asyncio
    async def test_full_workflow_execution(self):
        from onemancompany.core import routine as mod
        emp_data = {"00010": _emp(), "00011": _emp(name="Dev2")}

        # Workflow with prep step (should be skipped) + one real step
        prep_step = _make_step(title="Review Preparation", index=0)
        eval_step = _make_step(title="Self-Evaluation", index=1)
        wf = _make_workflow(steps=[prep_step, eval_step])

        room = _mock_room()

        mock_handler = AsyncMock(return_value={"status": "done"})

        with (
            patch.object(mod, "load_all_employees", return_value=emp_data),
            patch.object(mod, "load_employee", side_effect=lambda eid: emp_data.get(eid)),
            patch.object(mod, "_store", _mock_store()),
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_chat", new_callable=AsyncMock),
            patch.object(mod, "load_workflows", return_value={"project_retrospective_workflow": "md"}),
            patch.object(mod, "parse_workflow", return_value=wf),
            patch.object(mod, "company_state", MagicMock(meeting_rooms={"room1": room})),
            patch.object(mod, "_set_participants_status", new_callable=AsyncMock),
            patch.object(mod, "_resolve_handler", return_value=mock_handler),
            patch.object(mod, "_save_report"),
            patch.object(mod, "_build_summary", return_value="summary"),
        ):
            await mod.run_post_task_routine("task", participants=["00010", "00011"])
        # Only eval_step executed (prep skipped)
        assert mock_handler.call_count == 1

    @pytest.mark.asyncio
    async def test_with_project_id_and_archive(self):
        from onemancompany.core import routine as mod
        emp_data = {"00010": _emp(), "00011": _emp(name="Dev2")}

        eval_step = _make_step(title="Self-Evaluation", index=0)
        wf = _make_workflow(steps=[eval_step])
        room = _mock_room()

        mock_handler = AsyncMock(return_value={"status": "done"})

        named_project = {"team": [{"employee_id": "00010"}, {"employee_id": "00011"}]}
        with (
            patch.object(mod, "load_all_employees", return_value=emp_data),
            patch.object(mod, "load_employee", side_effect=lambda eid: emp_data.get(eid)),
            patch.object(mod, "_store", _mock_store()),
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_chat", new_callable=AsyncMock),
            patch.object(mod, "load_workflows", return_value={"project_retrospective_workflow": "md"}),
            patch.object(mod, "parse_workflow", return_value=wf),
            patch.object(mod, "company_state", MagicMock(meeting_rooms={"room1": room})),
            patch.object(mod, "_set_participants_status", new_callable=AsyncMock),
            patch.object(mod, "_resolve_handler", return_value=mock_handler),
            patch.object(mod, "_save_report"),
            patch.object(mod, "_build_summary", return_value="summary"),
            patch("onemancompany.core.project_archive.load_project", return_value={}),
            patch("onemancompany.core.project_archive.load_named_project", return_value=named_project),
            patch("onemancompany.core.project_archive.append_action"),
        ):
            await mod.run_post_task_routine("task", participants=["00010", "00011"], project_id="proj/iter1")

    @pytest.mark.asyncio
    async def test_workflow_with_project_archive_recording(self):
        """When project_id is set and workflow populates ctx with data, archive actions are recorded."""
        from onemancompany.core import routine as mod
        emp_data = {"00010": _emp(), "00011": _emp(name="Dev2")}

        eval_step = _make_step(title="Self-Evaluation", index=0)
        wf = _make_workflow(steps=[eval_step])
        room = _mock_room()

        # Handler that populates ctx with data so archive recording is triggered
        async def _handler(step, ctx):
            ctx.self_evaluations = [{"employee_id": "00010", "evaluation": "ok"}]
            ctx.senior_reviews = [{"reviewer_id": "00011", "review": "good"}]
            ctx.coo_report = "Report"
            ctx.action_items = [{"source": "COO", "description": "Act"}]
            ctx.employee_feedback = [{"employee_id": "00010", "feedback": "need tools"}]
            return {"status": "done"}

        named_project = {"team": [{"employee_id": "00010"}, {"employee_id": "00011"}]}
        mock_append = MagicMock()
        with (
            patch.object(mod, "load_all_employees", return_value=emp_data),
            patch.object(mod, "load_employee", side_effect=lambda eid: emp_data.get(eid)),
            patch.object(mod, "_store", _mock_store()),
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_chat", new_callable=AsyncMock),
            patch.object(mod, "load_workflows", return_value={"project_retrospective_workflow": "md"}),
            patch.object(mod, "parse_workflow", return_value=wf),
            patch.object(mod, "company_state", MagicMock(meeting_rooms={"room1": room})),
            patch.object(mod, "_set_participants_status", new_callable=AsyncMock),
            patch.object(mod, "_resolve_handler", return_value=_handler),
            patch.object(mod, "_save_report"),
            patch.object(mod, "_build_summary", return_value="summary"),
            patch("onemancompany.core.project_archive.load_project", return_value={}),
            patch("onemancompany.core.project_archive.load_named_project", return_value=named_project),
            patch("onemancompany.core.project_archive.append_action", mock_append),
        ):
            await mod.run_post_task_routine("task", participants=["00010", "00011"], project_id="proj/iter1")
        # append_action should be called for self_evals, senior_reviews, coo_report, action_items, feedback
        assert mock_append.call_count == 5

    @pytest.mark.asyncio
    async def test_participants_default_excludes_ceo(self):
        """When participants=None, all employees except CEO are included."""
        from onemancompany.core import routine as mod
        emp_data = {CEO_ID: _emp(name="CEO"), "00010": _emp(), "00011": _emp(name="Dev2")}

        with (
            patch.object(mod, "load_all_employees", return_value=emp_data),
            patch.object(mod, "load_employee", side_effect=lambda eid: emp_data.get(eid)),
            patch.object(mod, "_store", _mock_store()),
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "load_workflows", return_value={}),
            patch.object(mod, "_run_post_task_routine_fallback", new_callable=AsyncMock) as mock_fb,
        ):
            await mod.run_post_task_routine("task")
        call_args = mock_fb.call_args
        participants = call_args[0][1]
        assert CEO_ID not in participants


# ---------------------------------------------------------------------------
# Dedup helpers: _tokenize, _jaccard, _dedup_action_items, _load_past_action_items
# ---------------------------------------------------------------------------

class TestTokenize:
    def test_tokenize_basic(self):
        from onemancompany.core.routine import _tokenize
        result = _tokenize("Hello world 123")
        assert "hello" in result
        assert "world" in result
        assert "123" in result

    def test_tokenize_chinese(self):
        from onemancompany.core.routine import _tokenize
        result = _tokenize("提升效率 improve")
        assert "improve" in result


class TestJaccard:
    def test_identical(self):
        from onemancompany.core.routine import _jaccard
        assert _jaccard({"a", "b"}, {"a", "b"}) == 1.0

    def test_disjoint(self):
        from onemancompany.core.routine import _jaccard
        assert _jaccard({"a"}, {"b"}) == 0.0

    def test_partial_overlap(self):
        from onemancompany.core.routine import _jaccard
        assert _jaccard({"a", "b"}, {"b", "c"}) == pytest.approx(1 / 3)

    def test_empty_sets(self):
        from onemancompany.core.routine import _jaccard
        assert _jaccard(set(), set()) == 0.0

    def test_one_empty(self):
        from onemancompany.core.routine import _jaccard
        assert _jaccard({"a"}, set()) == 0.0


class TestLoadPastActionItems:
    def test_no_reports_dir(self):
        from onemancompany.core import routine as mod
        with patch.object(mod, "REPORTS_DIR", Path("/nonexistent")):
            result = mod._load_past_action_items()
        assert result == []

    def test_with_reports(self, tmp_path):
        import yaml
        from onemancompany.core import routine as mod

        report = {"id": "r1", "action_items": [{"description": "fix bug"}]}
        report_path = tmp_path / "r1.yaml"
        with open(report_path, "w") as f:
            yaml.dump(report, f)

        with patch.object(mod, "REPORTS_DIR", tmp_path):
            result = mod._load_past_action_items()
        assert len(result) == 1
        assert result[0]["description"] == "fix bug"

    def test_malformed_report(self, tmp_path):
        from onemancompany.core import routine as mod

        report_path = tmp_path / "bad.yaml"
        report_path.write_text("not: a: valid: ---")

        with patch.object(mod, "REPORTS_DIR", tmp_path):
            result = mod._load_past_action_items()
        # Should not crash; returns whatever it can parse
        assert isinstance(result, list)

    def test_report_with_no_action_items(self, tmp_path):
        import yaml
        from onemancompany.core import routine as mod

        report = {"id": "r1"}
        report_path = tmp_path / "r1.yaml"
        with open(report_path, "w") as f:
            yaml.dump(report, f)

        with patch.object(mod, "REPORTS_DIR", tmp_path):
            result = mod._load_past_action_items()
        assert result == []

    def test_report_with_non_dict_action_items(self, tmp_path):
        import yaml
        from onemancompany.core import routine as mod

        report = {"id": "r1", "action_items": ["string item"]}
        report_path = tmp_path / "r1.yaml"
        with open(report_path, "w") as f:
            yaml.dump(report, f)

        with patch.object(mod, "REPORTS_DIR", tmp_path):
            result = mod._load_past_action_items()
        assert result == []

    def test_report_none_doc(self, tmp_path):
        from onemancompany.core import routine as mod

        report_path = tmp_path / "empty.yaml"
        report_path.write_text("")

        with patch.object(mod, "REPORTS_DIR", tmp_path):
            result = mod._load_past_action_items()
        assert result == []


class TestDedupActionItems:
    def test_no_past_items(self):
        from onemancompany.core import routine as mod
        items = [{"description": "new thing"}]
        with patch.object(mod, "_load_past_action_items", return_value=[]):
            unique, dups, recurring = mod._dedup_action_items(items)
        assert unique == items
        assert dups == []
        assert recurring == []

    def test_duplicate_detected(self):
        from onemancompany.core import routine as mod
        past = [{"description": "improve testing coverage", "report_id": "r1"}]
        items = [{"description": "improve testing coverage"}]
        with patch.object(mod, "_load_past_action_items", return_value=past):
            unique, dups, recurring = mod._dedup_action_items(items)
        assert len(dups) == 1
        assert unique == []

    def test_recurring_detected(self):
        from onemancompany.core import routine as mod
        # Same desc appearing in 2 past reports
        past = [
            {"description": "improve testing coverage", "report_id": "r1"},
            {"description": "improve testing coverage", "report_id": "r2"},
        ]
        items = [{"description": "improve testing coverage"}]
        with patch.object(mod, "_load_past_action_items", return_value=past):
            unique, dups, recurring = mod._dedup_action_items(items)
        assert len(recurring) == 1
        assert unique == []

    def test_unique_with_past_items(self):
        """Item with no match in past should be unique even when past items exist."""
        from onemancompany.core import routine as mod
        past = [{"description": "completely different topic XYZ", "report_id": "r1"}]
        items = [{"description": "brand new action item ABC"}]
        with patch.object(mod, "_load_past_action_items", return_value=past):
            unique, dups, recurring = mod._dedup_action_items(items)
        assert len(unique) == 1
        assert dups == []
        assert recurring == []


# ---------------------------------------------------------------------------
# _ea_auto_approve_actions (fallback path)
# ---------------------------------------------------------------------------

class TestEaAutoApproveActions:
    @pytest.mark.asyncio
    async def test_all_duplicates(self):
        from onemancompany.core import routine as mod
        items = [{"description": "dup"}]
        with (
            patch.object(mod, "_dedup_action_items", return_value=([], [{"description": "dup"}], [])),
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_chat", new_callable=AsyncMock),
        ):
            result = await mod._ea_auto_approve_actions(items, "task", "report", "room1")
        assert result["approved"] == []

    @pytest.mark.asyncio
    async def test_with_recurring(self):
        from onemancompany.core import routine as mod
        items = [{"description": "recurring"}]
        with (
            patch.object(mod, "_dedup_action_items", return_value=([], [], [{"description": "recurring"}])),
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_chat", new_callable=AsyncMock),
        ):
            result = await mod._ea_auto_approve_actions(items, "task", "report", "room1")
        assert result["approved"] == []

    @pytest.mark.asyncio
    async def test_approved_actions_pushed_to_coo(self):
        from onemancompany.core import routine as mod
        items = [{"source": "COO", "description": "Do X", "priority": "high"}]

        ea_json = '{"approved_indices": [0], "rejected_indices": [], "reason": "OK"}'
        mock_coo_loop = MagicMock()
        with (
            patch.object(mod, "_dedup_action_items", return_value=(items, [], [])),
            patch.object(mod, "make_llm", return_value=MagicMock()),
            patch.object(mod, "tracked_ainvoke", new_callable=AsyncMock, return_value=_llm_resp(ea_json)),
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_chat", new_callable=AsyncMock),
            patch("onemancompany.core.agent_loop.get_agent_loop", return_value=mock_coo_loop),
        ):
            result = await mod._ea_auto_approve_actions(items, "task", "report", "room1")
        assert len(result["approved"]) == 1
        mock_coo_loop.push_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_json_defaults_approve_all(self):
        from onemancompany.core import routine as mod
        items = [{"source": "HR", "description": "Do Y", "priority": "medium"}]

        mock_coo_loop = MagicMock()
        with (
            patch.object(mod, "_dedup_action_items", return_value=(items, [], [])),
            patch.object(mod, "make_llm", return_value=MagicMock()),
            patch.object(mod, "tracked_ainvoke", new_callable=AsyncMock, return_value=_llm_resp("blah blah")),
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_chat", new_callable=AsyncMock),
            patch("onemancompany.core.agent_loop.get_agent_loop", return_value=mock_coo_loop),
        ):
            result = await mod._ea_auto_approve_actions(items, "task", "report", "room1")
        assert len(result["approved"]) == 1

    @pytest.mark.asyncio
    async def test_json_decode_error(self):
        from onemancompany.core import routine as mod
        items = [{"source": "HR", "description": "Do Z", "priority": "high"}]

        mock_coo_loop = MagicMock()
        with (
            patch.object(mod, "_dedup_action_items", return_value=(items, [], [])),
            patch.object(mod, "make_llm", return_value=MagicMock()),
            patch.object(mod, "tracked_ainvoke", new_callable=AsyncMock, return_value=_llm_resp("{bad json}")),
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_chat", new_callable=AsyncMock),
            patch("onemancompany.core.agent_loop.get_agent_loop", return_value=mock_coo_loop),
        ):
            result = await mod._ea_auto_approve_actions(items, "task", "report", "room1")
        assert len(result["approved"]) == 1

    @pytest.mark.asyncio
    async def test_with_asset_consolidation(self):
        from onemancompany.core import routine as mod
        items = [
            {"type": "asset_consolidation", "source": "COO", "description": "Asset",
             "priority": "medium", "name": "tool", "asset_description": "desc",
             "project_dir": "/tmp", "files": ["x.py"]},
        ]

        ea_json = '{"approved_indices": [0], "rejected_indices": [], "reason": "OK"}'
        with (
            patch.object(mod, "_dedup_action_items", return_value=(items, [], [])),
            patch.object(mod, "make_llm", return_value=MagicMock()),
            patch.object(mod, "tracked_ainvoke", new_callable=AsyncMock, return_value=_llm_resp(ea_json)),
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_chat", new_callable=AsyncMock),
            patch("onemancompany.agents.coo_agent.register_asset", MagicMock(invoke=MagicMock(return_value="ok"))),
        ):
            result = await mod._ea_auto_approve_actions(items, "task", "report", "room1")
        assert len(result["approved"]) == 1

    @pytest.mark.asyncio
    async def test_coo_loop_not_found(self):
        from onemancompany.core import routine as mod
        items = [{"source": "COO", "description": "Do X", "priority": "high"}]

        ea_json = '{"approved_indices": [0], "rejected_indices": [], "reason": "OK"}'
        with (
            patch.object(mod, "_dedup_action_items", return_value=(items, [], [])),
            patch.object(mod, "make_llm", return_value=MagicMock()),
            patch.object(mod, "tracked_ainvoke", new_callable=AsyncMock, return_value=_llm_resp(ea_json)),
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_chat", new_callable=AsyncMock),
            patch("onemancompany.core.agent_loop.get_agent_loop", return_value=None),
        ):
            result = await mod._ea_auto_approve_actions(items, "task", "report", "room1")
        # No crash, approved list still populated
        assert len(result["approved"]) == 1


# ---------------------------------------------------------------------------
# Fallback routine: _run_post_task_routine_fallback
# ---------------------------------------------------------------------------

class TestRunPostTaskRoutineFallback:
    @pytest.mark.asyncio
    async def test_fallback_no_room(self):
        from onemancompany.core import routine as mod
        with (
            patch.object(mod, "load_workflows", return_value={}),
            patch.object(mod, "company_state", MagicMock(meeting_rooms={})),
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_store", _mock_store()),
        ):
            await mod._run_post_task_routine_fallback("task", ["00010", "00011"])

    @pytest.mark.asyncio
    async def test_fallback_full_flow(self):
        from onemancompany.core import routine as mod
        room = _mock_room()
        phase1_result = {"self_evaluations": [], "senior_reviews": [], "hr_summary": []}
        phase2_result = {"coo_report": "OK", "employee_feedback": [], "action_items": [{"source": "HR", "description": "Do X"}]}

        with (
            patch.object(mod, "load_workflows", return_value={}),
            patch.object(mod, "company_state", MagicMock(meeting_rooms={"room1": room})),
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_chat", new_callable=AsyncMock),
            patch.object(mod, "_store", _mock_store()),
            patch.object(mod, "_set_participants_status", new_callable=AsyncMock),
            patch.object(mod, "_run_review_phase1", new_callable=AsyncMock, return_value=phase1_result),
            patch.object(mod, "_run_review_phase2", new_callable=AsyncMock, return_value=phase2_result),
            patch.object(mod, "_ea_auto_approve_actions", new_callable=AsyncMock, return_value={"approved": []}),
            patch.object(mod, "_save_report"),
            patch.object(mod, "_build_summary", return_value="summary"),
        ):
            await mod._run_post_task_routine_fallback("task", ["00010", "00011"])

    @pytest.mark.asyncio
    async def test_fallback_with_project_id(self):
        from onemancompany.core import routine as mod
        room = _mock_room()
        phase1_result = {
            "self_evaluations": [{"employee_id": "00010", "evaluation": "ok"}],
            "senior_reviews": [{"reviewer_id": "00011", "review": "good"}],
            "hr_summary": [],
        }
        phase2_result = {
            "coo_report": "Report",
            "employee_feedback": [{"employee_id": "00010", "feedback": "need tools"}],
            "action_items": [],
        }

        with (
            patch.object(mod, "load_workflows", return_value={}),
            patch.object(mod, "company_state", MagicMock(meeting_rooms={"room1": room})),
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_chat", new_callable=AsyncMock),
            patch.object(mod, "_store", _mock_store()),
            patch.object(mod, "_set_participants_status", new_callable=AsyncMock),
            patch.object(mod, "_run_review_phase1", new_callable=AsyncMock, return_value=phase1_result),
            patch.object(mod, "_run_review_phase2", new_callable=AsyncMock, return_value=phase2_result),
            patch.object(mod, "_save_report"),
            patch.object(mod, "_build_summary", return_value="summary"),
            patch("onemancompany.core.project_archive.append_action"),
        ):
            await mod._run_post_task_routine_fallback("task", ["00010", "00011"], project_id="proj1")


# ---------------------------------------------------------------------------
# _run_review_phase1
# ---------------------------------------------------------------------------

class TestRunReviewPhase1:
    @pytest.mark.asyncio
    async def test_phase1_basic(self):
        from onemancompany.core import routine as mod
        emp = _emp(work_principles="Be thorough")

        review_json = '[{"name": "Dev1", "review": "OK"}]'
        hr_json = '[{"employee": "Dev1", "improvements": ["learn more"]}]'

        # Second and third calls: senior review, HR summary
        responses = [
            _llm_resp("I did well"),     # self-eval for 00010
            _llm_resp("I also did well"),  # self-eval for 00011
            _llm_resp(review_json),        # senior review
            _llm_resp(hr_json),            # HR summary
        ]

        def _load(eid):
            if eid == "00010":
                return _emp(name="Senior", nickname="S", level=3, role="Lead")
            return _emp(name="Junior", nickname="J", level=1)

        with (
            patch.object(mod, "make_llm", return_value=MagicMock()),
            patch.object(mod, "tracked_ainvoke", new_callable=AsyncMock, side_effect=responses),
            patch.object(mod, "load_employee", side_effect=_load),
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_chat", new_callable=AsyncMock),
            patch.object(mod, "get_employee_skills_prompt", return_value=""),
            patch.object(mod, "get_employee_tools_prompt", return_value=""),
        ):
            result = await mod._run_review_phase1("task", ["00010", "00011"], workflow_doc="", room_id="room1")

        assert len(result["self_evaluations"]) == 2
        assert len(result["hr_summary"]) == 1

    @pytest.mark.asyncio
    async def test_phase1_with_workflow_doc(self):
        from onemancompany.core import routine as mod
        emp = _emp()

        with (
            patch.object(mod, "make_llm", return_value=MagicMock()),
            patch.object(mod, "tracked_ainvoke", new_callable=AsyncMock, return_value=_llm_resp("eval")),
            patch.object(mod, "load_employee", return_value=None),  # skip all employees
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_chat", new_callable=AsyncMock),
        ):
            result = await mod._run_review_phase1("task", ["99999"], workflow_doc="some doc", room_id="room1")
        assert result["self_evaluations"] == []


# ---------------------------------------------------------------------------
# _run_review_phase2
# ---------------------------------------------------------------------------

class TestRunReviewPhase2:
    @pytest.mark.asyncio
    async def test_phase2_basic(self):
        from onemancompany.core import routine as mod
        emp = _emp(work_principles="Principles")
        phase1 = {"hr_summary": [{"employee": "Dev1", "improvements": ["improve"]}]}

        responses = [
            _llm_resp("COO report text"),  # COO report
            _llm_resp("Need more tools"),   # employee feedback
            _llm_resp('[{"source": "COO", "description": "Do X", "priority": "high"}]'),  # action items
        ]

        with (
            patch.object(mod, "load_all_employees", return_value={"00010": emp}),
            patch.object(mod, "load_employee", return_value=emp),
            patch.object(mod, "company_state", MagicMock(tools={})),
            patch.object(mod, "_store", _mock_store(load_rooms=MagicMock(return_value={}))),
            patch.object(mod, "make_llm", return_value=MagicMock()),
            patch.object(mod, "tracked_ainvoke", new_callable=AsyncMock, side_effect=responses),
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_chat", new_callable=AsyncMock),
            patch.object(mod, "get_employee_skills_prompt", return_value=""),
            patch.object(mod, "get_employee_tools_prompt", return_value=""),
        ):
            result = await mod._run_review_phase2("task", ["00010"], phase1, room_id="room1")

        assert result["coo_report"] == "COO report text"
        assert len(result["employee_feedback"]) == 1
        assert len(result["action_items"]) == 1

    @pytest.mark.asyncio
    async def test_phase2_with_workflow_doc(self):
        from onemancompany.core import routine as mod
        phase1 = {"hr_summary": []}

        with (
            patch.object(mod, "load_all_employees", return_value={}),
            patch.object(mod, "load_employee", return_value=None),
            patch.object(mod, "company_state", MagicMock(tools={})),
            patch.object(mod, "_store", _mock_store(load_rooms=MagicMock(return_value={}))),
            patch.object(mod, "make_llm", return_value=MagicMock()),
            patch.object(mod, "tracked_ainvoke", new_callable=AsyncMock, side_effect=[
                _llm_resp("COO report"),
                _llm_resp('[{"source": "COO", "description": "Action", "priority": "low"}]'),
            ]),
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_chat", new_callable=AsyncMock),
        ):
            result = await mod._run_review_phase2("task", [], phase1, workflow_doc="some doc", room_id="room1")
        assert result["coo_report"] == "COO report"


# ---------------------------------------------------------------------------
# _build_summary
# ---------------------------------------------------------------------------

class TestBuildSummary:
    def test_minimal_doc(self):
        from onemancompany.core.routine import _build_summary
        doc = {"timestamp": "2024-01-01T00:00:00", "task_summary": "Task", "phase1": {}, "phase2": {}}
        result = _build_summary(doc)
        assert "Meeting Report" in result
        assert "Task" in result

    def test_with_workflow(self):
        from onemancompany.core.routine import _build_summary
        doc = {
            "timestamp": "2024-01-01T00:00:00", "task_summary": "Task",
            "workflow": "project_retro",
            "phase1": {"hr_summary": [{"employee": "Dev1", "improvements": ["improve X"]}]},
            "phase2": {"coo_report": "All good", "employee_feedback": [{"name": "Dev1", "feedback": "Need tools"}]},
        }
        result = _build_summary(doc)
        assert "Workflow" in result
        assert "Review Meeting" in result
        assert "Operations Review" in result
        assert "Employee Remarks" in result

    def test_with_asset_suggestions(self):
        from onemancompany.core.routine import _build_summary
        doc = {
            "timestamp": "2024-01-01T00:00:00", "task_summary": "Task",
            "phase1": {}, "phase2": {},
            "asset_suggestions": [{"name": "tool.py", "description": "Useful", "files": ["tool.py"]}],
        }
        result = _build_summary(doc)
        assert "Asset Consolidation" in result

    def test_asset_suggestions_from_phase2(self):
        from onemancompany.core.routine import _build_summary
        doc = {
            "timestamp": "2024-01-01T00:00:00", "task_summary": "Task",
            "phase1": {},
            "phase2": {"asset_suggestions": [{"name": "script", "description": "Helper", "files": ["s.py"]}]},
        }
        result = _build_summary(doc)
        assert "Asset Consolidation" in result


# ---------------------------------------------------------------------------
# _save_report
# ---------------------------------------------------------------------------

class TestSaveReport:
    def test_save_report(self, tmp_path):
        from onemancompany.core import routine as mod
        with patch.object(mod, "REPORTS_DIR", tmp_path):
            mod._save_report("test123", {"id": "test123", "data": "value"})
        assert (tmp_path / "test123.yaml").exists()


# ---------------------------------------------------------------------------
# execute_approved_actions
# ---------------------------------------------------------------------------

class TestExecuteApprovedActions:
    @pytest.mark.asyncio
    async def test_report_not_found(self):
        from onemancompany.core import routine as mod
        with patch.object(mod, "REPORTS_DIR", Path("/nonexistent")):
            result = await mod.execute_approved_actions("xxx", [0])
        assert "not found" in result

    @pytest.mark.asyncio
    async def test_from_pending_reports(self):
        from onemancompany.core import routine as mod
        doc = {
            "id": "r1",
            "action_items": [
                {"source": "HR", "description": "Hire someone"},
            ],
        }
        mod.pending_reports["r1"] = doc

        mock_coo_loop = MagicMock()
        with (
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_save_report"),
            patch("onemancompany.core.agent_loop.get_agent_loop", return_value=mock_coo_loop),
        ):
            result = await mod.execute_approved_actions("r1", [0])
        assert "Pushed" in result or "actions" in result.lower()
        mock_coo_loop.push_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_approved_actions(self):
        from onemancompany.core import routine as mod
        doc = {"id": "r1", "action_items": [{"source": "HR", "description": "X"}]}
        mod.pending_reports["r1"] = doc
        with patch.object(mod, "_publish", new_callable=AsyncMock):
            result = await mod.execute_approved_actions("r1", [])
        assert "No actions" in result

    @pytest.mark.asyncio
    async def test_from_disk(self, tmp_path):
        import yaml
        from onemancompany.core import routine as mod

        doc = {"id": "r2", "action_items": [{"source": "COO", "description": "Do Y"}]}
        report_path = tmp_path / "r2.yaml"
        with open(report_path, "w") as f:
            yaml.dump(doc, f)

        mock_coo_loop = MagicMock()
        with (
            patch.object(mod, "REPORTS_DIR", tmp_path),
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_save_report"),
            patch("onemancompany.core.agent_loop.get_agent_loop", return_value=mock_coo_loop),
        ):
            result = await mod.execute_approved_actions("r2", [0])
        assert "Pushed" in result or "actions" in result.lower()

    @pytest.mark.asyncio
    async def test_asset_consolidation_only(self):
        from onemancompany.core import routine as mod
        doc = {
            "id": "r3",
            "action_items": [
                {"type": "asset_consolidation", "source": "COO", "description": "Asset",
                 "name": "tool", "asset_description": "desc",
                 "project_dir": "/tmp", "files": ["x.py"]},
            ],
        }
        mod.pending_reports["r3"] = doc

        with (
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_save_report"),
            patch("onemancompany.agents.coo_agent.register_asset", MagicMock(invoke=MagicMock(return_value="ok"))),
        ):
            result = await mod.execute_approved_actions("r3", [0])
        assert "asset" in result.lower()
        mod.pending_reports.pop("r3", None)

    @pytest.mark.asyncio
    async def test_mixed_asset_and_regular(self):
        from onemancompany.core import routine as mod
        doc = {
            "id": "r4",
            "action_items": [
                {"type": "asset_consolidation", "source": "COO", "description": "Asset",
                 "name": "tool", "asset_description": "desc",
                 "project_dir": "/tmp", "files": ["x.py"]},
                {"source": "HR", "description": "Hire someone"},
            ],
        }
        mod.pending_reports["r4"] = doc

        mock_coo_loop = MagicMock()
        with (
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_save_report"),
            patch("onemancompany.agents.coo_agent.register_asset", MagicMock(invoke=MagicMock(return_value="ok"))),
            patch("onemancompany.core.agent_loop.get_agent_loop", return_value=mock_coo_loop),
        ):
            result = await mod.execute_approved_actions("r4", [0, 1])
        assert "asset" in result.lower()
        mod.pending_reports.pop("r4", None)

    @pytest.mark.asyncio
    async def test_coo_loop_not_found(self):
        from onemancompany.core import routine as mod
        doc = {"id": "r5", "action_items": [{"source": "COO", "description": "Do Z"}]}
        mod.pending_reports["r5"] = doc

        with (
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_save_report"),
            patch("onemancompany.core.agent_loop.get_agent_loop", return_value=None),
        ):
            result = await mod.execute_approved_actions("r5", [0])
        assert "not found" in result.lower()
        mod.pending_reports.pop("r5", None)

    @pytest.mark.asyncio
    async def test_unrouted_actions_go_to_coo(self):
        from onemancompany.core import routine as mod
        doc = {
            "id": "r6",
            "action_items": [
                {"source": "Unknown", "description": "Something"},
            ],
        }
        mod.pending_reports["r6"] = doc

        mock_coo_loop = MagicMock()
        with (
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_save_report"),
            patch("onemancompany.core.agent_loop.get_agent_loop", return_value=mock_coo_loop),
        ):
            result = await mod.execute_approved_actions("r6", [0])
        mock_coo_loop.push_task.assert_called_once()
        mod.pending_reports.pop("r6", None)


# ---------------------------------------------------------------------------
# run_all_hands_meeting
# ---------------------------------------------------------------------------

class TestRunAllHandsMeeting:
    @pytest.mark.asyncio
    async def test_no_employees(self):
        from onemancompany.core import routine as mod
        with patch.object(mod, "load_all_employees", return_value={}):
            await mod.run_all_hands_meeting("Hello everyone")

    @pytest.mark.asyncio
    async def test_no_room(self):
        from onemancompany.core import routine as mod
        emp_data = {"00010": _emp()}
        with (
            patch.object(mod, "load_all_employees", return_value=emp_data),
            patch.object(mod, "company_state", MagicMock(meeting_rooms={})),
            patch.object(mod, "_publish", new_callable=AsyncMock),
        ):
            await mod.run_all_hands_meeting("Hello")

    @pytest.mark.asyncio
    async def test_full_meeting(self):
        from onemancompany.core import routine as mod
        emp = _emp(work_principles="Be thorough")
        emp_data = {"00010": emp}
        room = _mock_room()

        with (
            patch.object(mod, "load_all_employees", return_value=emp_data),
            patch.object(mod, "company_state", MagicMock(meeting_rooms={"room1": room})),
            patch.object(mod, "_store", _mock_store()),
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_chat", new_callable=AsyncMock),
            patch.object(mod, "_set_participants_status", new_callable=AsyncMock),
            patch.object(mod, "make_llm", return_value=MagicMock()),
            patch.object(mod, "tracked_ainvoke", new_callable=AsyncMock, return_value=_llm_resp("Understood")),
            patch.object(mod, "get_employee_skills_prompt", return_value=""),
            patch.object(mod, "get_employee_tools_prompt", return_value=""),
            patch.object(mod, "_save_report"),
        ):
            await mod.run_all_hands_meeting("Company update")


# ---------------------------------------------------------------------------
# CEO Meeting System
# ---------------------------------------------------------------------------

class TestCeoMeetingStart:
    @pytest.mark.asyncio
    async def test_start_all_hands(self):
        from onemancompany.core import routine as routine_mod
        from onemancompany.core.config import HR_ID, COO_ID, EA_ID, CSO_ID

        emp_data = {
            "00010": {"name": "Dev1", "level": 1, "nickname": "D1"},
            "00011": {"name": "Dev2", "level": 1, "nickname": "D2"},
        }
        mock_room = _mock_room()

        with (
            patch.object(routine_mod, "load_all_employees", return_value=emp_data),
            patch.object(routine_mod, "company_state", MagicMock(meeting_rooms={"room1": mock_room})),
            patch.object(routine_mod, "_store", _mock_store()),
            patch.object(routine_mod, "_publish", new_callable=AsyncMock),
            patch.object(routine_mod, "_set_participants_status", new_callable=AsyncMock),
        ):
            result = await routine_mod.start_ceo_meeting("all_hands")

        assert result["type"] == "all_hands"
        assert result["status"] == "started"
        assert len(result["participants"]) == 2
        routine_mod._active_ceo_meeting = None

    @pytest.mark.asyncio
    async def test_start_returns_error_when_meeting_active(self):
        from onemancompany.core import routine as routine_mod
        routine_mod._active_ceo_meeting = {"type": "all_hands"}
        try:
            result = await routine_mod.start_ceo_meeting("discussion")
            assert "error" in result
        finally:
            routine_mod._active_ceo_meeting = None

    @pytest.mark.asyncio
    async def test_start_no_employees(self):
        from onemancompany.core import routine as mod
        with patch.object(mod, "load_all_employees", return_value={}):
            result = await mod.start_ceo_meeting("all_hands")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_start_only_ceo_employee(self):
        from onemancompany.core import routine as mod
        with patch.object(mod, "load_all_employees", return_value={CEO_ID: _emp(name="CEO")}):
            result = await mod.start_ceo_meeting("all_hands")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_start_no_room(self):
        from onemancompany.core import routine as mod
        emp_data = {"00010": _emp()}
        with (
            patch.object(mod, "load_all_employees", return_value=emp_data),
            patch.object(mod, "company_state", MagicMock(meeting_rooms={})),
        ):
            result = await mod.start_ceo_meeting("all_hands")
        assert "error" in result


class TestCeoMeetingChat:
    @pytest.mark.asyncio
    async def test_chat_no_active_meeting(self):
        from onemancompany.core import routine as routine_mod
        routine_mod._active_ceo_meeting = None
        result = await routine_mod.ceo_meeting_chat("hello")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_all_hands_chat_generates_responses(self):
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

    @pytest.mark.asyncio
    async def test_all_hands_chat_with_work_principles(self):
        from onemancompany.core import routine as mod

        mod._active_ceo_meeting = {
            "type": "all_hands",
            "participants": ["00010"],
            "chat_history": [],
            "room_id": "room1",
        }

        emp_data = {
            "00010": _emp(work_principles="Always test"),
        }

        with (
            patch.object(mod, "_chat", new_callable=AsyncMock),
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "load_all_employees", return_value=emp_data),
            patch.object(mod, "make_llm", return_value=MagicMock()),
            patch.object(mod, "tracked_ainvoke", new_callable=AsyncMock, return_value=_llm_resp("Got it")),
        ):
            try:
                result = await mod.ceo_meeting_chat("Focus on quality")
                assert len(result["responses"]) == 1
            finally:
                mod._active_ceo_meeting = None

    @pytest.mark.asyncio
    async def test_all_hands_skips_missing_employee(self):
        from onemancompany.core import routine as mod

        mod._active_ceo_meeting = {
            "type": "all_hands",
            "participants": ["99999"],
            "chat_history": [],
            "room_id": "room1",
        }

        with (
            patch.object(mod, "_chat", new_callable=AsyncMock),
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "load_all_employees", return_value={}),
        ):
            try:
                result = await mod.ceo_meeting_chat("Hello")
                assert result["responses"] == []
            finally:
                mod._active_ceo_meeting = None

    @pytest.mark.asyncio
    async def test_discussion_chat_token_grab(self):
        from onemancompany.core import routine as mod

        mod._active_ceo_meeting = {
            "type": "discussion",
            "participants": ["00010", "00011"],
            "chat_history": [],
            "room_id": "room1",
        }
        mod._ceo_meeting_cancel = None

        emp_data = {
            "00010": _emp(name="Dev1", nickname="D1"),
            "00011": _emp(name="Dev2", nickname="D2"),
        }

        # First evaluate returns YES, second NO
        async def _mock_invoke(llm, prompt, **kwargs):
            if "evaluate" in prompt.lower() or "want to speak" in prompt.lower():
                emp_id = kwargs.get("employee_id", "")
                if emp_id == "00010":
                    return _llm_resp("YES I want to speak")
                return _llm_resp("NO")
            return _llm_resp("My discussion point")

        with (
            patch.object(mod, "_chat", new_callable=AsyncMock),
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "load_all_employees", return_value=emp_data),
            patch.object(mod, "make_llm", return_value=MagicMock()),
            patch.object(mod, "tracked_ainvoke", new_callable=AsyncMock, side_effect=_mock_invoke),
            patch("onemancompany.agents.common_tools._build_evaluate_prompt", return_value="evaluate prompt"),
            patch("onemancompany.agents.common_tools._build_speech_prompt", return_value="speech prompt"),
        ):
            try:
                result = await mod.ceo_meeting_chat("Let's discuss")
                assert "responses" in result
            finally:
                mod._active_ceo_meeting = None
                mod._ceo_meeting_cancel = None

    @pytest.mark.asyncio
    async def test_discussion_cancel_event(self):
        from onemancompany.core import routine as mod

        cancel = asyncio.Event()
        cancel.set()  # Pre-set to simulate CEO sending new message
        mod._active_ceo_meeting = {
            "type": "discussion",
            "participants": ["00010"],
            "chat_history": [],
            "room_id": "room1",
        }
        mod._ceo_meeting_cancel = cancel

        emp_data = {"00010": _emp()}

        with (
            patch.object(mod, "_chat", new_callable=AsyncMock),
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "load_all_employees", return_value=emp_data),
        ):
            try:
                result = await mod.ceo_meeting_chat("New message")
                assert "responses" in result
                assert result["responses"] == []
            finally:
                mod._active_ceo_meeting = None
                mod._ceo_meeting_cancel = None

    @pytest.mark.asyncio
    async def test_discussion_no_winner(self):
        from onemancompany.core import routine as mod

        mod._active_ceo_meeting = {
            "type": "discussion",
            "participants": ["00010"],
            "chat_history": [],
            "room_id": "room1",
        }
        mod._ceo_meeting_cancel = None

        emp_data = {"00010": _emp()}

        with (
            patch.object(mod, "_chat", new_callable=AsyncMock),
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "load_all_employees", return_value=emp_data),
            patch.object(mod, "make_llm", return_value=MagicMock()),
            patch.object(mod, "tracked_ainvoke", new_callable=AsyncMock, return_value=_llm_resp("NO")),
            patch("onemancompany.agents.common_tools._build_evaluate_prompt", return_value="evaluate"),
        ):
            try:
                result = await mod.ceo_meeting_chat("Thoughts?")
                assert result["responses"] == []
            finally:
                mod._active_ceo_meeting = None


class TestCeoMeetingEnd:
    @pytest.mark.asyncio
    async def test_end_no_active_meeting(self):
        from onemancompany.core import routine as routine_mod
        routine_mod._active_ceo_meeting = None
        result = await routine_mod.end_ceo_meeting()
        assert "error" in result

    @pytest.mark.asyncio
    async def test_end_saves_guidance_and_extracts_action_points(self):
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

        reflection_resp = MagicMock(content="NO_UPDATE")
        ea_resp = MagicMock(content='["Improve unit test coverage"]')

        mock_store = _mock_store()

        with (
            patch.object(routine_mod, "load_all_employees", return_value=emp_data),
            patch.object(routine_mod, "_publish", new_callable=AsyncMock),
            patch.object(routine_mod, "_chat", new_callable=AsyncMock),
            patch.object(routine_mod, "_set_participants_status", new_callable=AsyncMock),
            patch.object(routine_mod, "_store", mock_store),
            patch.object(routine_mod, "company_state", MagicMock(meeting_rooms={"room1": mock_room})),
            patch.object(routine_mod, "make_llm", return_value=MagicMock()),
            patch.object(routine_mod, "tracked_ainvoke", new_callable=AsyncMock, side_effect=[
                reflection_resp,
                ea_resp,
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

    @pytest.mark.asyncio
    async def test_end_with_work_principles_update(self):
        from onemancompany.core import routine as mod

        mod._active_ceo_meeting = {
            "type": "all_hands",
            "room_id": "room1",
            "room_name": "Main Room",
            "participants": ["00010"],
            "chat_history": [{"speaker": "CEO", "message": "Focus on TDD"}],
        }

        emp_data = {"00010": _emp(work_principles="Old principles")}
        mock_room = MagicMock(id="room1", name="Main Room", is_booked=True,
                              booked_by="00001", participants=["00001", "00010"])
        mock_st = _mock_store()

        # Reflection returns UPDATED:
        reflection_resp = _llm_resp("UPDATED: New TDD-focused principles")
        ea_resp = _llm_resp("[]")

        with (
            patch.object(mod, "load_all_employees", return_value=emp_data),
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_chat", new_callable=AsyncMock),
            patch.object(mod, "_set_participants_status", new_callable=AsyncMock),
            patch.object(mod, "_store", mock_st),
            patch.object(mod, "company_state", MagicMock(meeting_rooms={"room1": mock_room})),
            patch.object(mod, "make_llm", return_value=MagicMock()),
            patch.object(mod, "tracked_ainvoke", new_callable=AsyncMock, side_effect=[reflection_resp, ea_resp]),
            patch.object(mod, "_save_report"),
        ):
            result = await mod.end_ceo_meeting()

        assert result["status"] == "ended"
        mock_st.save_work_principles.assert_called_once()

    @pytest.mark.asyncio
    async def test_end_guidance_save_exception(self):
        from onemancompany.core import routine as mod

        mod._active_ceo_meeting = {
            "type": "discussion",
            "room_id": "room1",
            "room_name": "Main Room",
            "participants": ["00010"],
            "chat_history": [{"speaker": "CEO", "message": "Hello"}],
        }

        emp_data = {"00010": _emp()}
        mock_room = MagicMock(id="room1", name="Main Room", is_booked=True,
                              booked_by="00001", participants=["00001", "00010"])
        mock_st = _mock_store(
            load_employee_guidance=MagicMock(side_effect=Exception("disk error")),
        )

        with (
            patch.object(mod, "load_all_employees", return_value=emp_data),
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_chat", new_callable=AsyncMock),
            patch.object(mod, "_set_participants_status", new_callable=AsyncMock),
            patch.object(mod, "_store", mock_st),
            patch.object(mod, "company_state", MagicMock(meeting_rooms={"room1": mock_room})),
            patch.object(mod, "make_llm", return_value=MagicMock()),
            patch.object(mod, "tracked_ainvoke", new_callable=AsyncMock, side_effect=[
                _llm_resp("NO_UPDATE"),
                _llm_resp("[]"),
            ]),
            patch.object(mod, "_save_report"),
        ):
            result = await mod.end_ceo_meeting()
        assert result["status"] == "ended"

    @pytest.mark.asyncio
    async def test_end_reflection_exception(self):
        from onemancompany.core import routine as mod

        mod._active_ceo_meeting = {
            "type": "discussion",
            "room_id": "room1",
            "room_name": "Main Room",
            "participants": ["00010"],
            "chat_history": [{"speaker": "CEO", "message": "Hello"}],
        }

        emp_data = {"00010": _emp()}
        mock_room = MagicMock(id="room1", name="Main Room", is_booked=True,
                              booked_by="00001", participants=["00001", "00010"])
        mock_st = _mock_store()

        reflection_error_invoke = AsyncMock(side_effect=[
            Exception("LLM error"),  # reflection fails
            _llm_resp("[]"),         # EA
        ])

        with (
            patch.object(mod, "load_all_employees", return_value=emp_data),
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_chat", new_callable=AsyncMock),
            patch.object(mod, "_set_participants_status", new_callable=AsyncMock),
            patch.object(mod, "_store", mock_st),
            patch.object(mod, "company_state", MagicMock(meeting_rooms={"room1": mock_room})),
            patch.object(mod, "make_llm", return_value=MagicMock()),
            patch.object(mod, "tracked_ainvoke", reflection_error_invoke),
            patch.object(mod, "_save_report"),
        ):
            result = await mod.end_ceo_meeting()
        assert result["status"] == "ended"

    @pytest.mark.asyncio
    async def test_end_ea_extraction_failure(self):
        from onemancompany.core import routine as mod

        mod._active_ceo_meeting = {
            "type": "discussion",
            "room_id": "room1",
            "room_name": "Main Room",
            "participants": [],
            "chat_history": [{"speaker": "CEO", "message": "Hello"}],
        }

        mock_room = MagicMock(id="room1", name="Main Room", is_booked=True,
                              booked_by="00001", participants=["00001"])
        mock_st = _mock_store()

        # EA fails
        ea_invoke = AsyncMock(side_effect=Exception("EA error"))
        with (
            patch.object(mod, "load_all_employees", return_value={}),
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_chat", new_callable=AsyncMock),
            patch.object(mod, "_set_participants_status", new_callable=AsyncMock),
            patch.object(mod, "_store", mock_st),
            patch.object(mod, "company_state", MagicMock(meeting_rooms={"room1": mock_room})),
            patch.object(mod, "make_llm", return_value=MagicMock()),
            patch.object(mod, "tracked_ainvoke", ea_invoke),
            patch.object(mod, "_save_report"),
        ):
            result = await mod.end_ceo_meeting()
        assert result["status"] == "ended"
        assert result["action_points"] == []

    @pytest.mark.asyncio
    async def test_end_no_room(self):
        from onemancompany.core import routine as mod

        mod._active_ceo_meeting = {
            "type": "discussion",
            "room_id": "room_missing",
            "room_name": "Missing",
            "participants": [],
            "chat_history": [],
        }

        with (
            patch.object(mod, "load_all_employees", return_value={}),
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_chat", new_callable=AsyncMock),
            patch.object(mod, "_store", _mock_store()),
            patch.object(mod, "company_state", MagicMock(meeting_rooms={})),
            patch.object(mod, "make_llm", return_value=MagicMock()),
            patch.object(mod, "tracked_ainvoke", new_callable=AsyncMock, return_value=_llm_resp("[]")),
            patch.object(mod, "_save_report"),
        ):
            result = await mod.end_ceo_meeting()
        assert result["status"] == "ended"

    @pytest.mark.asyncio
    async def test_end_create_project_failure(self):
        from onemancompany.core import routine as mod

        mod._active_ceo_meeting = {
            "type": "all_hands",
            "room_id": "room1",
            "room_name": "Room",
            "participants": [],
            "chat_history": [{"speaker": "CEO", "message": "Do things"}],
        }

        mock_room = MagicMock(id="room1", name="Room", is_booked=True,
                              booked_by=CEO_ID, participants=[CEO_ID])

        with (
            patch.object(mod, "load_all_employees", return_value={}),
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_chat", new_callable=AsyncMock),
            patch.object(mod, "_set_participants_status", new_callable=AsyncMock),
            patch.object(mod, "_store", _mock_store()),
            patch.object(mod, "company_state", MagicMock(meeting_rooms={"room1": mock_room})),
            patch.object(mod, "make_llm", return_value=MagicMock()),
            patch.object(mod, "tracked_ainvoke", new_callable=AsyncMock, return_value=_llm_resp('["Action 1"]')),
            patch.object(mod, "_save_report"),
            patch.object(mod, "_create_project_from_action_points",
                         new_callable=AsyncMock, side_effect=Exception("create failed")),
        ):
            result = await mod.end_ceo_meeting()
        assert result["status"] == "ended"
        assert result["project_id"] == ""

    @pytest.mark.asyncio
    async def test_end_cancels_existing_cancel_event(self):
        from onemancompany.core import routine as mod

        old_cancel = asyncio.Event()
        mod._ceo_meeting_cancel = old_cancel
        mod._active_ceo_meeting = {
            "type": "discussion",
            "room_id": "room1",
            "room_name": "Room",
            "participants": [],
            "chat_history": [],
        }

        mock_room = MagicMock(id="room1", name="Room", is_booked=True,
                              booked_by=CEO_ID, participants=[CEO_ID])

        with (
            patch.object(mod, "load_all_employees", return_value={}),
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_chat", new_callable=AsyncMock),
            patch.object(mod, "_set_participants_status", new_callable=AsyncMock),
            patch.object(mod, "_store", _mock_store()),
            patch.object(mod, "company_state", MagicMock(meeting_rooms={"room1": mock_room})),
            patch.object(mod, "make_llm", return_value=MagicMock()),
            patch.object(mod, "tracked_ainvoke", new_callable=AsyncMock, return_value=_llm_resp("[]")),
            patch.object(mod, "_save_report"),
        ):
            result = await mod.end_ceo_meeting()
        assert old_cancel.is_set()
        assert result["status"] == "ended"


# ---------------------------------------------------------------------------
# _create_project_from_action_points
# ---------------------------------------------------------------------------

class TestCreateProjectFromActionPoints:
    @pytest.mark.asyncio
    async def test_creates_project(self):
        from onemancompany.core import routine as mod

        mock_em = MagicMock()
        mock_tree = MagicMock()
        mock_root = MagicMock(id="root1")
        mock_ea_node = MagicMock(id="ea1")
        mock_tree.create_root.return_value = mock_root
        mock_tree.add_child.return_value = mock_ea_node

        with (
            patch("onemancompany.core.project_archive.async_create_project_from_task",
                  new_callable=AsyncMock, return_value=("proj1", "iter1")),
            patch("onemancompany.core.project_archive.get_project_dir", return_value="/tmp/proj1"),
            patch("onemancompany.core.task_tree.TaskTree", return_value=mock_tree),
            patch("onemancompany.core.vessel._save_project_tree"),
            patch("onemancompany.agents.tree_tools._add_to_project_team"),
            patch("onemancompany.core.agent_loop.employee_manager", mock_em),
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "event_bus", AsyncMock()),
        ):
            result = await mod._create_project_from_action_points(
                ["Action 1", "Action 2"], "all_hands", "transcript"
            )

        assert result == "proj1"
        mock_em.schedule_node.assert_called_once()


# ---------------------------------------------------------------------------
# Onboarding routine
# ---------------------------------------------------------------------------

class TestRunOnboardingRoutine:
    @pytest.mark.asyncio
    async def test_no_employee(self):
        from onemancompany.core import routine as mod
        with patch.object(mod, "load_employee", return_value=None):
            await mod.run_onboarding_routine("99999")

    @pytest.mark.asyncio
    async def test_onboarding_with_empty_principles(self):
        from onemancompany.core import routine as mod
        emp = _emp(work_principles="")
        mock_st = _mock_store()
        with (
            patch.object(mod, "load_employee", return_value=emp),
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_store", mock_st),
            patch("onemancompany.core.state.make_title", return_value="Junior Developer"),
        ):
            await mod.run_onboarding_routine("00010")
        mock_st.save_work_principles.assert_called_once()
        mock_st.save_employee.assert_called_once()

    @pytest.mark.asyncio
    async def test_onboarding_with_existing_principles(self):
        from onemancompany.core import routine as mod
        emp = _emp(work_principles="Already has principles")
        mock_st = _mock_store()
        with (
            patch.object(mod, "load_employee", return_value=emp),
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_store", mock_st),
        ):
            await mod.run_onboarding_routine("00010")
        mock_st.save_work_principles.assert_not_called()
        mock_st.save_employee.assert_called_once()


# ---------------------------------------------------------------------------
# Offboarding routine
# ---------------------------------------------------------------------------

class TestRunOffboardingRoutine:
    @pytest.mark.asyncio
    async def test_no_employee(self):
        from onemancompany.core import routine as mod
        with patch.object(mod, "load_employee", return_value=None):
            await mod.run_offboarding_routine("99999", "layoff")

    @pytest.mark.asyncio
    async def test_offboarding(self):
        from onemancompany.core import routine as mod
        emp = _emp()
        with (
            patch.object(mod, "load_employee", return_value=emp),
            patch.object(mod, "_publish", new_callable=AsyncMock),
            patch.object(mod, "_save_report"),
        ):
            await mod.run_offboarding_routine("00010", "performance")


# ---------------------------------------------------------------------------
# Performance meeting routine
# ---------------------------------------------------------------------------

class TestRunPerformanceMeeting:
    @pytest.mark.asyncio
    async def test_no_employee(self):
        from onemancompany.core import routine as mod
        with patch.object(mod, "load_employee", return_value=None):
            await mod.run_performance_meeting("99999", 3.5, "Good work")

    @pytest.mark.asyncio
    async def test_performance_meeting(self):
        from onemancompany.core import routine as mod
        emp = _emp()
        with (
            patch.object(mod, "load_employee", return_value=emp),
            patch.object(mod, "_publish", new_callable=AsyncMock),
        ):
            await mod.run_performance_meeting("00010", 3.5, "Keep it up")


# ---------------------------------------------------------------------------
# Snapshot provider
# ---------------------------------------------------------------------------

class TestRoutineSnapshot:
    def test_save_empty(self):
        from onemancompany.core.routine import _RoutineSnapshot, pending_reports
        pending_reports.clear()
        assert _RoutineSnapshot.save() == {}

    def test_save_with_data(self):
        from onemancompany.core.routine import _RoutineSnapshot, pending_reports
        pending_reports["r1"] = {"id": "r1", "data": "value"}
        try:
            result = _RoutineSnapshot.save()
            assert "pending_reports" in result
            assert "r1" in result["pending_reports"]
        finally:
            pending_reports.clear()

    def test_restore(self):
        from onemancompany.core.routine import _RoutineSnapshot, pending_reports
        pending_reports.clear()
        _RoutineSnapshot.restore({"pending_reports": {"r2": {"id": "r2"}}})
        assert "r2" in pending_reports
        pending_reports.clear()

    def test_restore_empty(self):
        from onemancompany.core.routine import _RoutineSnapshot, pending_reports
        pending_reports.clear()
        _RoutineSnapshot.restore({})
        assert len(pending_reports) == 0
