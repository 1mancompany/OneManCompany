"""Unit tests for core/event_models.py — Typed event payloads."""

from __future__ import annotations

import pytest

from onemancompany.core.event_models import (
    AgentDonePayload,
    AgentLogPayload,
    AgentTaskUpdatePayload,
    AgentThinkingPayload,
    CandidatesReadyPayload,
    CompanyCultureUpdatedPayload,
    EmployeeFiredPayload,
    EmployeeHiredPayload,
    EmployeeReviewedPayload,
    FileEditProposedPayload,
    GuidancePayload,
    InquiryPayload,
    MeetingBookedPayload,
    MeetingChatPayload,
    ResolutionDecidedPayload,
    ResolutionReadyPayload,
    StateSnapshotPayload,
    TaskCompletedPayload,
    TaskStartedPayload,
    WorkflowUpdatedPayload,
)


# ---------------------------------------------------------------------------
# TaskStartedPayload
# ---------------------------------------------------------------------------

class TestTaskStartedPayload:
    def test_defaults(self):
        p = TaskStartedPayload()
        assert p.task_id == ""
        assert p.employee_id == ""
        assert p.description == ""

    def test_custom_values(self):
        p = TaskStartedPayload(task_id="t1", employee_id="00002", description="Build feature")
        assert p.task_id == "t1"
        assert p.employee_id == "00002"
        assert p.description == "Build feature"

    def test_serialization(self):
        p = TaskStartedPayload(task_id="t1", employee_id="e1", description="desc")
        d = p.model_dump()
        assert d == {"task_id": "t1", "employee_id": "e1", "description": "desc"}

    def test_json_roundtrip(self):
        p = TaskStartedPayload(task_id="t1")
        json_str = p.model_dump_json()
        p2 = TaskStartedPayload.model_validate_json(json_str)
        assert p2.task_id == "t1"


# ---------------------------------------------------------------------------
# TaskCompletedPayload
# ---------------------------------------------------------------------------

class TestTaskCompletedPayload:
    def test_defaults(self):
        p = TaskCompletedPayload()
        assert p.task_id == ""
        assert p.employee_id == ""
        assert p.success is True
        assert p.output_summary == ""
        assert p.cost_usd == 0.0

    def test_failed_task(self):
        p = TaskCompletedPayload(success=False, cost_usd=0.5, output_summary="error")
        assert p.success is False
        assert p.cost_usd == 0.5
        assert p.output_summary == "error"

    def test_serialization(self):
        p = TaskCompletedPayload(task_id="t1", success=False, cost_usd=1.23)
        d = p.model_dump()
        assert d["success"] is False
        assert d["cost_usd"] == 1.23


# ---------------------------------------------------------------------------
# AgentThinkingPayload
# ---------------------------------------------------------------------------

class TestAgentThinkingPayload:
    def test_defaults(self):
        p = AgentThinkingPayload()
        assert p.employee_id == ""
        assert p.message == ""
        assert p.content == ""
        assert p.tool_name is None

    def test_with_tool_name(self):
        p = AgentThinkingPayload(tool_name="search_web", message="thinking...")
        assert p.tool_name == "search_web"

    def test_tool_name_optional(self):
        p = AgentThinkingPayload(employee_id="e1")
        d = p.model_dump()
        assert d["tool_name"] is None


# ---------------------------------------------------------------------------
# AgentDonePayload
# ---------------------------------------------------------------------------

class TestAgentDonePayload:
    def test_defaults(self):
        p = AgentDonePayload()
        assert p.role == ""
        assert p.summary == ""
        assert p.employee_id == ""

    def test_custom(self):
        p = AgentDonePayload(role="HR", summary="Hired 3 people", employee_id="00002")
        assert p.role == "HR"


# ---------------------------------------------------------------------------
# AgentLogPayload
# ---------------------------------------------------------------------------

class TestAgentLogPayload:
    def test_defaults(self):
        p = AgentLogPayload()
        assert p.employee_id == ""
        assert p.log_type == ""
        assert p.content == ""


# ---------------------------------------------------------------------------
# AgentTaskUpdatePayload
# ---------------------------------------------------------------------------

class TestAgentTaskUpdatePayload:
    def test_defaults(self):
        p = AgentTaskUpdatePayload()
        assert p.status == ""

    def test_custom(self):
        p = AgentTaskUpdatePayload(employee_id="e1", task_id="t1", status="complete", summary="done")
        assert p.status == "complete"
        assert p.summary == "done"


# ---------------------------------------------------------------------------
# CandidatesReadyPayload
# ---------------------------------------------------------------------------

class TestCandidatesReadyPayload:
    def test_defaults(self):
        p = CandidatesReadyPayload()
        assert p.batch_id == ""
        assert p.jd == ""
        assert p.candidates == []

    def test_with_candidates(self):
        cands = [{"name": "Alice"}, {"name": "Bob"}]
        p = CandidatesReadyPayload(batch_id="b1", candidates=cands)
        assert len(p.candidates) == 2
        assert p.candidates[0]["name"] == "Alice"

    def test_candidates_list_is_independent(self):
        p1 = CandidatesReadyPayload()
        p2 = CandidatesReadyPayload()
        p1.candidates.append({"name": "test"})
        assert len(p2.candidates) == 0


