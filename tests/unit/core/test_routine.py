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

    @pytest.mark.asyncio
    async def test_coo_loop_not_found(self, tmp_path, monkeypatch):
        """COO agent loop not found for dispatching actions."""
        monkeypatch.setattr("onemancompany.core.routine.REPORTS_DIR", tmp_path)
        doc = {
            "id": "r-no-coo",
            "action_items": [
                {"source": "COO", "description": "Do stuff", "priority": "high"},
            ],
        }
        pending_reports["r-no-coo"] = doc

        with patch("onemancompany.core.routine._publish", new_callable=AsyncMock), \
             patch("onemancompany.core.agent_loop.get_agent_loop", return_value=None):
            result = await execute_approved_actions("r-no-coo", [0])

        assert "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_asset_plus_regular_actions(self, tmp_path, monkeypatch):
        """Mix of asset consolidation and regular actions."""
        monkeypatch.setattr("onemancompany.core.routine.REPORTS_DIR", tmp_path)
        doc = {
            "id": "r-mix",
            "action_items": [
                {
                    "type": "asset_consolidation",
                    "source": "COO",
                    "description": "Save widget",
                    "name": "widget",
                    "asset_description": "A widget",
                    "project_dir": str(tmp_path),
                    "files": ["widget.py"],
                },
                {"source": "HR", "description": "Update docs", "priority": "medium"},
            ],
        }
        pending_reports["r-mix"] = doc

        mock_register = MagicMock()
        mock_register.invoke = MagicMock(return_value={"status": "ok"})
        mock_loop = MagicMock()

        with patch("onemancompany.core.routine._publish", new_callable=AsyncMock), \
             patch("onemancompany.agents.coo_agent.register_asset", mock_register), \
             patch("onemancompany.core.agent_loop.get_agent_loop", return_value=mock_loop):
            result = await execute_approved_actions("r-mix", [0, 1])

        assert "资产" in result
        mock_loop.push_task.assert_called_once()


# ---------------------------------------------------------------------------
# Event helpers
# ---------------------------------------------------------------------------

class TestEventHelpers:
    @pytest.mark.asyncio
    async def test_publish(self, monkeypatch):
        from onemancompany.core import routine as routine_mod
        mock_bus = MagicMock(publish=AsyncMock())
        monkeypatch.setattr(routine_mod, "event_bus", mock_bus)

        await routine_mod._publish("test_type", {"key": "val"})
        mock_bus.publish.assert_awaited_once()
        event = mock_bus.publish.call_args[0][0]
        assert event.type == "test_type"
        assert event.agent == "ROUTINE"

    @pytest.mark.asyncio
    async def test_chat(self, monkeypatch):
        from onemancompany.core import routine as routine_mod
        mock_bus = MagicMock(publish=AsyncMock())
        monkeypatch.setattr(routine_mod, "event_bus", mock_bus)

        await routine_mod._chat("room-1", "Alice", "Engineer", "Hello")
        mock_bus.publish.assert_awaited_once()
        event = mock_bus.publish.call_args[0][0]
        assert event.type == "meeting_chat"
        assert event.payload["room_id"] == "room-1"
        assert event.payload["speaker"] == "Alice"


# ---------------------------------------------------------------------------
# _handle_self_evaluation
# ---------------------------------------------------------------------------

class TestHandleSelfEvaluation:
    @pytest.mark.asyncio
    async def test_evaluates_all_participants(self, monkeypatch):
        from onemancompany.core.routine import _handle_self_evaluation

        emp1 = FakeEmployee(id="00005", name="Alice", role="Engineer", level=2, nickname="小A",
                            work_principles="Be thorough")
        emp2 = FakeEmployee(id="00006", name="Bob", role="Designer", level=1, nickname="小B")
        state = _mock_company_state(employees={"00005": emp1, "00006": emp2})

        step = _make_step(title="Self-Evaluation", owner="employees")
        ctx = StepContext(
            task_summary="Build feature",
            participants=["00005", "00006"],
            room_id="r1",
            workflow=_make_workflow(),
            meeting_doc={},
            project_record={"timeline": [{"employee_id": "00005", "action": "code", "detail": "Wrote code"}]},
        )

        mock_ainvoke = AsyncMock(return_value=_make_llm_response("I did my best"))
        with patch("onemancompany.core.routine.company_state", state), \
             patch("onemancompany.core.routine._publish", new_callable=AsyncMock), \
             patch("onemancompany.core.routine._chat", new_callable=AsyncMock), \
             patch("onemancompany.core.routine.tracked_ainvoke", mock_ainvoke), \
             patch("onemancompany.core.routine.make_llm", return_value=MagicMock()), \
             patch("onemancompany.core.routine.get_employee_skills_prompt", return_value=""), \
             patch("onemancompany.core.routine.get_employee_tools_prompt", return_value=""):
            result = await _handle_self_evaluation(step, ctx)

        assert len(ctx.self_evaluations) == 2
        assert ctx.self_evaluations[0]["employee_id"] == "00005"
        assert ctx.self_evaluations[1]["employee_id"] == "00006"
        assert "self_evaluations" in result

    @pytest.mark.asyncio
    async def test_skips_unknown_employee(self, monkeypatch):
        from onemancompany.core.routine import _handle_self_evaluation

        state = _mock_company_state(employees={})

        step = _make_step(title="Self-Evaluation")
        ctx = StepContext(
            task_summary="test",
            participants=["nonexistent"],
            room_id="r1",
            workflow=_make_workflow(),
            meeting_doc={},
        )

        with patch("onemancompany.core.routine.company_state", state), \
             patch("onemancompany.core.routine._publish", new_callable=AsyncMock), \
             patch("onemancompany.core.routine._chat", new_callable=AsyncMock), \
             patch("onemancompany.core.routine.make_llm", return_value=MagicMock()):
            result = await _handle_self_evaluation(step, ctx)

        assert ctx.self_evaluations == []


# ---------------------------------------------------------------------------
# _handle_senior_review
# ---------------------------------------------------------------------------

class TestHandleSeniorReview:
    @pytest.mark.asyncio
    async def test_senior_reviews_juniors(self, monkeypatch):
        from onemancompany.core.routine import _handle_senior_review

        senior = FakeEmployee(id="00005", name="Alice", role="Lead", level=3, nickname="小A")
        junior = FakeEmployee(id="00006", name="Bob", role="Engineer", level=1, nickname="小B")
        state = _mock_company_state(employees={"00005": senior, "00006": junior})

        step = _make_step(title="Senior Peer Review")
        ctx = StepContext(
            task_summary="Build feature",
            participants=["00005", "00006"],
            room_id="r1",
            workflow=_make_workflow(),
            meeting_doc={},
            project_record={"timeline": []},
        )
        ctx.self_evaluations = [
            {"employee_id": "00006", "name": "Bob", "nickname": "小B", "level": 1, "evaluation": "I coded well"},
        ]

        review_json = '[{"name": "Bob", "review": "Good job"}]'
        mock_ainvoke = AsyncMock(return_value=_make_llm_response(review_json))

        with patch("onemancompany.core.routine.company_state", state), \
             patch("onemancompany.core.routine._publish", new_callable=AsyncMock), \
             patch("onemancompany.core.routine._chat", new_callable=AsyncMock), \
             patch("onemancompany.core.routine.tracked_ainvoke", mock_ainvoke), \
             patch("onemancompany.core.routine.make_llm", return_value=MagicMock()), \
             patch("onemancompany.core.routine.get_employee_skills_prompt", return_value=""), \
             patch("onemancompany.core.routine.get_employee_tools_prompt", return_value=""):
            result = await _handle_senior_review(step, ctx)

        assert len(ctx.senior_reviews) == 1
        assert ctx.senior_reviews[0]["reviewer"] == "Alice"
        assert "senior_reviews" in result

    @pytest.mark.asyncio
    async def test_bad_json_review(self, monkeypatch):
        from onemancompany.core.routine import _handle_senior_review

        senior = FakeEmployee(id="00005", name="Alice", role="Lead", level=3, nickname="小A")
        junior = FakeEmployee(id="00006", name="Bob", role="Engineer", level=1, nickname="小B")
        state = _mock_company_state(employees={"00005": senior, "00006": junior})

        step = _make_step(title="Senior Peer Review")
        ctx = StepContext(
            task_summary="task",
            participants=["00005", "00006"],
            room_id="r1",
            workflow=_make_workflow(),
            meeting_doc={},
        )
        ctx.self_evaluations = [
            {"employee_id": "00006", "name": "Bob", "nickname": "小B", "level": 1, "evaluation": "ok"},
        ]

        mock_ainvoke = AsyncMock(return_value=_make_llm_response("Not valid json at all"))

        with patch("onemancompany.core.routine.company_state", state), \
             patch("onemancompany.core.routine._publish", new_callable=AsyncMock), \
             patch("onemancompany.core.routine._chat", new_callable=AsyncMock), \
             patch("onemancompany.core.routine.tracked_ainvoke", mock_ainvoke), \
             patch("onemancompany.core.routine.make_llm", return_value=MagicMock()), \
             patch("onemancompany.core.routine.get_employee_skills_prompt", return_value=""), \
             patch("onemancompany.core.routine.get_employee_tools_prompt", return_value=""):
            result = await _handle_senior_review(step, ctx)

        # Should fall back to wrapping the raw text
        assert len(ctx.senior_reviews) == 1
        assert ctx.senior_reviews[0]["reviews"][0]["name"] == "all"

    @pytest.mark.asyncio
    async def test_no_juniors_to_review(self, monkeypatch):
        from onemancompany.core.routine import _handle_senior_review

        # All same level — no one reviews anyone
        emp1 = FakeEmployee(id="00005", name="Alice", role="Engineer", level=2, nickname="小A")
        emp2 = FakeEmployee(id="00006", name="Bob", role="Engineer", level=2, nickname="小B")
        state = _mock_company_state(employees={"00005": emp1, "00006": emp2})

        step = _make_step(title="Senior Peer Review")
        ctx = StepContext(
            task_summary="task",
            participants=["00005", "00006"],
            room_id="r1",
            workflow=_make_workflow(),
            meeting_doc={},
        )

        with patch("onemancompany.core.routine.company_state", state), \
             patch("onemancompany.core.routine._publish", new_callable=AsyncMock), \
             patch("onemancompany.core.routine._chat", new_callable=AsyncMock):
            result = await _handle_senior_review(step, ctx)

        assert ctx.senior_reviews == []


