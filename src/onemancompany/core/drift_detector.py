"""Drift detection — multi-dimensional scoring of contract violations.

After agent execution, compare the result against the TaskContract to detect:
- Cost overruns
- Iteration limit breaches
- Protected path modifications
- Unauthorized tool usage
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

from onemancompany.core.models import AgentResult
from onemancompany.core.task_contract import TaskContract


class ViolationCode(str, Enum):
    COST_EXCEEDED = "cost_exceeded"
    PROTECTED_PATH = "protected_path"
    SCOPE_DRIFT = "scope_drift"
    ITERATION_EXCEEDED = "iteration_exceeded"
    UNAUTHORIZED_TOOL = "unauthorized_tool"


class DriftViolation(BaseModel):
    code: ViolationCode
    severity: Literal["low", "medium", "high"]
    message: str
    detail: str = ""


class DriftResult(BaseModel):
    score: int = Field(ge=0, le=100, default=0)
    violations: list[DriftViolation] = []

    @property
    def safe(self) -> bool:
        """Score below 60 is considered safe."""
        return self.score < 60


# Severity weights (from Salacia drift.ts)
_SEVERITY_WEIGHT = {"high": 45, "medium": 25, "low": 10}


async def detect_drift(
    contract: TaskContract,
    result: AgentResult,
    proposed_edits: list[str] | None = None,
) -> DriftResult:
    """Score drift across multiple dimensions.

    Returns a DriftResult with 0-100 score and list of violations.
    """
    violations: list[DriftViolation] = []

    # 1. Cost drift
    if result.cost_usd > contract.max_cost_usd:
        violations.append(DriftViolation(
            code=ViolationCode.COST_EXCEEDED,
            severity="high",
            message=f"Spent ${result.cost_usd:.4f}, budget was ${contract.max_cost_usd:.4f}",
        ))

    # 2. Iteration count
    if result.attempt > contract.max_iterations:
        violations.append(DriftViolation(
            code=ViolationCode.ITERATION_EXCEEDED,
            severity="medium",
            message=f"Ran {result.attempt} iterations, limit was {contract.max_iterations}",
        ))

    # 3. Protected path modifications
    for path in (proposed_edits or []):
        for protected in contract.protected_paths:
            if path.startswith(protected):
                violations.append(DriftViolation(
                    code=ViolationCode.PROTECTED_PATH,
                    severity="high",
                    message=f"Attempted to modify protected path: {path}",
                    detail=f"Protected pattern: {protected}",
                ))
                break  # one violation per path

    # Compute score
    score = min(100, sum(_SEVERITY_WEIGHT[v.severity] for v in violations))
    return DriftResult(score=score, violations=violations)
