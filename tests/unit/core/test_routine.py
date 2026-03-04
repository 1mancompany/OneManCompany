"""Unit tests for core/routine.py — workflow-driven post-task meeting system."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from onemancompany.core.routine import (
    StepContext,
    _build_summary,
    _handle_ceo_review,
    _handle_meeting_prep,
    _resolve_handler,
    _save_report,
    _set_participants_status,
    execute_approved_actions,
    pending_reports,
    run_all_hands_meeting,
    run_post_task_routine,
)
from onemancompany.core.workflow_engine import (
    WorkflowDefinition,
    WorkflowStep,
    classify_step_owner,
)


# ---------------------------------------------------------------------------
# Lightweight fakes for Employee / MeetingRoom / CompanyState
# ---------------------------------------------------------------------------

@dataclass
class FakeEmployee:
    id: str
    name: str
    role: str
    skills: list[str] = field(default_factory=list)
    nickname: str = ""
    level: int = 1
    department: str = "Engineering"
    work_principles: str = ""
    status: str = "idle"
    current_quarter_tasks: int = 0
    performance_history: list = field(default_factory=list)


@dataclass
class FakeMeetingRoom:
    id: str
    name: str
    description: str = ""
    capacity: int = 10
    is_booked: bool = False
    booked_by: str = ""
    participants: list[str] = field(default_factory=list)


def _make_workflow(steps: list[WorkflowStep] | None = None) -> WorkflowDefinition:
    return WorkflowDefinition(
        name="test_workflow",
        flow_id="test_flow",
        owner="HR",
        collaborators="COO",
        trigger="manual",
        steps=steps or [],
    )


def _make_step(index: int = 0, title: str = "Test Step", owner: str = "HR") -> WorkflowStep:
    return WorkflowStep(
        index=index,
        title=title,
        owner=owner,
        instructions=["Do something"],
        output_description="Done",
        raw_text=f"## {title}\n",
    )


def _make_llm_response(content: str = "Test response") -> MagicMock:
    resp = MagicMock()
    resp.content = content
    return resp


# ---------------------------------------------------------------------------
# Common patches (applied to many tests)
# ---------------------------------------------------------------------------

def _mock_company_state(employees: dict | None = None, rooms: dict | None = None):
    """Create a mock company_state with given employees and rooms."""
    state = MagicMock()
    state.employees = employees or {}
    state.ex_employees = {}
    state.tools = {}
    state.meeting_rooms = rooms or {}
    state.company_culture = []
    return state


# ---------------------------------------------------------------------------
# _set_participants_status
# ---------------------------------------------------------------------------

class TestSetParticipantsStatus:
    def test_sets_status(self):
        emp = FakeEmployee(id="00005", name="Alice", role="Engineer")
        state = _mock_company_state(employees={"00005": emp})
        with patch("onemancompany.core.routine.company_state", state):
            _set_participants_status(["00005"], "in_meeting")
        assert emp.status == "in_meeting"

    def test_skips_unknown_ids(self):
        state = _mock_company_state()
        with patch("onemancompany.core.routine.company_state", state):
            _set_participants_status(["nonexistent"], "idle")  # should not raise

    def test_sets_multiple(self):
        emp1 = FakeEmployee(id="00005", name="Alice", role="Engineer")
        emp2 = FakeEmployee(id="00006", name="Bob", role="Designer")
        state = _mock_company_state(employees={"00005": emp1, "00006": emp2})
        with patch("onemancompany.core.routine.company_state", state):
            _set_participants_status(["00005", "00006"], "working")
        assert emp1.status == "working"
        assert emp2.status == "working"


# ---------------------------------------------------------------------------
# StepContext
# ---------------------------------------------------------------------------

class TestStepContext:
    def _make_ctx(self, **overrides):
        defaults = dict(
            task_summary="Build feature X",
            participants=["00005", "00006"],
            room_id="room-1",
            workflow=_make_workflow(),
            meeting_doc={},
        )
        defaults.update(overrides)
        return StepContext(**defaults)

    def test_defaults(self):
        ctx = self._make_ctx()
        assert ctx.task_summary == "Build feature X"
        assert ctx.results == {}
        assert ctx.self_evaluations == []
        assert ctx.coo_report == ""

    def test_format_project_timeline_empty(self):
        ctx = self._make_ctx(project_record={})
        assert ctx.format_project_timeline() == ""

    def test_format_project_timeline_with_entries(self):
        emp = FakeEmployee(id="00005", name="Alice", role="Eng", nickname="小A")
        state = _mock_company_state(employees={"00005": emp})
        ctx = self._make_ctx(project_record={
            "timeline": [
                {"employee_id": "00005", "action": "code_commit", "detail": "Fixed bug"},
            ]
        })
        with patch("onemancompany.core.routine.company_state", state):
            text = ctx.format_project_timeline()
        assert "Alice" in text
        assert "code_commit" in text

    def test_format_company_culture_empty(self):
        state = _mock_company_state()
        state.company_culture = []
        ctx = self._make_ctx()
        with patch("onemancompany.core.routine.company_state", state):
            assert ctx.format_company_culture() == ""

    def test_format_company_culture_with_items(self):
        state = _mock_company_state()
        state.company_culture = [{"content": "Be excellent"}]
        ctx = self._make_ctx()
        with patch("onemancompany.core.routine.company_state", state):
            text = ctx.format_company_culture()
        assert "Be excellent" in text

    def test_get_employee_actions_found(self):
        ctx = self._make_ctx(project_record={
            "timeline": [
                {"employee_id": "00005", "action": "review", "detail": "Reviewed PR"},
                {"employee_id": "00006", "action": "code", "detail": "Wrote tests"},
            ]
        })
        text = ctx.get_employee_actions("00005")
        assert "review" in text
        assert "Wrote tests" not in text

    def test_get_employee_actions_none(self):
        ctx = self._make_ctx(project_record={
            "timeline": [
                {"employee_id": "00006", "action": "code", "detail": "Wrote tests"},
            ]
        })
        text = ctx.get_employee_actions("00005")
        assert "没有" in text

    def test_get_employee_actions_no_timeline(self):
        ctx = self._make_ctx(project_record={})
        text = ctx.get_employee_actions("00005")
        assert "没有" in text


# ---------------------------------------------------------------------------
# _resolve_handler
# ---------------------------------------------------------------------------

class TestResolveHandler:
    def test_title_match_self_evaluation(self):
        step = _make_step(title="Phase 2: Self-Evaluation")
        handler = _resolve_handler(step)
        assert handler.__name__ == "_handle_self_evaluation"

    def test_title_match_review_preparation(self):
        step = _make_step(title="Phase 1: Review Preparation")
        handler = _resolve_handler(step)
        assert handler.__name__ == "_handle_meeting_prep"

    def test_title_match_senior_peer_review(self):
        step = _make_step(title="Phase 3: Senior Peer Review")
        handler = _resolve_handler(step)
        assert handler.__name__ == "_handle_senior_review"

    def test_title_match_hr_summary(self):
        step = _make_step(title="HR Summary")
        handler = _resolve_handler(step)
        assert handler.__name__ == "_handle_hr_summary"

    def test_title_match_coo_operations_report(self):
        step = _make_step(title="COO Operations Report")
        handler = _resolve_handler(step)
        assert handler.__name__ == "_handle_coo_report"

    def test_title_match_asset_consolidation(self):
        step = _make_step(title="Asset Consolidation Review")
        handler = _resolve_handler(step)
        assert handler.__name__ == "_handle_asset_consolidation"

    def test_title_match_employee_open_floor(self):
        step = _make_step(title="Employee Open Floor Discussion")
        handler = _resolve_handler(step)
        assert handler.__name__ == "_handle_employee_open_floor"

    def test_title_match_action_plan(self):
        step = _make_step(title="Action Plan Generation")
        handler = _resolve_handler(step)
        assert handler.__name__ == "_handle_action_plan"

    def test_title_match_ceo_approval(self):
        step = _make_step(title="CEO Approval")
        handler = _resolve_handler(step)
        assert handler.__name__ == "_handle_ceo_review"

    def test_owner_based_fallback_employees(self):
        step = _make_step(title="Some Unknown Title", owner="Each participating employee")
        handler = _resolve_handler(step)
        assert handler.__name__ == "_handle_self_evaluation"

    def test_owner_based_fallback_senior(self):
        step = _make_step(title="Unknown Title", owner="Senior employees")
        handler = _resolve_handler(step)
        assert handler.__name__ == "_handle_senior_review"

    def test_owner_based_fallback_coo_hr(self):
        step = _make_step(title="Unknown", owner="COO + HR")
        handler = _resolve_handler(step)
        assert handler.__name__ == "_handle_action_plan"

    def test_owner_based_fallback_ceo(self):
        step = _make_step(title="Unknown", owner="CEO")
        handler = _resolve_handler(step)
        assert handler.__name__ == "_handle_ceo_review"

    def test_generic_fallback(self):
        step = _make_step(title="Completely Unknown", owner="Random Person")
        handler = _resolve_handler(step)
        assert handler.__name__ == "_handle_generic_step"


# ---------------------------------------------------------------------------
# _handle_meeting_prep (simple handler)
# ---------------------------------------------------------------------------

class TestHandleMeetingPrep:
    @pytest.mark.asyncio
    async def test_returns_prepared(self):
        step = _make_step(title="Review Preparation")
        ctx = StepContext(
            task_summary="test",
            participants=[],
            room_id="r1",
            workflow=_make_workflow(),
            meeting_doc={},
        )
        with patch("onemancompany.core.routine._publish", new_callable=AsyncMock):
            result = await _handle_meeting_prep(step, ctx)
        assert result == {"status": "prepared"}


# ---------------------------------------------------------------------------
# _handle_ceo_review (simple handler)
# ---------------------------------------------------------------------------

class TestHandleCeoReview:
    @pytest.mark.asyncio
    async def test_returns_awaiting(self):
        step = _make_step(title="CEO Approval")
        ctx = StepContext(
            task_summary="test",
            participants=[],
            room_id="r1",
            workflow=_make_workflow(),
            meeting_doc={},
        )
        with patch("onemancompany.core.routine._publish", new_callable=AsyncMock):
            result = await _handle_ceo_review(step, ctx)
        assert result == {"status": "awaiting_ceo_review"}


# ---------------------------------------------------------------------------
# _build_summary
# ---------------------------------------------------------------------------

class TestBuildSummary:
    def test_minimal_doc(self):
        doc = {
            "timestamp": "2024-01-01T12:00:00",
            "task_summary": "Build feature",
            "phase1": {},
            "phase2": {},
        }
        summary = _build_summary(doc)
        assert "Build feature" in summary

    def test_with_workflow(self):
        doc = {
            "timestamp": "2024-01-01T12:00:00",
            "task_summary": "Build feature",
            "workflow": "project_retrospective_workflow",
            "phase1": {},
            "phase2": {},
        }
        summary = _build_summary(doc)
        assert "project_retrospective_workflow" in summary

    def test_with_hr_summary(self):
        doc = {
            "timestamp": "2024-01-01T12:00:00",
            "task_summary": "task",
            "phase1": {
                "hr_summary": [
                    {"employee": "Alice", "improvements": ["Be faster"]},
                ]
            },
            "phase2": {},
        }
        summary = _build_summary(doc)
        assert "Alice" in summary
        assert "Be faster" in summary

    def test_with_coo_report(self):
        doc = {
            "timestamp": "2024-01-01T12:00:00",
            "task_summary": "task",
            "phase1": {},
            "phase2": {
                "coo_report": "Company is doing well",
                "employee_feedback": [
                    {"name": "Bob", "feedback": "Need more tools"},
                ],
            },
        }
        summary = _build_summary(doc)
        assert "Company is doing well" in summary
        assert "Bob" in summary

    def test_with_asset_suggestions(self):
        doc = {
            "timestamp": "2024-01-01T12:00:00",
            "task_summary": "task",
            "phase1": {},
            "phase2": {},
            "asset_suggestions": [
                {"name": "Widget", "description": "A useful tool", "files": ["widget.py"]},
            ],
        }
        summary = _build_summary(doc)
        assert "Widget" in summary
        assert "widget.py" in summary


# ---------------------------------------------------------------------------
# _save_report
# ---------------------------------------------------------------------------

class TestSaveReport:
    def test_saves_yaml(self, tmp_path, monkeypatch):
        monkeypatch.setattr("onemancompany.core.routine.REPORTS_DIR", tmp_path)
        doc = {"id": "r123", "task_summary": "test"}
        _save_report("r123", doc)
        path = tmp_path / "r123.yaml"
        assert path.exists()
        loaded = yaml.safe_load(path.read_text())
        assert loaded["id"] == "r123"


# ---------------------------------------------------------------------------
# run_post_task_routine — integration-level tests with mocks
# ---------------------------------------------------------------------------

class TestRunPostTaskRoutine:
    @pytest.fixture
    def mock_env(self, tmp_path, monkeypatch):
        """Set up a fully mocked environment for run_post_task_routine."""
        emp1 = FakeEmployee(id="00005", name="Alice", role="Engineer", level=2, nickname="小A")
        emp2 = FakeEmployee(id="00006", name="Bob", role="Engineer", level=1, nickname="小B")
        room = FakeMeetingRoom(id="room-1", name="Meeting Room A")
        state = _mock_company_state(
            employees={"00005": emp1, "00006": emp2},
            rooms={"room-1": room},
        )

        monkeypatch.setattr("onemancompany.core.routine.REPORTS_DIR", tmp_path)
        monkeypatch.setattr("onemancompany.core.routine.company_state", state)
        monkeypatch.setattr("onemancompany.core.routine.FOUNDING_LEVEL", 4)
        monkeypatch.setattr("onemancompany.core.routine.EA_ID", "00004")

        # Mock update_employee_performance to be a no-op
        monkeypatch.setattr("onemancompany.core.routine.update_employee_performance", lambda *a: None)

        return state

    @pytest.mark.asyncio
    async def test_returns_early_with_no_employees(self, monkeypatch):
        state = _mock_company_state()
        monkeypatch.setattr("onemancompany.core.routine.company_state", state)
        with patch("onemancompany.core.routine._publish", new_callable=AsyncMock):
            await run_post_task_routine("task")
        # Should return without doing anything

    @pytest.mark.asyncio
    async def test_returns_early_with_single_participant(self, mock_env, monkeypatch):
        """Solo tasks (1 participant after filtering) skip the meeting."""
        mock_env.employees = {"00005": FakeEmployee(id="00005", name="Alice", role="Eng", level=1)}
        monkeypatch.setattr("onemancompany.core.routine.load_workflows", lambda: {})

        with patch("onemancompany.core.routine._publish", new_callable=AsyncMock):
            await run_post_task_routine("task", participants=["00005"])

    @pytest.mark.asyncio
    async def test_fallback_when_no_workflow(self, mock_env, monkeypatch):
        """Falls back to legacy routine when no workflow doc exists."""
        monkeypatch.setattr("onemancompany.core.routine.load_workflows", lambda: {})

        mock_publish = AsyncMock()
        mock_ainvoke = AsyncMock(return_value=_make_llm_response('[{"name": "all", "review": "ok"}]'))

        with patch("onemancompany.core.routine._publish", mock_publish), \
             patch("onemancompany.core.routine._chat", new_callable=AsyncMock), \
             patch("onemancompany.core.routine.tracked_ainvoke", mock_ainvoke), \
             patch("onemancompany.core.routine.make_llm", return_value=MagicMock()), \
             patch("onemancompany.core.routine.get_employee_skills_prompt", return_value=""), \
             patch("onemancompany.core.routine.get_employee_tools_prompt", return_value=""):
            await run_post_task_routine("task", participants=["00005", "00006"])

        # Should have published meeting events
        event_types = [call.args[0] for call in mock_publish.call_args_list]
        assert "meeting_booked" in event_types
        assert "meeting_released" in event_types

    @pytest.mark.asyncio
    async def test_no_room_available(self, mock_env):
        """When all rooms are booked, the meeting is deferred."""
        # Book the only room
        mock_env.meeting_rooms["room-1"].is_booked = True

        workflow_md = "## Phase 1: Self-Evaluation\n- **Responsible**: HR\n"
        with patch("onemancompany.core.routine.load_workflows", return_value={"project_retrospective_workflow": workflow_md}), \
             patch("onemancompany.core.routine._publish", new_callable=AsyncMock) as mock_publish:
            await run_post_task_routine("task", participants=["00005", "00006"])

        # Should publish about no room available
        messages = [call.args[1].get("message", "") for call in mock_publish.call_args_list if "message" in call.args[1]]
        assert any("延期" in m for m in messages)

    @pytest.mark.asyncio
    async def test_workflow_driven_meeting(self, mock_env, monkeypatch):
        """Full workflow-driven meeting runs all steps."""
        workflow_md = (
            "# Test Workflow\n\n"
            "- **Flow ID**: test\n"
            "- **Owner**: HR\n"
            "- **Collaborators**: COO\n"
            "- **Trigger**: manual\n\n"
            "---\n\n"
            "## Phase 1: Self-Evaluation\n\n"
            "- **Responsible**: Each participating employee\n"
            "- **Steps**:\n  1. Review work\n"
            "- **Output**: Self eval\n\n"
            "## Phase 2: CEO Approval\n\n"
            "- **Responsible**: CEO\n"
            "- **Output**: Approval\n"
        )

        mock_ainvoke = AsyncMock(return_value=_make_llm_response("Good self-eval"))
        mock_publish = AsyncMock()

        with patch("onemancompany.core.routine.load_workflows", return_value={"project_retrospective_workflow": workflow_md}), \
             patch("onemancompany.core.routine._publish", mock_publish), \
             patch("onemancompany.core.routine._chat", new_callable=AsyncMock), \
             patch("onemancompany.core.routine.tracked_ainvoke", mock_ainvoke), \
             patch("onemancompany.core.routine.make_llm", return_value=MagicMock()), \
             patch("onemancompany.core.routine.get_employee_skills_prompt", return_value=""), \
             patch("onemancompany.core.routine.get_employee_tools_prompt", return_value=""):
            await run_post_task_routine("Build X", participants=["00005", "00006"])

        # Room should be released after
        room = mock_env.meeting_rooms["room-1"]
        assert room.is_booked is False
        assert room.booked_by == ""

        # Report should have been saved
        event_types = [call.args[0] for call in mock_publish.call_args_list]
        assert "meeting_booked" in event_types
        assert "meeting_released" in event_types

    @pytest.mark.asyncio
    async def test_room_released_on_exception(self, mock_env, monkeypatch):
        """Room is released even if an exception occurs during the meeting."""
        workflow_md = (
            "# Fail Workflow\n\n"
            "## Phase 1: Self-Evaluation\n\n"
            "- **Responsible**: Each participating employee\n"
            "- **Output**: Boom\n"
        )

        async def failing_ainvoke(*args, **kwargs):
            raise RuntimeError("LLM exploded")

        with patch("onemancompany.core.routine.load_workflows", return_value={"project_retrospective_workflow": workflow_md}), \
             patch("onemancompany.core.routine._publish", new_callable=AsyncMock), \
             patch("onemancompany.core.routine._chat", new_callable=AsyncMock), \
             patch("onemancompany.core.routine.tracked_ainvoke", failing_ainvoke), \
             patch("onemancompany.core.routine.make_llm", return_value=MagicMock()), \
             patch("onemancompany.core.routine.get_employee_skills_prompt", return_value=""), \
             patch("onemancompany.core.routine.get_employee_tools_prompt", return_value=""):
            with pytest.raises(RuntimeError, match="LLM exploded"):
                await run_post_task_routine("task", participants=["00005", "00006"])

        # Room should still be released
        room = mock_env.meeting_rooms["room-1"]
        assert room.is_booked is False

    @pytest.mark.asyncio
    async def test_increments_employee_tasks(self, mock_env, monkeypatch):
        """Participating normal employees get current_quarter_tasks incremented."""
        monkeypatch.setattr("onemancompany.core.routine.load_workflows", lambda: {})
        emp = mock_env.employees["00005"]
        assert emp.current_quarter_tasks == 0

        mock_ainvoke = AsyncMock(return_value=_make_llm_response('[{"name": "all", "review": "ok"}]'))
        with patch("onemancompany.core.routine._publish", new_callable=AsyncMock), \
             patch("onemancompany.core.routine._chat", new_callable=AsyncMock), \
             patch("onemancompany.core.routine.tracked_ainvoke", mock_ainvoke), \
             patch("onemancompany.core.routine.make_llm", return_value=MagicMock()), \
             patch("onemancompany.core.routine.get_employee_skills_prompt", return_value=""), \
             patch("onemancompany.core.routine.get_employee_tools_prompt", return_value=""):
            await run_post_task_routine("task", participants=["00005", "00006"])

        assert emp.current_quarter_tasks == 1


# ---------------------------------------------------------------------------
# run_all_hands_meeting
# ---------------------------------------------------------------------------

class TestRunAllHandsMeeting:
    @pytest.mark.asyncio
    async def test_returns_early_with_no_employees(self, monkeypatch):
        state = _mock_company_state()
        monkeypatch.setattr("onemancompany.core.routine.company_state", state)
        with patch("onemancompany.core.routine._publish", new_callable=AsyncMock):
            await run_all_hands_meeting("Be excellent")

    @pytest.mark.asyncio
    async def test_no_room_defers(self, monkeypatch):
        emp = FakeEmployee(id="00005", name="Alice", role="Eng")
        room = FakeMeetingRoom(id="r1", name="R1", is_booked=True, capacity=20)
        state = _mock_company_state(
            employees={"00005": emp},
            rooms={"r1": room},
        )
        monkeypatch.setattr("onemancompany.core.routine.company_state", state)

        mock_publish = AsyncMock()
        with patch("onemancompany.core.routine._publish", mock_publish):
            await run_all_hands_meeting("Be excellent")

        messages = [
            call.args[1].get("message", "")
            for call in mock_publish.call_args_list
            if len(call.args) >= 2 and isinstance(call.args[1], dict)
        ]
        assert any("延期" in m for m in messages)

    @pytest.mark.asyncio
    async def test_successful_meeting(self, tmp_path, monkeypatch):
        emp = FakeEmployee(id="00005", name="Alice", role="Eng", nickname="小A")
        room = FakeMeetingRoom(id="r1", name="Grand Hall", capacity=50)
        state = _mock_company_state(
            employees={"00005": emp},
            rooms={"r1": room},
        )
        monkeypatch.setattr("onemancompany.core.routine.company_state", state)
        monkeypatch.setattr("onemancompany.core.routine.REPORTS_DIR", tmp_path)
        monkeypatch.setattr("onemancompany.core.routine.CEO_ID", "00001")

        mock_ainvoke = AsyncMock(return_value=_make_llm_response("I will do better"))
        mock_publish = AsyncMock()

        with patch("onemancompany.core.routine._publish", mock_publish), \
             patch("onemancompany.core.routine._chat", new_callable=AsyncMock), \
             patch("onemancompany.core.routine.tracked_ainvoke", mock_ainvoke), \
             patch("onemancompany.core.routine.make_llm", return_value=MagicMock()), \
             patch("onemancompany.core.routine.get_employee_skills_prompt", return_value=""), \
             patch("onemancompany.core.routine.get_employee_tools_prompt", return_value=""):
            await run_all_hands_meeting("Work harder")

        # Room should be released
        assert room.is_booked is False

        # Events should include meeting_booked and meeting_released
        event_types = [call.args[0] for call in mock_publish.call_args_list]
        assert "meeting_booked" in event_types
        assert "meeting_released" in event_types
        assert "guidance_noted" in event_types

    @pytest.mark.asyncio
    async def test_room_released_on_error(self, monkeypatch):
        emp = FakeEmployee(id="00005", name="Alice", role="Eng")
        room = FakeMeetingRoom(id="r1", name="Room", capacity=20)
        state = _mock_company_state(
            employees={"00005": emp},
            rooms={"r1": room},
        )
        monkeypatch.setattr("onemancompany.core.routine.company_state", state)
        monkeypatch.setattr("onemancompany.core.routine.CEO_ID", "00001")

        async def failing_ainvoke(*args, **kwargs):
            raise RuntimeError("LLM fail")

        with patch("onemancompany.core.routine._publish", new_callable=AsyncMock), \
             patch("onemancompany.core.routine._chat", new_callable=AsyncMock), \
             patch("onemancompany.core.routine.tracked_ainvoke", failing_ainvoke), \
             patch("onemancompany.core.routine.make_llm", return_value=MagicMock()), \
             patch("onemancompany.core.routine.get_employee_skills_prompt", return_value=""), \
             patch("onemancompany.core.routine.get_employee_tools_prompt", return_value=""):
            with pytest.raises(RuntimeError):
                await run_all_hands_meeting("msg")

        assert room.is_booked is False


# ---------------------------------------------------------------------------
# execute_approved_actions
# ---------------------------------------------------------------------------

class TestExecuteApprovedActions:
    @pytest.mark.asyncio
    async def test_report_not_found(self):
        with patch("onemancompany.core.routine._publish", new_callable=AsyncMock), \
             patch("onemancompany.core.routine.REPORTS_DIR", MagicMock()):
            result = await execute_approved_actions("nonexistent", [0])
        assert "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_no_actions(self, tmp_path, monkeypatch):
        monkeypatch.setattr("onemancompany.core.routine.REPORTS_DIR", tmp_path)
        doc = {"id": "r1", "action_items": []}
        pending_reports["r1"] = doc

        with patch("onemancompany.core.routine._publish", new_callable=AsyncMock):
            result = await execute_approved_actions("r1", [])
        assert "No actions" in result

    @pytest.mark.asyncio
    async def test_asset_consolidation_only(self, tmp_path, monkeypatch):
        monkeypatch.setattr("onemancompany.core.routine.REPORTS_DIR", tmp_path)
        doc = {
            "id": "r2",
            "action_items": [
                {
                    "type": "asset_consolidation",
                    "source": "COO",
                    "description": "Consolidate widget",
                    "name": "widget",
                    "asset_description": "A widget tool",
                    "project_dir": str(tmp_path),
                    "files": ["widget.py"],
                },
            ],
        }
        pending_reports["r2"] = doc

        mock_register = MagicMock()
        mock_register.invoke = MagicMock(return_value={"status": "ok"})
        with patch("onemancompany.core.routine._publish", new_callable=AsyncMock), \
             patch("onemancompany.agents.coo_agent.register_asset", mock_register):
            result = await execute_approved_actions("r2", [0])
        assert "资产" in result

    @pytest.mark.asyncio
    async def test_regular_actions_dispatched_to_coo(self, tmp_path, monkeypatch):
        monkeypatch.setattr("onemancompany.core.routine.REPORTS_DIR", tmp_path)
        doc = {
            "id": "r3",
            "action_items": [
                {"source": "COO", "description": "Improve process", "priority": "high"},
            ],
        }
        pending_reports["r3"] = doc

        mock_loop = MagicMock()
        mock_loop.push_task = MagicMock()

        with patch("onemancompany.core.routine._publish", new_callable=AsyncMock), \
             patch("onemancompany.core.agent_loop.get_agent_loop", return_value=mock_loop):
            result = await execute_approved_actions("r3", [0])

        assert "推送" in result or "COO" in result
        mock_loop.push_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_loads_from_disk_fallback(self, tmp_path, monkeypatch):
        monkeypatch.setattr("onemancompany.core.routine.REPORTS_DIR", tmp_path)

        doc = {"id": "disk-r", "action_items": []}
        _save_report.__wrapped__ if hasattr(_save_report, '__wrapped__') else None
        report_path = tmp_path / "disk-r.yaml"
        with open(report_path, "w") as f:
            yaml.dump(doc, f)

        with patch("onemancompany.core.routine._publish", new_callable=AsyncMock):
            result = await execute_approved_actions("disk-r", [])
        assert "No actions" in result