# ---------------------------------------------------------------------------
# _handle_hr_summary
# ---------------------------------------------------------------------------

class TestHandleHrSummary:
    @pytest.mark.asyncio
    async def test_produces_summary(self, monkeypatch):
        from onemancompany.core.routine import _handle_hr_summary

        state = _mock_company_state()

        step = _make_step(title="HR Summary")
        ctx = StepContext(
            task_summary="Build feature",
            participants=["00005"],
            room_id="r1",
            workflow=_make_workflow(),
            meeting_doc={},
            project_record={"timeline": []},
        )
        ctx.self_evaluations = [{"name": "Alice", "level": 2, "evaluation": "ok"}]
        ctx.senior_reviews = [{"reviewer": "Boss", "reviewer_level": 3, "reviews": [{"name": "Alice", "review": "good"}]}]

        improvements = '[{"employee": "Alice", "improvements": ["Be faster"]}]'
        mock_ainvoke = AsyncMock(return_value=_make_llm_response(improvements))

        with patch("onemancompany.core.routine.company_state", state), \
             patch("onemancompany.core.routine._publish", new_callable=AsyncMock), \
             patch("onemancompany.core.routine._chat", new_callable=AsyncMock), \
             patch("onemancompany.core.routine.tracked_ainvoke", mock_ainvoke), \
             patch("onemancompany.core.routine.make_llm", return_value=MagicMock()):
            result = await _handle_hr_summary(step, ctx)

        assert len(ctx.hr_summary) == 1
        assert ctx.hr_summary[0]["employee"] == "Alice"
        assert "hr_summary" in result

    @pytest.mark.asyncio
    async def test_bad_json_summary(self, monkeypatch):
        from onemancompany.core.routine import _handle_hr_summary

        state = _mock_company_state()
        step = _make_step(title="HR Summary")
        ctx = StepContext(
            task_summary="task",
            participants=[],
            room_id="r1",
            workflow=_make_workflow(),
            meeting_doc={},
        )
        ctx.self_evaluations = []
        ctx.senior_reviews = []

        mock_ainvoke = AsyncMock(return_value=_make_llm_response("Free text summary"))

        with patch("onemancompany.core.routine.company_state", state), \
             patch("onemancompany.core.routine._publish", new_callable=AsyncMock), \
             patch("onemancompany.core.routine._chat", new_callable=AsyncMock), \
             patch("onemancompany.core.routine.tracked_ainvoke", mock_ainvoke), \
             patch("onemancompany.core.routine.make_llm", return_value=MagicMock()):
            result = await _handle_hr_summary(step, ctx)

        assert ctx.hr_summary[0]["employee"] == "all"


# ---------------------------------------------------------------------------
# _handle_coo_report
# ---------------------------------------------------------------------------

class TestHandleCooReport:
    @pytest.mark.asyncio
    async def test_produces_report(self, monkeypatch):
        from onemancompany.core.routine import _handle_coo_report

        state = _mock_company_state(employees={"00005": FakeEmployee(id="00005", name="A", role="E")})

        step = _make_step(title="COO Operations Report")
        ctx = StepContext(
            task_summary="Build feature",
            participants=["00005"],
            room_id="r1",
            workflow=_make_workflow(),
            meeting_doc={},
            project_record={
                "timeline": [{"employee_id": "00005", "action": "code", "detail": "coded"}],
                "cost": {"actual_cost_usd": 1.5, "budget_estimate_usd": 2.0,
                         "token_usage": {"input": 1000, "output": 500},
                         "breakdown": [{"employee_id": "00005", "model": "gpt-4", "total_tokens": 1500, "cost_usd": 1.5}]},
            },
        )

        mock_ainvoke = AsyncMock(return_value=_make_llm_response("Operations are smooth"))

        with patch("onemancompany.core.routine.company_state", state), \
             patch("onemancompany.core.routine._publish", new_callable=AsyncMock), \
             patch("onemancompany.core.routine._chat", new_callable=AsyncMock), \
             patch("onemancompany.core.routine.tracked_ainvoke", mock_ainvoke), \
             patch("onemancompany.core.routine.make_llm", return_value=MagicMock()):
            result = await _handle_coo_report(step, ctx)

        assert ctx.coo_report == "Operations are smooth"
        assert "coo_report" in result

    @pytest.mark.asyncio
    async def test_no_cost_data(self, monkeypatch):
        from onemancompany.core.routine import _handle_coo_report

        state = _mock_company_state()
        step = _make_step(title="COO Operations Report")
        ctx = StepContext(
            task_summary="task",
            participants=[],
            room_id="r1",
            workflow=_make_workflow(),
            meeting_doc={},
            project_record={},
        )

        mock_ainvoke = AsyncMock(return_value=_make_llm_response("Report"))

        with patch("onemancompany.core.routine.company_state", state), \
             patch("onemancompany.core.routine._publish", new_callable=AsyncMock), \
             patch("onemancompany.core.routine._chat", new_callable=AsyncMock), \
             patch("onemancompany.core.routine.tracked_ainvoke", mock_ainvoke), \
             patch("onemancompany.core.routine.make_llm", return_value=MagicMock()):
            result = await _handle_coo_report(step, ctx)

        assert ctx.coo_report == "Report"


# ---------------------------------------------------------------------------
# _handle_asset_consolidation
# ---------------------------------------------------------------------------

class TestHandleAssetConsolidation:
    @pytest.mark.asyncio
    async def test_no_project_id(self, monkeypatch):
        from onemancompany.core.routine import _handle_asset_consolidation

        step = _make_step(title="Asset Consolidation")
        ctx = StepContext(
            task_summary="task",
            participants=[],
            room_id="r1",
            workflow=_make_workflow(),
            meeting_doc={},
            project_record={},
        )

        with patch("onemancompany.core.routine._publish", new_callable=AsyncMock), \
             patch("onemancompany.core.routine._chat", new_callable=AsyncMock):
            result = await _handle_asset_consolidation(step, ctx)

        assert result == {"asset_suggestions": []}

    @pytest.mark.asyncio
    async def test_no_files(self, monkeypatch):
        from onemancompany.core.routine import _handle_asset_consolidation

        step = _make_step(title="Asset Consolidation")
        ctx = StepContext(
            task_summary="task",
            participants=[],
            room_id="r1",
            workflow=_make_workflow(),
            meeting_doc={},
            project_record={"id": "proj-1"},
        )

        with patch("onemancompany.core.routine._publish", new_callable=AsyncMock), \
             patch("onemancompany.core.routine._chat", new_callable=AsyncMock), \
             patch("onemancompany.core.project_archive.list_project_files", return_value=[]), \
             patch("onemancompany.core.project_archive.get_project_dir", return_value="/proj"):
            result = await _handle_asset_consolidation(step, ctx)

        assert result == {"asset_suggestions": []}

    @pytest.mark.asyncio
    async def test_with_suggestions(self, monkeypatch):
        from onemancompany.core.routine import _handle_asset_consolidation

        step = _make_step(title="Asset Consolidation")
        ctx = StepContext(
            task_summary="task",
            participants=[],
            room_id="r1",
            workflow=_make_workflow(),
            meeting_doc={},
            project_record={"id": "proj-1"},
        )

        suggestions_json = '[{"name": "Widget", "description": "A widget tool", "files": ["widget.py"]}]'
        mock_ainvoke = AsyncMock(return_value=_make_llm_response(suggestions_json))

        with patch("onemancompany.core.routine._publish", new_callable=AsyncMock), \
             patch("onemancompany.core.routine._chat", new_callable=AsyncMock), \
             patch("onemancompany.core.routine.tracked_ainvoke", mock_ainvoke), \
             patch("onemancompany.core.routine.make_llm", return_value=MagicMock()), \
             patch("onemancompany.core.project_archive.list_project_files", return_value=["widget.py", "readme.md"]), \
             patch("onemancompany.core.project_archive.get_project_dir", return_value="/proj"):
            result = await _handle_asset_consolidation(step, ctx)

        assert len(ctx.asset_suggestions) == 1
        assert ctx.asset_suggestions[0]["name"] == "Widget"

    @pytest.mark.asyncio
    async def test_bad_json_suggestions(self, monkeypatch):
        from onemancompany.core.routine import _handle_asset_consolidation

        step = _make_step(title="Asset Consolidation")
        ctx = StepContext(
            task_summary="task",
            participants=[],
            room_id="r1",
            workflow=_make_workflow(),
            meeting_doc={},
            project_record={"id": "proj-1"},
        )

        mock_ainvoke = AsyncMock(return_value=_make_llm_response("No assets to consolidate"))

        with patch("onemancompany.core.routine._publish", new_callable=AsyncMock), \
             patch("onemancompany.core.routine._chat", new_callable=AsyncMock), \
             patch("onemancompany.core.routine.tracked_ainvoke", mock_ainvoke), \
             patch("onemancompany.core.routine.make_llm", return_value=MagicMock()), \
             patch("onemancompany.core.project_archive.list_project_files", return_value=["f.py"]), \
             patch("onemancompany.core.project_archive.get_project_dir", return_value="/proj"):
            result = await _handle_asset_consolidation(step, ctx)

        assert ctx.asset_suggestions == []


