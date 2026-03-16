"""Unit tests for talent_market/remote_protocol.py — Pydantic models for remote workers."""

from __future__ import annotations

import pytest

from onemancompany.talent_market.remote_protocol import (
    HeartbeatPayload,
    RemoteWorkerRegistration,
    TaskAssignment,
    TaskResult,
)


# ---------------------------------------------------------------------------
# RemoteWorkerRegistration
# ---------------------------------------------------------------------------


class TestRemoteWorkerRegistration:
    def test_minimal(self):
        reg = RemoteWorkerRegistration(
            employee_id="00010",
            worker_url="http://localhost:9000",
        )
        assert reg.employee_id == "00010"
        assert reg.worker_url == "http://localhost:9000"
        assert reg.capabilities == []

    def test_with_capabilities(self):
        reg = RemoteWorkerRegistration(
            employee_id="00010",
            worker_url="http://localhost:9000",
            capabilities=["coding", "testing"],
        )
        assert reg.capabilities == ["coding", "testing"]

    def test_model_dump(self):
        reg = RemoteWorkerRegistration(
            employee_id="00010",
            worker_url="http://localhost:9000",
            capabilities=["coding"],
        )
        d = reg.model_dump()
        assert d["employee_id"] == "00010"
        assert d["worker_url"] == "http://localhost:9000"
        assert d["capabilities"] == ["coding"]

    def test_from_dict(self):
        data = {
            "employee_id": "00010",
            "worker_url": "http://example.com",
            "capabilities": ["design"],
        }
        reg = RemoteWorkerRegistration(**data)
        assert reg.employee_id == "00010"
        assert reg.capabilities == ["design"]

    def test_missing_required_field(self):
        with pytest.raises(Exception):
            RemoteWorkerRegistration(worker_url="http://localhost:9000")


# ---------------------------------------------------------------------------
# TaskAssignment
# ---------------------------------------------------------------------------


class TestTaskAssignment:
    def test_minimal(self):
        ta = TaskAssignment(
            task_id="task_001",
            project_id="proj_a",
            task_description="Build feature X",
        )
        assert ta.task_id == "task_001"
        assert ta.project_id == "proj_a"
        assert ta.task_description == "Build feature X"
        assert ta.project_dir == ""
        assert ta.context == {}

    def test_full(self):
        ta = TaskAssignment(
            task_id="task_002",
            project_id="proj_b",
            task_description="Fix bug Y",
            project_dir="/workspace/proj_b",
            context={"skills": ["python"], "tools": ["sandbox"]},
        )
        assert ta.project_dir == "/workspace/proj_b"
        assert ta.context["skills"] == ["python"]

    def test_model_dump(self):
        ta = TaskAssignment(
            task_id="t1",
            project_id="p1",
            task_description="desc",
        )
        d = ta.model_dump()
        assert "task_id" in d
        assert "project_id" in d
        assert "task_description" in d
        assert "project_dir" in d
        assert "context" in d


# ---------------------------------------------------------------------------
# TaskResult
# ---------------------------------------------------------------------------


class TestTaskResult:
    def test_minimal(self):
        tr = TaskResult(
            task_id="task_001",
            employee_id="00010",
            status="completed",
        )
        assert tr.task_id == "task_001"
        assert tr.employee_id == "00010"
        assert tr.status == "completed"
        assert tr.output == ""
        assert tr.artifacts == []
        assert tr.model_used == ""
        assert tr.input_tokens == 0
        assert tr.output_tokens == 0
        assert tr.estimated_cost_usd == 0.0

    def test_full(self):
        tr = TaskResult(
            task_id="task_001",
            employee_id="00010",
            status="completed",
            output="Done building feature X",
            artifacts=[{"type": "file", "path": "/output/result.py"}],
            model_used="claude-sonnet-4-6",
            input_tokens=5000,
            output_tokens=2000,
            estimated_cost_usd=0.05,
        )
        assert tr.output == "Done building feature X"
        assert len(tr.artifacts) == 1
        assert tr.estimated_cost_usd == 0.05

    def test_failed_status(self):
        tr = TaskResult(
            task_id="t1",
            employee_id="e1",
            status="failed",
            output="Error: something went wrong",
        )
        assert tr.status == "failed"

    def test_in_progress_status(self):
        tr = TaskResult(
            task_id="t1",
            employee_id="e1",
            status="in_progress",
        )
        assert tr.status == "in_progress"


# ---------------------------------------------------------------------------
# HeartbeatPayload
# ---------------------------------------------------------------------------


class TestHeartbeatPayload:
    def test_idle(self):
        hb = HeartbeatPayload(
            employee_id="00010",
            status="idle",
        )
        assert hb.employee_id == "00010"
        assert hb.status == "idle"
        assert hb.current_task_id is None

    def test_busy(self):
        hb = HeartbeatPayload(
            employee_id="00010",
            status="busy",
            current_task_id="task_001",
        )
        assert hb.status == "busy"
        assert hb.current_task_id == "task_001"

    def test_model_dump(self):
        hb = HeartbeatPayload(employee_id="e1", status="idle")
        d = hb.model_dump()
        assert d["employee_id"] == "e1"
        assert d["status"] == "idle"
        assert d["current_task_id"] is None

    def test_roundtrip(self):
        """Test serialization + deserialization roundtrip."""
        original = HeartbeatPayload(
            employee_id="00010",
            status="busy",
            current_task_id="task_xyz",
        )
        data = original.model_dump()
        restored = HeartbeatPayload(**data)
        assert restored.employee_id == original.employee_id
        assert restored.status == original.status
        assert restored.current_task_id == original.current_task_id
