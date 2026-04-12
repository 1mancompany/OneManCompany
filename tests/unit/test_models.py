"""Unit tests for core Pydantic models."""

import pytest
from pydantic import ValidationError

from onemancompany.core.models import (
    AgentResult,
    CostRecord,
    DecisionStatus,
    Department,
    EmployeeRole,
    FileEditProposal,
    HostingMode,
    OverheadCosts,
    PerformanceRecord,
    Resolution,
    TaskPhase,
)


class TestPerformanceRecord:
    def test_valid_score(self):
        record = PerformanceRecord(
            quarter=1, score=3.75, tasks_completed=3, reviewer="00002",
        )
        assert record.score == 3.75
        assert record.quarter == 1

    def test_score_boundaries(self):
        # Valid boundaries
        PerformanceRecord(quarter=1, score=0.0, tasks_completed=0, reviewer="00002")
        PerformanceRecord(quarter=1, score=5.0, tasks_completed=0, reviewer="00002")

    def test_invalid_score_rejected(self):
        with pytest.raises(ValidationError):
            PerformanceRecord(quarter=1, score=5.5, tasks_completed=0, reviewer="00002")

    def test_negative_score_rejected(self):
        with pytest.raises(ValidationError):
            PerformanceRecord(quarter=1, score=-1.0, tasks_completed=0, reviewer="00002")


class TestCostRecord:
    def test_valid_record(self, sample_cost_record):
        assert sample_cost_record.category == "agent_task"
        assert sample_cost_record.input_tokens == 1000
        assert sample_cost_record.output_tokens == 500

    def test_negative_tokens_rejected(self):
        with pytest.raises(ValidationError):
            CostRecord(category="test", model="m", input_tokens=-1, output_tokens=0, cost_usd=0.0)

    def test_negative_cost_rejected(self):
        with pytest.raises(ValidationError):
            CostRecord(category="test", model="m", input_tokens=0, output_tokens=0, cost_usd=-0.01)


class TestOverheadCosts:
    def test_add_and_total(self, sample_cost_record):
        costs = OverheadCosts()
        costs.add(sample_cost_record)
        assert costs.total_cost_usd == 0.012
        assert costs.total_tokens == 1500
        assert costs.total_input_tokens == 1000
        assert costs.total_output_tokens == 500

    def test_multiple_adds(self, sample_cost_record):
        costs = OverheadCosts()
        costs.add(sample_cost_record)
        costs.add(sample_cost_record)
        assert costs.total_cost_usd == pytest.approx(0.024)
        assert costs.total_tokens == 3000
        assert len(costs.records) == 2

    def test_by_category(self, sample_cost_record):
        costs = OverheadCosts()
        costs.add(sample_cost_record)
        assert "agent_task" in costs.by_category
        assert costs.by_category["agent_task"]["cost_usd"] == 0.012

    def test_empty(self):
        costs = OverheadCosts()
        assert costs.total_cost_usd == 0.0
        assert costs.total_tokens == 0
        assert len(costs.records) == 0


class TestAgentResult:
    def test_success(self, sample_agent_result):
        assert sample_agent_result.success is True
        assert sample_agent_result.attempt == 1

    def test_cost_non_negative(self):
        with pytest.raises(ValidationError):
            AgentResult(success=True, output="ok", cost_usd=-0.5)

    def test_failure(self):
        result = AgentResult(success=False, output="", error="Timeout", attempt=3)
        assert result.success is False
        assert result.error == "Timeout"


class TestEnums:
    def test_task_phase_values(self):
        assert TaskPhase.PENDING.value == "pending"
        assert TaskPhase.COMPLETED.value == "completed"
        assert TaskPhase.PROCESSING.value == "processing"
        assert TaskPhase.FAILED.value == "failed"
        assert TaskPhase.FINISHED.value == "finished"

    def test_decision_status_values(self):
        assert DecisionStatus.PENDING.value == "pending"
        assert DecisionStatus.APPROVED.value == "approved"
        assert DecisionStatus.REJECTED.value == "rejected"
        assert DecisionStatus.DEFERRED.value == "deferred"

    def test_hosting_mode_values(self):
        assert HostingMode.COMPANY.value == "company"
        assert HostingMode.SELF.value == "self"
        assert HostingMode.REMOTE.value == "remote"

    def test_department_values(self):
        assert Department.ENGINEERING.value == "Engineering"
        assert Department.HR.value == "HR"

    def test_employee_role_values(self):
        assert EmployeeRole.ENGINEER.value == "Engineer"
        assert EmployeeRole.HR.value == "Human Resources"


class TestResolutionModels:
    def test_file_edit_proposal(self):
        edit = FileEditProposal(
            edit_id="edit_001",
            file_path="/tmp/test.py",
            new_content="print('hello')",
            reason="Add greeting",
            proposed_by="00008",
        )
        assert edit.decision is None
        assert edit.executed is False

    def test_resolution(self):
        res = Resolution(
            resolution_id="20260304_120000_abc123",
            project_id="proj_1",
            employee_id="00008",
            edits=[
                FileEditProposal(edit_id="e1", file_path="/tmp/a.py"),
                FileEditProposal(edit_id="e2", file_path="/tmp/b.py"),
            ],
        )
        assert len(res.edits) == 2
        assert res.status == DecisionStatus.PENDING


class TestProductEnums:
    def test_product_status_values(self):
        from onemancompany.core.models import ProductStatus
        assert ProductStatus.PLANNING.value == "planning"
        assert ProductStatus.ACTIVE.value == "active"
        assert ProductStatus.ARCHIVED.value == "archived"

    def test_issue_priority_values(self):
        from onemancompany.core.models import IssuePriority
        assert IssuePriority.P0.value == "P0"
        assert IssuePriority.P1.value == "P1"
        assert IssuePriority.P2.value == "P2"
        assert IssuePriority.P3.value == "P3"

    def test_issue_status_values(self):
        from onemancompany.core.models import IssueStatus
        assert IssueStatus.OPEN.value == "open"
        assert IssueStatus.IN_PROGRESS.value == "in_progress"
        assert IssueStatus.CLOSED.value == "closed"

    def test_issue_resolution_values(self):
        from onemancompany.core.models import IssueResolution
        assert IssueResolution.FIXED.value == "fixed"
        assert IssueResolution.WONTFIX.value == "wontfix"
        assert IssueResolution.DUPLICATE.value == "duplicate"
        assert IssueResolution.BY_DESIGN.value == "by_design"

    def test_event_type_product_events(self):
        from onemancompany.core.models import EventType
        assert EventType.PRODUCT_CREATED.value == "product_created"
        assert EventType.ISSUE_CREATED.value == "issue_created"
        assert EventType.ISSUE_CLOSED.value == "issue_closed"
        assert EventType.KR_UPDATED.value == "kr_updated"
        assert EventType.VERSION_RELEASED.value == "version_released"

    def test_product_enums_are_str(self):
        from onemancompany.core.models import ProductStatus, IssuePriority, IssueStatus, IssueResolution
        # All should be str enums for YAML serialization
        assert isinstance(ProductStatus.ACTIVE, str)
        assert isinstance(IssuePriority.P0, str)
        assert isinstance(IssueStatus.OPEN, str)
        assert isinstance(IssueResolution.FIXED, str)