# ---------------------------------------------------------------------------
# _handle_employee_open_floor
# ---------------------------------------------------------------------------

class TestHandleEmployeeOpenFloor:
    @pytest.mark.asyncio
    async def test_all_employees_speak(self, monkeypatch):
        from onemancompany.core.routine import _handle_employee_open_floor

        emp1 = FakeEmployee(id="00005", name="Alice", role="Engineer", level=2, nickname="小A",
                            work_principles="Be thorough")
        emp2 = FakeEmployee(id="00006", name="Bob", role="Designer", level=1, nickname="小B")
        state = _mock_company_state(employees={"00005": emp1, "00006": emp2})

        step = _make_step(title="Employee Open Floor")
        ctx = StepContext(
            task_summary="Build feature",
            participants=["00005", "00006"],
            room_id="r1",
            workflow=_make_workflow(),
            meeting_doc={},
            project_record={"timeline": [{"employee_id": "00005", "action": "code", "detail": "stuff"}]},
        )

        mock_ainvoke = AsyncMock(return_value=_make_llm_response("I need better tools"))

        with patch("onemancompany.core.routine.company_state", state), \
             patch("onemancompany.core.routine._publish", new_callable=AsyncMock), \
             patch("onemancompany.core.routine._chat", new_callable=AsyncMock), \
             patch("onemancompany.core.routine.tracked_ainvoke", mock_ainvoke), \
             patch("onemancompany.core.routine.make_llm", return_value=MagicMock()), \
             patch("onemancompany.core.routine.get_employee_skills_prompt", return_value=""), \
             patch("onemancompany.core.routine.get_employee_tools_prompt", return_value=""):
            result = await _handle_employee_open_floor(step, ctx)

        assert len(ctx.employee_feedback) == 2
        assert "employee_feedback" in result

    @pytest.mark.asyncio
    async def test_skips_unknown_employee(self, monkeypatch):
        from onemancompany.core.routine import _handle_employee_open_floor

        state = _mock_company_state()
        step = _make_step(title="Employee Open Floor")
        ctx = StepContext(
            task_summary="task",
            participants=["nonexistent"],
            room_id="r1",
            workflow=_make_workflow(),
            meeting_doc={},
        )

        with patch("onemancompany.core.routine.company_state", state), \
             patch("onemancompany.core.routine._publish", new_callable=AsyncMock), \
             patch("onemancompany.core.routine._chat", new_callable=AsyncMock):
            result = await _handle_employee_open_floor(step, ctx)

        assert ctx.employee_feedback == []


# ---------------------------------------------------------------------------
# _handle_action_plan
# ---------------------------------------------------------------------------

class TestHandleActionPlan:
    @pytest.mark.asyncio
    async def test_produces_action_plan(self, monkeypatch):
        from onemancompany.core.routine import _handle_action_plan

        state = _mock_company_state()
        step = _make_step(title="Action Plan")
        ctx = StepContext(
            task_summary="Build feature",
            participants=[],
            room_id="r1",
            workflow=_make_workflow(),
            meeting_doc={},
        )
        ctx.employee_feedback = [{"name": "Alice", "feedback": "Need more tools"}]
        ctx.hr_summary = [{"employee": "Alice", "improvements": ["Be faster"]}]
        ctx.coo_report = "Things are fine"

        actions_json = '[{"source": "HR", "description": "Buy tools", "priority": "high"}]'
        mock_ainvoke = AsyncMock(return_value=_make_llm_response(actions_json))

        with patch("onemancompany.core.routine.company_state", state), \
             patch("onemancompany.core.routine._publish", new_callable=AsyncMock), \
             patch("onemancompany.core.routine._chat", new_callable=AsyncMock), \
             patch("onemancompany.core.routine.tracked_ainvoke", mock_ainvoke), \
             patch("onemancompany.core.routine.make_llm", return_value=MagicMock()):
            result = await _handle_action_plan(step, ctx)

        assert len(ctx.action_items) == 1
        assert ctx.action_items[0]["source"] == "HR"

    @pytest.mark.asyncio
    async def test_bad_json_action_plan(self, monkeypatch):
        from onemancompany.core.routine import _handle_action_plan

        state = _mock_company_state()
        step = _make_step(title="Action Plan")
        ctx = StepContext(
            task_summary="task",
            participants=[],
            room_id="r1",
            workflow=_make_workflow(),
            meeting_doc={},
        )
        ctx.employee_feedback = []
        ctx.hr_summary = []
        ctx.coo_report = ""

        mock_ainvoke = AsyncMock(return_value=_make_llm_response("Just do it"))

        with patch("onemancompany.core.routine.company_state", state), \
             patch("onemancompany.core.routine._publish", new_callable=AsyncMock), \
             patch("onemancompany.core.routine._chat", new_callable=AsyncMock), \
             patch("onemancompany.core.routine.tracked_ainvoke", mock_ainvoke), \
             patch("onemancompany.core.routine.make_llm", return_value=MagicMock()):
            result = await _handle_action_plan(step, ctx)

        assert ctx.action_items[0]["source"] == "COO"
        assert ctx.action_items[0]["description"] == "Just do it"

    @pytest.mark.asyncio
    async def test_action_plan_with_asset_suggestions(self, monkeypatch):
        from onemancompany.core.routine import _handle_action_plan

        state = _mock_company_state()
        step = _make_step(title="Action Plan")
        ctx = StepContext(
            task_summary="task",
            participants=[],
            room_id="r1",
            workflow=_make_workflow(),
            meeting_doc={},
            project_record={"id": "proj-1"},
        )
        ctx.employee_feedback = []
        ctx.hr_summary = []
        ctx.coo_report = ""
        ctx.asset_suggestions = [{"name": "Widget", "description": "A tool", "files": ["w.py"]}]

        actions_json = '[{"source": "COO", "description": "Improve", "priority": "low"}]'
        mock_ainvoke = AsyncMock(return_value=_make_llm_response(actions_json))

        with patch("onemancompany.core.routine.company_state", state), \
             patch("onemancompany.core.routine._publish", new_callable=AsyncMock), \
             patch("onemancompany.core.routine._chat", new_callable=AsyncMock), \
             patch("onemancompany.core.routine.tracked_ainvoke", mock_ainvoke), \
             patch("onemancompany.core.routine.make_llm", return_value=MagicMock()), \
             patch("onemancompany.core.project_archive.get_project_dir", return_value="/proj"):
            result = await _handle_action_plan(step, ctx)

        # Should have the regular action + 1 asset consolidation action
        assert len(ctx.action_items) == 2
        asset_action = [a for a in ctx.action_items if a.get("type") == "asset_consolidation"]
        assert len(asset_action) == 1
        assert asset_action[0]["name"] == "Widget"


# ---------------------------------------------------------------------------
# _handle_generic_step
# ---------------------------------------------------------------------------

