"""Unit tests for drift detection."""

import pytest

from onemancompany.core.drift_detector import DriftResult, ViolationCode, detect_drift
from onemancompany.core.models import AgentResult
from onemancompany.core.task_contract import TaskContract


class TestDriftDetector:
    @pytest.mark.asyncio
    async def test_cost_exceeded(self):
        contract = TaskContract(
            task_id="t1", title="test", goals=["test"],
            max_cost_usd=0.5,
        )
        result = AgentResult(success=True, output="ok", cost_usd=1.2)
        drift = await detect_drift(contract, result)
        assert drift.score >= 45  # high severity = 45 points
        assert any(v.code == ViolationCode.COST_EXCEEDED for v in drift.violations)

    @pytest.mark.asyncio
    async def test_protected_path_blocked(self):
        contract = TaskContract(
            task_id="t1", title="test", goals=["test"],
            protected_paths=[".env", "config.yaml"],
        )
        result = AgentResult(success=True, output="ok")
        drift = await detect_drift(contract, result, proposed_edits=[".env"])
        assert any(v.code == ViolationCode.PROTECTED_PATH for v in drift.violations)
        assert drift.score >= 45  # high severity = 45

    @pytest.mark.asyncio
    async def test_iteration_exceeded(self):
        contract = TaskContract(
            task_id="t1", title="test", goals=["test"],
            max_iterations=3,
        )
        result = AgentResult(success=True, output="ok", attempt=5)
        drift = await detect_drift(contract, result)
        assert any(v.code == ViolationCode.ITERATION_EXCEEDED for v in drift.violations)

    @pytest.mark.asyncio
    async def test_clean_execution(self):
        contract = TaskContract(
            task_id="t1", title="test", goals=["test"],
            max_cost_usd=5.0, max_iterations=10,
        )
        result = AgentResult(success=True, output="ok", cost_usd=0.1, attempt=1)
        drift = await detect_drift(contract, result)
        assert drift.safe
        assert drift.score == 0
        assert len(drift.violations) == 0

    @pytest.mark.asyncio
    async def test_multiple_violations_stack(self):
        contract = TaskContract(
            task_id="t1", title="test", goals=["test"],
            max_cost_usd=0.5, max_iterations=2,
            protected_paths=[".env"],
        )
        result = AgentResult(success=True, output="ok", cost_usd=1.0, attempt=5)
        drift = await detect_drift(contract, result, proposed_edits=[".env"])
        # cost (45) + iteration (25) + protected path (45) = 100 (capped)
        assert drift.score == 100
        assert len(drift.violations) == 3

    @pytest.mark.asyncio
    async def test_score_capped_at_100(self):
        contract = TaskContract(
            task_id="t1", title="test", goals=["test"],
            max_cost_usd=0.01, max_iterations=1,
            protected_paths=[".env", "config.yaml", "secret.key"],
        )
        result = AgentResult(success=True, output="ok", cost_usd=10.0, attempt=100)
        drift = await detect_drift(
            contract, result,
            proposed_edits=[".env", "config.yaml", "secret.key"],
        )
        assert drift.score == 100
