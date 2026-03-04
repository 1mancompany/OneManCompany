"""Shared test fixtures for all test levels."""

from __future__ import annotations

import pytest

from onemancompany.core.models import (
    AgentResult,
    CostRecord,
    OverheadCosts,
    PerformanceRecord,
)
from onemancompany.core.task_contract import TaskContract
from onemancompany.core.task_lifecycle import TaskPhase


@pytest.fixture
def sample_cost_record() -> CostRecord:
    return CostRecord(
        category="agent_task",
        model="anthropic/claude-sonnet-4-6",
        input_tokens=1000,
        output_tokens=500,
        cost_usd=0.012,
        employee_id="00002",
    )


@pytest.fixture
def sample_performance_record() -> PerformanceRecord:
    return PerformanceRecord(
        quarter=1,
        score=3.75,
        tasks_completed=3,
        reviewer="00002",
        notes="Excellent work",
    )


@pytest.fixture
def sample_contract() -> TaskContract:
    return TaskContract(
        task_id="t1",
        title="Test task",
        goals=["Build feature X"],
        max_cost_usd=0.5,
        max_iterations=5,
    )


@pytest.fixture
def sample_agent_result() -> AgentResult:
    return AgentResult(
        success=True,
        output="Task completed successfully",
        cost_usd=0.1,
        attempt=1,
    )