class TestHandleGenericStep:
    @pytest.mark.asyncio
    async def test_generic_step(self, monkeypatch):
        from onemancompany.core.routine import _handle_generic_step

        step = _make_step(title="Custom Step", owner="SomeRole")
        ctx = StepContext(
            task_summary="Build feature",
            participants=[],
            room_id="r1",
            workflow=_make_workflow(),
            meeting_doc={},
        )

        mock_ainvoke = AsyncMock(return_value=_make_llm_response("Generic step output"))

        with patch("onemancompany.core.routine._publish", new_callable=AsyncMock), \
             patch("onemancompany.core.routine._chat", new_callable=AsyncMock), \
             patch("onemancompany.core.routine.tracked_ainvoke", mock_ainvoke), \
             patch("onemancompany.core.routine.make_llm", return_value=MagicMock()):
            result = await _handle_generic_step(step, ctx)

        assert "generic_output" in result
        assert result["generic_output"] == "Generic step output"


# ---------------------------------------------------------------------------
# _run_workflow
# ---------------------------------------------------------------------------

class TestRunWorkflow:
    @pytest.mark.asyncio
    async def test_executes_all_steps(self, monkeypatch):
        from onemancompany.core.routine import _run_workflow

        steps = [
            _make_step(index=0, title="CEO Approval", owner="CEO"),
            _make_step(index=1, title="Custom Unknown", owner="Random"),
        ]
        workflow = _make_workflow(steps=steps)
        ctx = StepContext(
            task_summary="task",
            participants=[],
            room_id="r1",
            workflow=workflow,
            meeting_doc={},
        )

        mock_ainvoke = AsyncMock(return_value=_make_llm_response("Output"))

        with patch("onemancompany.core.routine._publish", new_callable=AsyncMock), \
             patch("onemancompany.core.routine._chat", new_callable=AsyncMock), \
             patch("onemancompany.core.routine.tracked_ainvoke", mock_ainvoke), \
             patch("onemancompany.core.routine.make_llm", return_value=MagicMock()):
            result = await _run_workflow(workflow, ctx)

        assert "CEO Approval" in result
        assert "Custom Unknown" in result
        assert "CEO Approval" in ctx.results


# ---------------------------------------------------------------------------
# run_post_task_routine — with project_id
# ---------------------------------------------------------------------------

class TestRunPostTaskRoutineWithProject:
    @pytest.mark.asyncio
    async def test_with_project_filters_participants(self, tmp_path, monkeypatch):
        """Participants filtered to actual contributors when project_id is given."""
        emp1 = FakeEmployee(id="00004", name="EA", role="EA", level=4, nickname="EA")
        emp2 = FakeEmployee(id="00005", name="Alice", role="Engineer", level=2, nickname="小A")
        emp3 = FakeEmployee(id="00006", name="Bob", role="Designer", level=1, nickname="小B")
        room = FakeMeetingRoom(id="room-1", name="Room A")
        state = _mock_company_state(
            employees={"00004": emp1, "00005": emp2, "00006": emp3},
            rooms={"room-1": room},
        )

        monkeypatch.setattr("onemancompany.core.routine.REPORTS_DIR", tmp_path)
        monkeypatch.setattr("onemancompany.core.routine.company_state", state)
        monkeypatch.setattr("onemancompany.core.routine.FOUNDING_LEVEL", 4)
        monkeypatch.setattr("onemancompany.core.routine.EA_ID", "00004")
        monkeypatch.setattr("onemancompany.core.routine.update_employee_performance", lambda *a: None)

        # Only emp2 contributed
        project_record = {
            "id": "proj-1",
            "timeline": [{"employee_id": "00005", "action": "code", "detail": "Did work"}],
        }
        monkeypatch.setattr("onemancompany.core.project_archive.load_project", lambda pid: project_record)

        workflow_md = (
            "# Test\n\n"
            "## Phase 1: CEO Approval\n\n"
            "- **Responsible**: CEO\n"
            "- **Output**: Approved\n"
        )
        monkeypatch.setattr("onemancompany.core.routine.load_workflows",
                            lambda: {"project_retrospective_workflow": workflow_md})

        mock_ainvoke = AsyncMock(return_value=_make_llm_response("ok"))
        mock_publish = AsyncMock()

        with patch("onemancompany.core.routine._publish", mock_publish), \
             patch("onemancompany.core.routine._chat", new_callable=AsyncMock), \
             patch("onemancompany.core.routine.tracked_ainvoke", mock_ainvoke), \
             patch("onemancompany.core.routine.make_llm", return_value=MagicMock()), \
             patch("onemancompany.core.routine.get_employee_skills_prompt", return_value=""), \
             patch("onemancompany.core.routine.get_employee_tools_prompt", return_value=""), \
             patch("onemancompany.core.project_archive.append_action", MagicMock()):
            await run_post_task_routine("Build X", project_id="proj-1")

        # Room should be released
        assert room.is_booked is False

    @pytest.mark.asyncio
    async def test_fallback_no_room(self, tmp_path, monkeypatch):
        """Fallback routine also handles no-room scenario."""
        emp1 = FakeEmployee(id="00005", name="Alice", role="Eng", level=1, nickname="小A")
        emp2 = FakeEmployee(id="00006", name="Bob", role="Eng", level=1, nickname="小B")
        room = FakeMeetingRoom(id="r1", name="Room", is_booked=True)
        state = _mock_company_state(
            employees={"00005": emp1, "00006": emp2},
            rooms={"r1": room},
        )

        monkeypatch.setattr("onemancompany.core.routine.REPORTS_DIR", tmp_path)
        monkeypatch.setattr("onemancompany.core.routine.company_state", state)
        monkeypatch.setattr("onemancompany.core.routine.FOUNDING_LEVEL", 4)
        monkeypatch.setattr("onemancompany.core.routine.EA_ID", "00004")
        monkeypatch.setattr("onemancompany.core.routine.update_employee_performance", lambda *a: None)
        monkeypatch.setattr("onemancompany.core.routine.load_workflows", lambda: {})

        mock_publish = AsyncMock()
        with patch("onemancompany.core.routine._publish", mock_publish):
            await run_post_task_routine("task", participants=["00005", "00006"])

        messages = [
            call.args[1].get("message", "")
            for call in mock_publish.call_args_list
            if len(call.args) >= 2 and isinstance(call.args[1], dict) and "message" in call.args[1]
        ]
        assert any("延期" in m for m in messages)

    @pytest.mark.asyncio
    async def test_malformed_workflow_falls_back(self, tmp_path, monkeypatch):
        """Malformed workflow (no steps) falls back to legacy."""
        emp1 = FakeEmployee(id="00005", name="Alice", role="Eng", level=2, nickname="小A")
        emp2 = FakeEmployee(id="00006", name="Bob", role="Eng", level=1, nickname="小B")
        room = FakeMeetingRoom(id="r1", name="Room A")
        state = _mock_company_state(
            employees={"00005": emp1, "00006": emp2},
            rooms={"r1": room},
        )

        monkeypatch.setattr("onemancompany.core.routine.REPORTS_DIR", tmp_path)
        monkeypatch.setattr("onemancompany.core.routine.company_state", state)
        monkeypatch.setattr("onemancompany.core.routine.FOUNDING_LEVEL", 4)
        monkeypatch.setattr("onemancompany.core.routine.EA_ID", "00004")
        monkeypatch.setattr("onemancompany.core.routine.update_employee_performance", lambda *a: None)

        # Workflow that parses to zero steps
        monkeypatch.setattr("onemancompany.core.routine.load_workflows",
                            lambda: {"project_retrospective_workflow": "Just some text, no ## headers"})

        mock_ainvoke = AsyncMock(return_value=_make_llm_response('[{"name": "all", "review": "ok"}]'))

        with patch("onemancompany.core.routine._publish", new_callable=AsyncMock), \
             patch("onemancompany.core.routine._chat", new_callable=AsyncMock), \
             patch("onemancompany.core.routine.tracked_ainvoke", mock_ainvoke), \
             patch("onemancompany.core.routine.make_llm", return_value=MagicMock()), \
             patch("onemancompany.core.routine.get_employee_skills_prompt", return_value=""), \
             patch("onemancompany.core.routine.get_employee_tools_prompt", return_value=""):
            await run_post_task_routine("task", participants=["00005", "00006"])

        # Should still complete (used fallback)
        assert room.is_booked is False


# ---------------------------------------------------------------------------
# run_onboarding_routine
# ---------------------------------------------------------------------------