# ---------------------------------------------------------------------------
# ResolutionReadyPayload / ResolutionDecidedPayload
# ---------------------------------------------------------------------------

class TestResolutionPayloads:
    def test_ready_defaults(self):
        p = ResolutionReadyPayload()
        assert p.resolution_id == ""
        assert p.edit_count == 0
        assert p.project_id is None

    def test_ready_with_project(self):
        p = ResolutionReadyPayload(project_id="proj1", edit_count=3)
        assert p.project_id == "proj1"

    def test_decided_defaults(self):
        p = ResolutionDecidedPayload()
        assert p.results == []

    def test_decided_with_results(self):
        p = ResolutionDecidedPayload(
            resolution_id="r1",
            project_id="p1",
            results=[{"edit_id": "e1", "approved": True}],
        )
        assert len(p.results) == 1


# ---------------------------------------------------------------------------
# MeetingChatPayload / MeetingBookedPayload
# ---------------------------------------------------------------------------

class TestMeetingPayloads:
    def test_chat_defaults(self):
        p = MeetingChatPayload()
        assert p.meeting_id == ""
        assert p.speaker_id == ""
        assert p.content == ""

    def test_booked_defaults(self):
        p = MeetingBookedPayload()
        assert p.participants == []

    def test_booked_with_participants(self):
        p = MeetingBookedPayload(
            meeting_id="m1", room_id="r1", booked_by="00002",
            participants=["00002", "00003"],
        )
        assert len(p.participants) == 2


# ---------------------------------------------------------------------------
# EmployeeHiredPayload / EmployeeFiredPayload / EmployeeReviewedPayload
# ---------------------------------------------------------------------------

class TestEmployeePayloads:
    def test_hired_defaults(self):
        p = EmployeeHiredPayload()
        assert p.name == ""
        assert p.employee_id == ""

    def test_hired_custom(self):
        p = EmployeeHiredPayload(name="Alice", nickname="A", role="Engineer", employee_id="00010")
        assert p.name == "Alice"

    def test_fired_defaults(self):
        p = EmployeeFiredPayload()
        assert p.reason == ""

    def test_fired_with_reason(self):
        p = EmployeeFiredPayload(name="Bob", employee_id="00010", reason="Performance")
        assert p.reason == "Performance"

    def test_reviewed_defaults(self):
        p = EmployeeReviewedPayload()
        assert p.score == 0.0
        assert p.quarter == 0


# ---------------------------------------------------------------------------
# FileEditProposedPayload
# ---------------------------------------------------------------------------

class TestFileEditProposedPayload:
    def test_defaults(self):
        p = FileEditProposedPayload()
        assert p.edit_id == ""
        assert p.file_path == ""

    def test_custom(self):
        p = FileEditProposedPayload(edit_id="e1", file_path="/src/main.py", employee_id="00002", reason="fix bug")
        assert p.reason == "fix bug"


# ---------------------------------------------------------------------------
# GuidancePayload
# ---------------------------------------------------------------------------

class TestGuidancePayload:
    def test_defaults(self):
        p = GuidancePayload()
        assert p.employee_id == ""
        assert p.content == ""


# ---------------------------------------------------------------------------
# StateSnapshotPayload
# ---------------------------------------------------------------------------

class TestStateSnapshotPayload:
    def test_empty_payload(self):
        p = StateSnapshotPayload()
        d = p.model_dump()
        assert d == {}


# ---------------------------------------------------------------------------
# InquiryPayload
# ---------------------------------------------------------------------------

class TestInquiryPayload:
    def test_defaults(self):
        p = InquiryPayload()
        assert p.employee_id == ""
        assert p.inquiry_id == ""
        assert p.question == ""


# ---------------------------------------------------------------------------
# WorkflowUpdatedPayload
# ---------------------------------------------------------------------------

class TestWorkflowUpdatedPayload:
    def test_defaults(self):
        p = WorkflowUpdatedPayload()
        assert p.workflow_id == ""
        assert p.phase == ""


# ---------------------------------------------------------------------------
# CompanyCultureUpdatedPayload
# ---------------------------------------------------------------------------

class TestCompanyCultureUpdatedPayload:
    def test_defaults(self):
        p = CompanyCultureUpdatedPayload()
        assert p.items == []

    def test_with_items(self):
        items = [{"title": "Integrity", "description": "Be honest"}]
        p = CompanyCultureUpdatedPayload(items=items)
        assert len(p.items) == 1


# ---------------------------------------------------------------------------
# Cross-cutting: model_validate from dict
# ---------------------------------------------------------------------------

class TestFromDict:
    def test_task_started_from_dict(self):
        d = {"task_id": "t1", "employee_id": "e1", "description": "foo"}
        p = TaskStartedPayload.model_validate(d)
        assert p.task_id == "t1"

    def test_unknown_fields_ignored_with_strict_false(self):
        # Pydantic v2 default: extra fields are ignored
        d = {"task_id": "t1", "extra_field": "ignored"}
        p = TaskStartedPayload.model_validate(d)
        assert p.task_id == "t1"
