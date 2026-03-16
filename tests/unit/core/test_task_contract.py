"""Unit tests for core/task_contract.py — TaskContract model."""

from __future__ import annotations

from datetime import datetime

import pytest

from onemancompany.core.task_contract import TaskContract


# ---------------------------------------------------------------------------
# Construction and defaults
# ---------------------------------------------------------------------------

class TestTaskContractDefaults:
    def test_minimal_construction(self):
        tc = TaskContract(task_id="t1", title="Test", goals=["goal1"])
        assert tc.task_id == "t1"
        assert tc.title == "Test"
        assert tc.goals == ["goal1"]

    def test_default_constraints(self):
        tc = TaskContract(task_id="t1", title="Test", goals=["g"])
        assert tc.constraints == []

    def test_default_assigned_agent(self):
        tc = TaskContract(task_id="t1", title="Test", goals=["g"])
        assert tc.assigned_agent == ""

    def test_default_allowed_tools(self):
        tc = TaskContract(task_id="t1", title="Test", goals=["g"])
        assert tc.allowed_tools == []

    def test_default_protected_paths(self):
        tc = TaskContract(task_id="t1", title="Test", goals=["g"])
        assert ".env" in tc.protected_paths
        assert "config.yaml" in tc.protected_paths
        assert "company/human_resource/" in tc.protected_paths

    def test_default_guardrails(self):
        tc = TaskContract(task_id="t1", title="Test", goals=["g"])
        assert tc.max_cost_usd == 1.0
        assert tc.max_iterations == 5
        assert tc.require_ceo_approval is False
        assert tc.risk_level == "low"

    def test_default_acceptance(self):
        tc = TaskContract(task_id="t1", title="Test", goals=["g"])
        assert tc.acceptance_criteria == []
        assert tc.verification_commands == []

    def test_created_at_auto(self):
        before = datetime.now()
        tc = TaskContract(task_id="t1", title="Test", goals=["g"])
        after = datetime.now()
        assert before <= tc.created_at <= after


# ---------------------------------------------------------------------------
# Custom values
# ---------------------------------------------------------------------------

class TestTaskContractCustom:
    def test_custom_guardrails(self):
        tc = TaskContract(
            task_id="t1", title="Risky task", goals=["deploy"],
            max_cost_usd=10.0, max_iterations=20,
            require_ceo_approval=True, risk_level="high",
        )
        assert tc.max_cost_usd == 10.0
        assert tc.max_iterations == 20
        assert tc.require_ceo_approval is True
        assert tc.risk_level == "high"

    def test_custom_constraints(self):
        tc = TaskContract(
            task_id="t1", title="Test", goals=["g"],
            constraints=["no deleting files", "must pass tests"],
        )
        assert len(tc.constraints) == 2

    def test_multiple_goals(self):
        tc = TaskContract(
            task_id="t1", title="Test", goals=["build", "test", "deploy"],
        )
        assert len(tc.goals) == 3


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

class TestTaskContractValidation:
    def test_risk_level_enum(self):
        for level in ("low", "medium", "high"):
            tc = TaskContract(task_id="t1", title="t", goals=["g"], risk_level=level)
            assert tc.risk_level == level

    def test_invalid_risk_level_raises(self):
        with pytest.raises(Exception):
            TaskContract(task_id="t1", title="t", goals=["g"], risk_level="extreme")

    def test_missing_required_fields(self):
        with pytest.raises(Exception):
            TaskContract(task_id="t1")  # missing title and goals


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

class TestTaskContractSerialization:
    def test_model_dump(self):
        tc = TaskContract(task_id="t1", title="Test", goals=["g"])
        d = tc.model_dump()
        assert d["task_id"] == "t1"
        assert d["goals"] == ["g"]
        assert "created_at" in d

    def test_json_roundtrip(self):
        tc = TaskContract(task_id="t1", title="Test", goals=["build", "test"])
        json_str = tc.model_dump_json()
        tc2 = TaskContract.model_validate_json(json_str)
        assert tc2.task_id == tc.task_id
        assert tc2.goals == tc.goals

    def test_protected_paths_independent_per_instance(self):
        tc1 = TaskContract(task_id="t1", title="T1", goals=["g"])
        tc2 = TaskContract(task_id="t2", title="T2", goals=["g"])
        tc1.protected_paths.append("/extra")
        assert "/extra" not in tc2.protected_paths