class TestRunOnboardingRoutine:
    @pytest.mark.asyncio
    async def test_onboarding_new_employee(self, monkeypatch):
        from onemancompany.core.routine import run_onboarding_routine

        emp = FakeEmployee(id="00010", name="Newbie", role="Engineer", level=1, nickname="新人")
        emp.work_principles = ""
        emp.onboarding_completed = False
        state = _mock_company_state(employees={"00010": emp})

        monkeypatch.setattr("onemancompany.core.routine.company_state", state)
        monkeypatch.setattr("onemancompany.core.routine.PROBATION_TASKS", 2)

        mock_save_principles = MagicMock()
        mock_update_field = MagicMock()

        with patch("onemancompany.core.routine._publish", new_callable=AsyncMock), \
             patch("onemancompany.core.config.save_work_principles", mock_save_principles), \
             patch("onemancompany.core.config.update_employee_field", mock_update_field):
            await run_onboarding_routine("00010")

        assert emp.onboarding_completed is True
        assert emp.work_principles != ""
        mock_save_principles.assert_called_once()

    @pytest.mark.asyncio
    async def test_onboarding_existing_principles(self, monkeypatch):
        from onemancompany.core.routine import run_onboarding_routine

        emp = FakeEmployee(id="00010", name="Newbie", role="Engineer", level=1, nickname="新人")
        emp.work_principles = "Existing principles"
        emp.onboarding_completed = False
        state = _mock_company_state(employees={"00010": emp})

        monkeypatch.setattr("onemancompany.core.routine.company_state", state)

        mock_update_field = MagicMock()

        with patch("onemancompany.core.routine._publish", new_callable=AsyncMock), \
             patch("onemancompany.core.config.update_employee_field", mock_update_field):
            await run_onboarding_routine("00010")

        # Principles should not be overwritten
        assert emp.work_principles == "Existing principles"
        assert emp.onboarding_completed is True

    @pytest.mark.asyncio
    async def test_onboarding_nonexistent_employee(self, monkeypatch):
        from onemancompany.core.routine import run_onboarding_routine

        state = _mock_company_state()
        monkeypatch.setattr("onemancompany.core.routine.company_state", state)

        # Should not raise
        with patch("onemancompany.core.routine._publish", new_callable=AsyncMock):
            await run_onboarding_routine("99999")


# ---------------------------------------------------------------------------
# run_offboarding_routine
# ---------------------------------------------------------------------------

class TestRunOffboardingRoutine:
    @pytest.mark.asyncio
    async def test_offboarding(self, tmp_path, monkeypatch):
        from onemancompany.core.routine import run_offboarding_routine

        emp = FakeEmployee(id="00010", name="Leaving", role="Engineer", level=1, nickname="走人")
        state = _mock_company_state(employees={"00010": emp})

        monkeypatch.setattr("onemancompany.core.routine.company_state", state)
        monkeypatch.setattr("onemancompany.core.routine.REPORTS_DIR", tmp_path)

        mock_publish = AsyncMock()
        with patch("onemancompany.core.routine._publish", mock_publish):
            await run_offboarding_routine("00010", "performance")

        event_types = [call.args[0] for call in mock_publish.call_args_list]
        assert "exit_interview_started" in event_types
        assert "exit_interview_completed" in event_types

    @pytest.mark.asyncio
    async def test_offboarding_nonexistent(self, monkeypatch):
        from onemancompany.core.routine import run_offboarding_routine

        state = _mock_company_state()
        monkeypatch.setattr("onemancompany.core.routine.company_state", state)

        with patch("onemancompany.core.routine._publish", new_callable=AsyncMock):
            await run_offboarding_routine("99999", "voluntary")


# ---------------------------------------------------------------------------
# run_performance_meeting
# ---------------------------------------------------------------------------

class TestRunPerformanceMeeting:
    @pytest.mark.asyncio
    async def test_performance_meeting(self, monkeypatch):
        from onemancompany.core.routine import run_performance_meeting

        emp = FakeEmployee(id="00010", name="Worker", role="Engineer", level=1, nickname="打工人")
        state = _mock_company_state(employees={"00010": emp})

        monkeypatch.setattr("onemancompany.core.routine.company_state", state)

        mock_publish = AsyncMock()
        with patch("onemancompany.core.routine._publish", mock_publish):
            await run_performance_meeting("00010", 4.5, "Good job")

        assert mock_publish.await_count == 2

    @pytest.mark.asyncio
    async def test_performance_meeting_nonexistent(self, monkeypatch):
        from onemancompany.core.routine import run_performance_meeting

        state = _mock_company_state()
        monkeypatch.setattr("onemancompany.core.routine.company_state", state)

        with patch("onemancompany.core.routine._publish", new_callable=AsyncMock):
            await run_performance_meeting("99999", 3.0, "N/A")


# ---------------------------------------------------------------------------
# _handle_senior_review — JSON decode error branch
# ---------------------------------------------------------------------------

class TestHandleSeniorReviewJsonDecodeError:
    @pytest.mark.asyncio
    async def test_json_decode_error_fallback(self, monkeypatch):
        """JSONDecodeError in senior review falls back to raw text."""
        from onemancompany.core.routine import _handle_senior_review

        senior = FakeEmployee(id="00005", name="Alice", role="Lead", level=3, nickname="小A")
        junior = FakeEmployee(id="00006", name="Bob", role="Engineer", level=1, nickname="小B")
        state = _mock_company_state(employees={"00005": senior, "00006": junior})

        step = _make_step(title="Senior Peer Review")
        ctx = StepContext(
            task_summary="task",
            participants=["00005", "00006"],
            room_id="r1",
            workflow=_make_workflow(),
            meeting_doc={},
            project_record={"timeline": [{"employee_id": "00005", "action": "code", "detail": "stuff"}]},
        )
        ctx.self_evaluations = [
            {"employee_id": "00006", "name": "Bob", "nickname": "小B", "level": 1, "evaluation": "ok"},
        ]

        # Closed brackets but invalid JSON inside — triggers JSONDecodeError
        mock_ainvoke = AsyncMock(return_value=_make_llm_response('[invalid json content]'))

        with patch("onemancompany.core.routine.company_state", state), \
             patch("onemancompany.core.routine._publish", new_callable=AsyncMock), \
             patch("onemancompany.core.routine._chat", new_callable=AsyncMock), \
             patch("onemancompany.core.routine.tracked_ainvoke", mock_ainvoke), \
             patch("onemancompany.core.routine.make_llm", return_value=MagicMock()), \
             patch("onemancompany.core.routine.get_employee_skills_prompt", return_value=""), \
             patch("onemancompany.core.routine.get_employee_tools_prompt", return_value=""):
            result = await _handle_senior_review(step, ctx)

        assert ctx.senior_reviews[0]["reviews"][0]["name"] == "all"


# ---------------------------------------------------------------------------
# _handle_hr_summary — JSONDecodeError path
# ---------------------------------------------------------------------------

class TestHandleHrSummaryJsonDecodeError:
    @pytest.mark.asyncio
    async def test_json_decode_error_fallback(self, monkeypatch):
        from onemancompany.core.routine import _handle_hr_summary

        state = _mock_company_state()
        step = _make_step(title="HR Summary")
        ctx = StepContext(
            task_summary="task",
            participants=[],
            room_id="r1",
            workflow=_make_workflow(),
            meeting_doc={},
            project_record={"timeline": [{"employee_id": "00005", "action": "code", "detail": "stuff"}]},
        )
        ctx.self_evaluations = [{"name": "A", "level": 1, "evaluation": "ok"}]
        ctx.senior_reviews = [{"reviewer": "B", "reviewer_level": 2, "reviews": [{"name": "A", "review": "fine"}]}]

        # Closed brackets but invalid JSON inside — triggers JSONDecodeError
        mock_ainvoke = AsyncMock(return_value=_make_llm_response('[invalid json here]'))

        with patch("onemancompany.core.routine.company_state", state), \
             patch("onemancompany.core.routine._publish", new_callable=AsyncMock), \
             patch("onemancompany.core.routine._chat", new_callable=AsyncMock), \
             patch("onemancompany.core.routine.tracked_ainvoke", mock_ainvoke), \
             patch("onemancompany.core.routine.make_llm", return_value=MagicMock()):
            result = await _handle_hr_summary(step, ctx)

        assert ctx.hr_summary[0]["employee"] == "all"


# ---------------------------------------------------------------------------
# _handle_action_plan — JSONDecodeError path
# ---------------------------------------------------------------------------

class TestHandleActionPlanJsonDecodeError:
    @pytest.mark.asyncio
    async def test_json_decode_error_fallback(self, monkeypatch):
        from onemancompany.core.routine import _handle_action_plan

        state = _mock_company_state()
        step = _make_step(title="Action Plan")
        ctx = StepContext(
            task_summary="task",
            participants=[],
            room_id="r1",
            workflow=_make_workflow(),
            meeting_doc={},
        )
        ctx.employee_feedback = []
        ctx.hr_summary = []
        ctx.coo_report = ""

        # Closed brackets but invalid JSON inside — triggers JSONDecodeError
        mock_ainvoke = AsyncMock(return_value=_make_llm_response('[invalid json action]'))

        with patch("onemancompany.core.routine.company_state", state), \
             patch("onemancompany.core.routine._publish", new_callable=AsyncMock), \
             patch("onemancompany.core.routine._chat", new_callable=AsyncMock), \
             patch("onemancompany.core.routine.tracked_ainvoke", mock_ainvoke), \
             patch("onemancompany.core.routine.make_llm", return_value=MagicMock()):
            result = await _handle_action_plan(step, ctx)

        assert ctx.action_items[0]["source"] == "COO"


