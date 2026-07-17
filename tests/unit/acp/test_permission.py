"""Unit tests for the ACP policy engine (TDD — written before implementation).

Run with:
    .venv/bin/python -m pytest tests/unit/acp/test_permission.py -v
"""
from __future__ import annotations

from pathlib import Path

import pytest

from onemancompany.acp.permission import PolicyDecision, PolicyEngine


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def yaml_path(tmp_path: Path) -> Path:
    """Write the canonical permissions.yaml to a temp dir and return its path."""
    rules_file = tmp_path / "permissions.yaml"
    rules_file.write_text(
        """
default: allow
rules:
  - match: {tool: "write", target_exists: true, owner_is_self: false}
    action: reject
    reason: "Cannot overwrite files owned by other employees"
  - match: {tool: "external_api"}
    action: reject
    reason: "External API calls require explicit policy allowance"
  - match: {cost_usd_gt: 10.0}
    action: reject
    reason: "Single operation exceeds budget threshold"
""",
        encoding="utf-8",
    )
    return rules_file


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDefaultAllow:
    """test_default_allow — no matching rule → allow."""

    def test_default_allow(self, yaml_path: Path) -> None:
        engine = PolicyEngine(yaml_path)
        decision = engine.decide(tool="some_random_tool", args={}, context={})
        assert isinstance(decision, PolicyDecision)
        assert decision.allowed is True


class TestRejectOverwriteOthersFile:
    """test_reject_overwrite_others_file — tool=write, target_exists=true, owner_is_self=false → reject."""

    def test_reject_overwrite_others_file(self, yaml_path: Path) -> None:
        engine = PolicyEngine(yaml_path)
        decision = engine.decide(
            tool="write",
            args={},
            context={"target_exists": True, "owner_is_self": False},
        )
        assert decision.allowed is False
        assert "overwrite" in decision.reason.lower() or decision.reason != ""

    def test_allow_write_own_file(self, yaml_path: Path) -> None:
        """Same tool=write but owner_is_self=True — rule should NOT match → allow."""
        engine = PolicyEngine(yaml_path)
        decision = engine.decide(
            tool="write",
            args={},
            context={"target_exists": True, "owner_is_self": True},
        )
        assert decision.allowed is True


class TestRejectExternalApi:
    """test_reject_external_api — tool=external_api → reject."""

    def test_reject_external_api(self, yaml_path: Path) -> None:
        engine = PolicyEngine(yaml_path)
        decision = engine.decide(tool="external_api", args={}, context={})
        assert decision.allowed is False
        assert decision.reason != ""


class TestBudgetThreshold:
    """test_reject_over_budget / test_allow_under_budget."""

    def test_reject_over_budget(self, yaml_path: Path) -> None:
        """cost_usd=15.0 with cost_usd_gt=10.0 rule → reject."""
        engine = PolicyEngine(yaml_path)
        decision = engine.decide(
            tool="anything",
            args={},
            context={"cost_usd": 15.0},
        )
        assert decision.allowed is False
        assert "budget" in decision.reason.lower() or decision.reason != ""

    def test_allow_under_budget(self, yaml_path: Path) -> None:
        """cost_usd=5.0 → rule doesn't match → allow."""
        engine = PolicyEngine(yaml_path)
        decision = engine.decide(
            tool="anything",
            args={},
            context={"cost_usd": 5.0},
        )
        assert decision.allowed is True


class TestMissingYaml:
    """test_missing_yaml_defaults_allow — nonexistent path → allow with no crash."""

    def test_missing_yaml_defaults_allow(self, tmp_path: Path) -> None:
        nonexistent = tmp_path / "does_not_exist.yaml"
        engine = PolicyEngine(nonexistent)
        decision = engine.decide(tool="write", args={}, context={})
        assert decision.allowed is True