# ---------------------------------------------------------------------------
# _handle_asset_consolidation — JSONDecodeError path
# ---------------------------------------------------------------------------

class TestHandleAssetConsolidationJsonDecodeError:
    @pytest.mark.asyncio
    async def test_json_decode_error(self, monkeypatch):
        from onemancompany.core.routine import _handle_asset_consolidation

        step = _make_step(title="Asset Consolidation")
        ctx = StepContext(
            task_summary="task",
            participants=[],
            room_id="r1",
            workflow=_make_workflow(),
            meeting_doc={},
            project_record={"id": "proj-1"},
        )

        # Closed brackets but invalid JSON — triggers JSONDecodeError
        mock_ainvoke = AsyncMock(return_value=_make_llm_response('[invalid json asset]'))

        with patch("onemancompany.core.routine._publish", new_callable=AsyncMock), \
             patch("onemancompany.core.routine._chat", new_callable=AsyncMock), \
             patch("onemancompany.core.routine.tracked_ainvoke", mock_ainvoke), \
             patch("onemancompany.core.routine.make_llm", return_value=MagicMock()), \
             patch("onemancompany.core.project_archive.list_project_files", return_value=["file.py"]), \
             patch("onemancompany.core.project_archive.get_project_dir", return_value="/proj"):
            result = await _handle_asset_consolidation(step, ctx)

        assert ctx.asset_suggestions == []


# ---------------------------------------------------------------------------
# run_post_task_routine — skipping Preparation step + project archive recording
# ---------------------------------------------------------------------------

class TestRunPostTaskRoutinePreparationSkip:
    @pytest.mark.asyncio
    async def test_skips_preparation_step(self, tmp_path, monkeypatch):
        """First step with 'Preparation' in title is skipped."""
        emp1 = FakeEmployee(id="00005", name="Alice", role="Eng", level=2, nickname="小A")
        emp2 = FakeEmployee(id="00006", name="Bob", role="Eng", level=1, nickname="小B")
        room = FakeMeetingRoom(id="r1", name="Room A")
        state = _mock_company_state(
            employees={"00005": emp1, "00006": emp2},
            rooms={"r1": room},
        )

        monkeypatch.setattr("onemancompany.core.routine.REPORTS_DIR", tmp_path)
        monkeypatch.setattr("onemancompany.core.routine.company_state", state)
        monkeypatch.setattr("onemancompany.core.routine.FOUNDING_LEVEL", 4)
        monkeypatch.setattr("onemancompany.core.routine.EA_ID", "00004")
        monkeypatch.setattr("onemancompany.core.routine.update_employee_performance", lambda *a: None)

        # Workflow with Preparation as first step + CEO Approval
        workflow_md = (
            "# Test\n\n"
            "- **Flow ID**: test\n"
            "- **Owner**: HR\n"
            "- **Collaborators**: COO\n"
            "- **Trigger**: manual\n\n"
            "---\n\n"
            "## Phase 1: Review Preparation\n\n"
            "- **Responsible**: HR\n"
            "- **Output**: Room booked\n\n"
            "## Phase 2: CEO Approval\n\n"
            "- **Responsible**: CEO\n"
            "- **Output**: Approved\n"
        )
        monkeypatch.setattr("onemancompany.core.routine.load_workflows",
                            lambda: {"project_retrospective_workflow": workflow_md})

        mock_ainvoke = AsyncMock(return_value=_make_llm_response("ok"))
        mock_publish = AsyncMock()

        with patch("onemancompany.core.routine._publish", mock_publish), \
             patch("onemancompany.core.routine._chat", new_callable=AsyncMock), \
             patch("onemancompany.core.routine.tracked_ainvoke", mock_ainvoke), \
             patch("onemancompany.core.routine.make_llm", return_value=MagicMock()), \
             patch("onemancompany.core.routine.get_employee_skills_prompt", return_value=""), \
             patch("onemancompany.core.routine.get_employee_tools_prompt", return_value=""):
            await run_post_task_routine("Build X", participants=["00005", "00006"])

        assert room.is_booked is False

    @pytest.mark.asyncio
    async def test_records_in_project_archive(self, tmp_path, monkeypatch):
        """With project_id, routine results are recorded in project archive."""
        emp1 = FakeEmployee(id="00004", name="EA", role="EA", level=4, nickname="EA")
        emp2 = FakeEmployee(id="00005", name="Alice", role="Eng", level=2, nickname="小A")
        emp3 = FakeEmployee(id="00006", name="Bob", role="Eng", level=1, nickname="小B")
        room = FakeMeetingRoom(id="r1", name="Room A")
        state = _mock_company_state(
            employees={"00004": emp1, "00005": emp2, "00006": emp3},
            rooms={"r1": room},
        )

        monkeypatch.setattr("onemancompany.core.routine.REPORTS_DIR", tmp_path)
        monkeypatch.setattr("onemancompany.core.routine.company_state", state)
        monkeypatch.setattr("onemancompany.core.routine.FOUNDING_LEVEL", 4)
        monkeypatch.setattr("onemancompany.core.routine.EA_ID", "00004")
        monkeypatch.setattr("onemancompany.core.routine.update_employee_performance", lambda *a: None)

        project_record = {
            "id": "proj-1",
            "timeline": [
                {"employee_id": "00005", "action": "code", "detail": "Wrote code"},
                {"employee_id": "00006", "action": "review", "detail": "Reviewed"},
            ],
        }
        monkeypatch.setattr("onemancompany.core.project_archive.load_project", lambda pid: project_record)

        # Workflow: Self-Evaluation + COO Report + Action Plan + CEO Approval
        workflow_md = (
            "# Test\n\n"
            "- **Flow ID**: test\n"
            "- **Owner**: HR\n"
            "- **Collaborators**: COO\n"
            "- **Trigger**: manual\n\n"
            "---\n\n"
            "## Phase 1: Self-Evaluation\n\n"
            "- **Responsible**: Each participating employee\n"
            "- **Steps**:\n  1. Evaluate\n"
            "- **Output**: Self eval\n\n"
            "## Phase 2: COO Operations Report\n\n"
            "- **Responsible**: COO\n"
            "- **Output**: Report\n\n"
            "## Phase 3: Action Plan\n\n"
            "- **Responsible**: COO + HR\n"
            "- **Output**: Action items\n\n"
            "## Phase 4: CEO Approval\n\n"
            "- **Responsible**: CEO\n"
            "- **Output**: Approved\n"
        )
        monkeypatch.setattr("onemancompany.core.routine.load_workflows",
                            lambda: {"project_retrospective_workflow": workflow_md})

        call_count = 0

        async def mock_ainvoke(llm, prompt, category="", employee_id=""):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.content = "Test response"
            # For action plan, return valid JSON
            if "行动计划" in prompt or "action items" in prompt.lower():
                resp.content = '[{"source": "COO", "description": "Improve", "priority": "high"}]'
            return resp

        mock_append = MagicMock()

        with patch("onemancompany.core.routine._publish", new_callable=AsyncMock), \
             patch("onemancompany.core.routine._chat", new_callable=AsyncMock), \
             patch("onemancompany.core.routine.tracked_ainvoke", mock_ainvoke), \
             patch("onemancompany.core.routine.make_llm", return_value=MagicMock()), \
             patch("onemancompany.core.routine.get_employee_skills_prompt", return_value=""), \
             patch("onemancompany.core.routine.get_employee_tools_prompt", return_value=""), \
             patch("onemancompany.core.project_archive.append_action", mock_append):
            await run_post_task_routine("Build X", project_id="proj-1")

        # append_action should have been called for self-evals, COO report, and action items
        assert mock_append.call_count > 0
        assert room.is_booked is False


# ---------------------------------------------------------------------------
# Legacy phase functions — additional coverage
# ---------------------------------------------------------------------------

class TestLegacyPhaseAdditional:
    @pytest.mark.asyncio
    async def test_legacy_phase1_with_workflow_doc(self, tmp_path, monkeypatch):
        """Legacy phase1 with workflow doc context and JSON decode error."""
        from onemancompany.core.routine import _run_phase1_legacy

        emp1 = FakeEmployee(id="00005", name="Alice", role="Lead", level=3, nickname="小A",
                            work_principles="Be thorough")
        emp2 = FakeEmployee(id="00006", name="Bob", role="Engineer", level=1, nickname="小B")
        state = _mock_company_state(employees={"00005": emp1, "00006": emp2})

        call_idx = 0

        async def mock_ainvoke(llm, prompt, category="", employee_id=""):
            nonlocal call_idx
            call_idx += 1
            resp = MagicMock()
            if call_idx <= 2:
                # Self-evaluations
                resp.content = "I did my work"
            elif call_idx == 3:
                # Senior review — return invalid JSON with brackets
                resp.content = '[invalid json review data]'
            else:
                # HR summary — return no JSON brackets
                resp.content = "Everyone needs improvement"
            return resp

        with patch("onemancompany.core.routine.company_state", state), \
             patch("onemancompany.core.routine._publish", new_callable=AsyncMock), \
             patch("onemancompany.core.routine._chat", new_callable=AsyncMock), \
             patch("onemancompany.core.routine.tracked_ainvoke", mock_ainvoke), \
             patch("onemancompany.core.routine.make_llm", return_value=MagicMock()), \
             patch("onemancompany.core.routine.get_employee_skills_prompt", return_value=""), \
             patch("onemancompany.core.routine.get_employee_tools_prompt", return_value=""):
            result = await _run_phase1_legacy("Build feature", ["00005", "00006"], "workflow text", "room-1")

        assert len(result["self_evaluations"]) == 2
        # Senior review should have JSON decode error fallback
        assert result["senior_reviews"][0]["reviews"][0]["name"] == "all"
        # HR summary should use the no-bracket fallback
        assert result["hr_summary"][0]["employee"] == "all"

    @pytest.mark.asyncio
    async def test_legacy_phase2_with_workflow_doc(self, monkeypatch):
        """Legacy phase2 with employee feedback, principles, and JSON decode error."""
        from onemancompany.core.routine import _run_phase2_legacy

        emp1 = FakeEmployee(id="00005", name="Alice", role="Engineer", level=2, nickname="小A",
                            work_principles="Work hard")
        state = _mock_company_state(employees={"00005": emp1})

        call_idx = 0

        async def mock_ainvoke(llm, prompt, category="", employee_id=""):
            nonlocal call_idx
            call_idx += 1
            resp = MagicMock()
            if call_idx == 1:
                # COO report
                resp.content = "Company running well"
            elif call_idx == 2:
                # Employee feedback
                resp.content = "Need better tools"
            else:
                # Action items — return invalid JSON with brackets
                resp.content = '[invalid json action data]'
            return resp

        phase1 = {"hr_summary": [{"employee": "Alice", "improvements": ["Faster"]}]}

        with patch("onemancompany.core.routine.company_state", state), \
             patch("onemancompany.core.routine._publish", new_callable=AsyncMock), \
             patch("onemancompany.core.routine._chat", new_callable=AsyncMock), \
             patch("onemancompany.core.routine.tracked_ainvoke", mock_ainvoke), \
             patch("onemancompany.core.routine.make_llm", return_value=MagicMock()), \
             patch("onemancompany.core.routine.get_employee_skills_prompt", return_value=""), \
             patch("onemancompany.core.routine.get_employee_tools_prompt", return_value=""):
            result = await _run_phase2_legacy("Build feature", ["00005"], phase1, "workflow text", "room-1")

        assert result["coo_report"] == "Company running well"
        assert len(result["employee_feedback"]) == 1
        # Action items should have JSON decode error fallback
        assert result["action_items"][0]["source"] == "COO"

    @pytest.mark.asyncio
    async def test_legacy_phase2_skips_unknown_employee(self, monkeypatch):
        """Legacy phase2 skips unknown employee IDs."""
        from onemancompany.core.routine import _run_phase2_legacy

        state = _mock_company_state()

        call_idx = 0

        async def mock_ainvoke(llm, prompt, category="", employee_id=""):
            nonlocal call_idx
            call_idx += 1
            resp = MagicMock()
            if call_idx == 1:
                resp.content = "Report"
            else:
                resp.content = "No valid JSON []"
            return resp

        with patch("onemancompany.core.routine.company_state", state), \
             patch("onemancompany.core.routine._publish", new_callable=AsyncMock), \
             patch("onemancompany.core.routine._chat", new_callable=AsyncMock), \
             patch("onemancompany.core.routine.tracked_ainvoke", mock_ainvoke), \
             patch("onemancompany.core.routine.make_llm", return_value=MagicMock()), \
             patch("onemancompany.core.routine.get_employee_skills_prompt", return_value=""), \
             patch("onemancompany.core.routine.get_employee_tools_prompt", return_value=""):
            result = await _run_phase2_legacy("task", ["nonexistent"], {}, "", "r1")

        assert result["employee_feedback"] == []

    @pytest.mark.asyncio
    async def test_legacy_phase1_skips_unknown_and_no_junior(self, monkeypatch):
        """Legacy phase1 skips unknown employees and handles no juniors."""
        from onemancompany.core.routine import _run_phase1_legacy

        # Both same level — no senior review happens
        emp1 = FakeEmployee(id="00005", name="Alice", role="Eng", level=2, nickname="小A")
        state = _mock_company_state(employees={"00005": emp1})

        call_idx = 0

        async def mock_ainvoke(llm, prompt, category="", employee_id=""):
            nonlocal call_idx
            call_idx += 1
            resp = MagicMock()
            if call_idx == 1:
                resp.content = "Self eval"
            else:
                resp.content = "HR: []"
            return resp

        with patch("onemancompany.core.routine.company_state", state), \
             patch("onemancompany.core.routine._publish", new_callable=AsyncMock), \
             patch("onemancompany.core.routine._chat", new_callable=AsyncMock), \
             patch("onemancompany.core.routine.tracked_ainvoke", mock_ainvoke), \
             patch("onemancompany.core.routine.make_llm", return_value=MagicMock()), \
             patch("onemancompany.core.routine.get_employee_skills_prompt", return_value=""), \
             patch("onemancompany.core.routine.get_employee_tools_prompt", return_value=""):
            result = await _run_phase1_legacy("task", ["nonexistent", "00005"], "", "r1")

        # Only 1 self-evaluation (nonexistent skipped)
        assert len(result["self_evaluations"]) == 1
        # No senior reviews (single person can't review anyone)
        assert result["senior_reviews"] == []

    @pytest.mark.asyncio
    async def test_legacy_phase2_no_bracket_action_items(self, monkeypatch):
        """Action items with no JSON brackets fall back."""
        from onemancompany.core.routine import _run_phase2_legacy

        state = _mock_company_state()

        call_idx = 0

        async def mock_ainvoke(llm, prompt, category="", employee_id=""):
            nonlocal call_idx
            call_idx += 1
            resp = MagicMock()
            if call_idx == 1:
                resp.content = "Report"
            else:
                resp.content = "Just improve everything"
            return resp

        with patch("onemancompany.core.routine.company_state", state), \
             patch("onemancompany.core.routine._publish", new_callable=AsyncMock), \
             patch("onemancompany.core.routine._chat", new_callable=AsyncMock), \
             patch("onemancompany.core.routine.tracked_ainvoke", mock_ainvoke), \
             patch("onemancompany.core.routine.make_llm", return_value=MagicMock()), \
             patch("onemancompany.core.routine.get_employee_skills_prompt", return_value=""), \
             patch("onemancompany.core.routine.get_employee_tools_prompt", return_value=""):
            result = await _run_phase2_legacy("task", [], {}, "", "r1")

        assert result["action_items"][0]["source"] == "COO"
        assert result["action_items"][0]["description"] == "Just improve everything"


# ---------------------------------------------------------------------------
# run_all_hands_meeting — with principles
# ---------------------------------------------------------------------------

class TestLegacyPhaseJsonEdgeCases:
    @pytest.mark.asyncio
    async def test_legacy_phase1_no_bracket_senior_review(self, monkeypatch):
        """Legacy phase1: senior review text with no brackets hits else branch."""
        from onemancompany.core.routine import _run_phase1_legacy

        senior = FakeEmployee(id="00005", name="Alice", role="Lead", level=3, nickname="小A")
        junior = FakeEmployee(id="00006", name="Bob", role="Engineer", level=1, nickname="小B")
        state = _mock_company_state(employees={"00005": senior, "00006": junior})

        call_idx = 0

        async def mock_ainvoke(llm, prompt, category="", employee_id=""):
            nonlocal call_idx
            call_idx += 1
            resp = MagicMock()
            if call_idx <= 2:
                resp.content = "Self eval"
            elif call_idx == 3:
                # Senior review — no brackets at all (hits else branch at line 1151)
                resp.content = "Bob did fine overall"
            else:
                # HR summary — valid JSON
                resp.content = '[{"employee": "Bob", "improvements": ["improve"]}]'
            return resp

        with patch("onemancompany.core.routine.company_state", state), \
             patch("onemancompany.core.routine._publish", new_callable=AsyncMock), \
             patch("onemancompany.core.routine._chat", new_callable=AsyncMock), \
             patch("onemancompany.core.routine.tracked_ainvoke", mock_ainvoke), \
             patch("onemancompany.core.routine.make_llm", return_value=MagicMock()), \
             patch("onemancompany.core.routine.get_employee_skills_prompt", return_value=""), \
             patch("onemancompany.core.routine.get_employee_tools_prompt", return_value=""):
            result = await _run_phase1_legacy("task", ["00005", "00006"], "", "r1")

        assert result["senior_reviews"][0]["reviews"][0]["name"] == "all"

    @pytest.mark.asyncio
    async def test_legacy_phase1_json_decode_error_senior_review(self, monkeypatch):
        """Legacy phase1: senior review with bad JSON triggers JSONDecodeError."""
        from onemancompany.core.routine import _run_phase1_legacy

        senior = FakeEmployee(id="00005", name="Alice", role="Lead", level=3, nickname="小A")
        junior = FakeEmployee(id="00006", name="Bob", role="Engineer", level=1, nickname="小B")
        state = _mock_company_state(employees={"00005": senior, "00006": junior})

        call_idx = 0

        async def mock_ainvoke(llm, prompt, category="", employee_id=""):
            nonlocal call_idx
            call_idx += 1
            resp = MagicMock()
            if call_idx <= 2:
                resp.content = "Self eval"
            elif call_idx == 3:
                # Bad JSON with brackets (triggers JSONDecodeError at line 1152-1153)
                resp.content = '[invalid json in senior review]'
            else:
                resp.content = '[{"employee": "Bob", "improvements": ["fix"]}]'
            return resp

        with patch("onemancompany.core.routine.company_state", state), \
             patch("onemancompany.core.routine._publish", new_callable=AsyncMock), \
             patch("onemancompany.core.routine._chat", new_callable=AsyncMock), \
             patch("onemancompany.core.routine.tracked_ainvoke", mock_ainvoke), \
             patch("onemancompany.core.routine.make_llm", return_value=MagicMock()), \
             patch("onemancompany.core.routine.get_employee_skills_prompt", return_value=""), \
             patch("onemancompany.core.routine.get_employee_tools_prompt", return_value=""):
            result = await _run_phase1_legacy("task", ["00005", "00006"], "", "r1")

        assert result["senior_reviews"][0]["reviews"][0]["name"] == "all"

    @pytest.mark.asyncio
    async def test_legacy_phase1_json_decode_error_hr_summary(self, monkeypatch):
        """Legacy phase1: HR summary with bad JSON triggers JSONDecodeError."""
        from onemancompany.core.routine import _run_phase1_legacy

        emp = FakeEmployee(id="00005", name="Alice", role="Engineer", level=2, nickname="小A")
        state = _mock_company_state(employees={"00005": emp})

        call_idx = 0

        async def mock_ainvoke(llm, prompt, category="", employee_id=""):
            nonlocal call_idx
            call_idx += 1
            resp = MagicMock()
            if call_idx == 1:
                resp.content = "Self eval"
            else:
                # HR summary — bad JSON with brackets (triggers JSONDecodeError at line 1199-1200)
                resp.content = '[invalid json in hr summary]'
            return resp

        with patch("onemancompany.core.routine.company_state", state), \
             patch("onemancompany.core.routine._publish", new_callable=AsyncMock), \
             patch("onemancompany.core.routine._chat", new_callable=AsyncMock), \
             patch("onemancompany.core.routine.tracked_ainvoke", mock_ainvoke), \
             patch("onemancompany.core.routine.make_llm", return_value=MagicMock()), \
             patch("onemancompany.core.routine.get_employee_skills_prompt", return_value=""), \
             patch("onemancompany.core.routine.get_employee_tools_prompt", return_value=""):
            result = await _run_phase1_legacy("task", ["00005"], "", "r1")

        assert result["hr_summary"][0]["employee"] == "all"


# ---------------------------------------------------------------------------
# run_post_task_routine — project archive recording with senior_reviews
# ---------------------------------------------------------------------------

class TestRunPostTaskRoutineArchiveRecording:
    @pytest.mark.asyncio
    async def test_records_senior_reviews_in_archive(self, tmp_path, monkeypatch):
        """With project_id, senior reviews are recorded in project archive (line 966)."""
        emp1 = FakeEmployee(id="00004", name="EA", role="EA", level=4, nickname="EA")
        emp2 = FakeEmployee(id="00005", name="Alice", role="Lead", level=3, nickname="小A")
        emp3 = FakeEmployee(id="00006", name="Bob", role="Engineer", level=1, nickname="小B")
        room = FakeMeetingRoom(id="r1", name="Room A")
        state = _mock_company_state(
            employees={"00004": emp1, "00005": emp2, "00006": emp3},
            rooms={"r1": room},
        )

        monkeypatch.setattr("onemancompany.core.routine.REPORTS_DIR", tmp_path)
        monkeypatch.setattr("onemancompany.core.routine.company_state", state)
        monkeypatch.setattr("onemancompany.core.routine.FOUNDING_LEVEL", 4)
        monkeypatch.setattr("onemancompany.core.routine.EA_ID", "00004")
        monkeypatch.setattr("onemancompany.core.routine.update_employee_performance", lambda *a: None)

        project_record = {
            "id": "proj-1",
            "timeline": [
                {"employee_id": "00005", "action": "code", "detail": "Coded"},
                {"employee_id": "00006", "action": "test", "detail": "Tested"},
            ],
        }
        monkeypatch.setattr("onemancompany.core.project_archive.load_project", lambda pid: project_record)

        # Workflow with self-eval + senior review + CEO approval
        workflow_md = (
            "# Test\n\n"
            "- **Flow ID**: test\n"
            "- **Owner**: HR\n"
            "- **Collaborators**: COO\n"
            "- **Trigger**: manual\n\n"
            "---\n\n"
            "## Phase 1: Self-Evaluation\n\n"
            "- **Responsible**: Each participating employee\n"
            "- **Output**: Self eval\n\n"
            "## Phase 2: Senior Peer Review\n\n"
            "- **Responsible**: Senior employees\n"
            "- **Output**: Reviews\n\n"
            "## Phase 3: CEO Approval\n\n"
            "- **Responsible**: CEO\n"
            "- **Output**: Approved\n"
        )
        monkeypatch.setattr("onemancompany.core.routine.load_workflows",
                            lambda: {"project_retrospective_workflow": workflow_md})

        async def mock_ainvoke(llm, prompt, category="", employee_id=""):
            resp = MagicMock()
            if "JSON" in prompt and "review" in prompt.lower():
                resp.content = '[{"name": "Bob", "review": "Good work"}]'
            else:
                resp.content = "Test response"
            return resp

        mock_append = MagicMock()

        with patch("onemancompany.core.routine._publish", new_callable=AsyncMock), \
             patch("onemancompany.core.routine._chat", new_callable=AsyncMock), \
             patch("onemancompany.core.routine.tracked_ainvoke", mock_ainvoke), \
             patch("onemancompany.core.routine.make_llm", return_value=MagicMock()), \
             patch("onemancompany.core.routine.get_employee_skills_prompt", return_value=""), \
             patch("onemancompany.core.routine.get_employee_tools_prompt", return_value=""), \
             patch("onemancompany.core.project_archive.append_action", mock_append):
            await run_post_task_routine("Build X", project_id="proj-1")

        # append_action should be called for self_evaluations AND senior_reviews
        call_actions = [call[0][2] for call in mock_append.call_args_list]
        assert "自评" in call_actions
        assert "高级评审" in call_actions


# ---------------------------------------------------------------------------
# run_all_hands_meeting — with work_principles
# ---------------------------------------------------------------------------

class TestRunAllHandsMeetingAdditional:
    @pytest.mark.asyncio
    async def test_employee_with_principles(self, tmp_path, monkeypatch):
        """Employee with work_principles includes them in prompt."""
        emp = FakeEmployee(id="00005", name="Alice", role="Eng", nickname="小A",
                           work_principles="Be thorough")
        room = FakeMeetingRoom(id="r1", name="Hall", capacity=50)
        state = _mock_company_state(
            employees={"00005": emp},
            rooms={"r1": room},
        )

        monkeypatch.setattr("onemancompany.core.routine.company_state", state)
        monkeypatch.setattr("onemancompany.core.routine.REPORTS_DIR", tmp_path)
        monkeypatch.setattr("onemancompany.core.routine.CEO_ID", "00001")

        mock_ainvoke = AsyncMock(return_value=_make_llm_response("I will improve"))

        with patch("onemancompany.core.routine._publish", new_callable=AsyncMock), \
             patch("onemancompany.core.routine._chat", new_callable=AsyncMock), \
             patch("onemancompany.core.routine.tracked_ainvoke", mock_ainvoke), \
             patch("onemancompany.core.routine.make_llm", return_value=MagicMock()), \
             patch("onemancompany.core.routine.get_employee_skills_prompt", return_value=""), \
             patch("onemancompany.core.routine.get_employee_tools_prompt", return_value=""):
            await run_all_hands_meeting("Work harder")

        assert room.is_booked is False
