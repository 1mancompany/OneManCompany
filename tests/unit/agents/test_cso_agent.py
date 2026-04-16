"""Unit tests for agents/cso_agent.py — CSOAgent, sales pipeline tools."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from onemancompany.core.state import CompanyState, Employee


def _make_cs() -> CompanyState:
    cs = CompanyState()
    cs._next_employee_number = 100
    return cs


def _make_emp(emp_id: str, **kwargs) -> Employee:
    defaults = dict(
        id=emp_id, name=f"Emp {emp_id}", role="CSO",
        skills=["sales"], employee_number=emp_id, nickname="销售",
    )
    defaults.update(kwargs)
    return Employee(**defaults)


def _emp_to_dict(emp: Employee) -> dict:
    return {
        "id": emp.id, "name": emp.name, "role": emp.role,
        "skills": emp.skills, "nickname": emp.nickname,
        "level": getattr(emp, "level", 1),
        "department": getattr(emp, "department", ""),
        "tool_permissions": getattr(emp, "tool_permissions", []) or [],
        "guidance_notes": getattr(emp, "guidance_notes", []) or [],
        "runtime": {"status": "idle"},
    }


def _mock_store_for_employees(monkeypatch, employees: dict):
    from onemancompany.core import store as store_mod
    emp_dicts = {eid: _emp_to_dict(e) for eid, e in employees.items()}
    monkeypatch.setattr(store_mod, "load_employee",
                        lambda eid: emp_dicts.get(eid))
    monkeypatch.setattr(store_mod, "load_all_employees",
                        lambda: dict(emp_dicts))
    monkeypatch.setattr(store_mod, "load_employee_guidance",
                        lambda eid: (emp_dicts.get(eid) or {}).get("guidance_notes", []))
    monkeypatch.setattr(store_mod, "load_culture", lambda: [])
    monkeypatch.setattr(store_mod, "load_direction", lambda: "")


def _make_sales_task_dict(task_id: str, **kwargs) -> dict:
    defaults = dict(
        id=task_id,
        client_name="TestClient",
        description="Test task",
        requirements="Build X",
        budget_tokens=100,
        status="pending",
        assigned_to="",
        contract_approved=False,
        delivery="",
        settlement_tokens=0,
        created_at="",
    )
    defaults.update(kwargs)
    return defaults


def _mock_sales_store(monkeypatch, tasks: list[dict]):
    """Mock store-level sales task reads/writes for CSO tools."""
    from onemancompany.agents import cso_agent as cso_mod

    # The tasks list is mutated in place by the update helper
    monkeypatch.setattr(cso_mod._store, "load_sales_tasks", lambda: list(tasks))

    def _mock_save_sync(t):
        tasks.clear()
        tasks.extend(t)

    async def _mock_save(t):
        _mock_save_sync(t)

    monkeypatch.setattr(cso_mod._store, "save_sales_tasks", _mock_save)
    monkeypatch.setattr(cso_mod._store, "save_sales_tasks_sync", _mock_save_sync)

    # Mock overhead
    overhead_data = {"company_tokens": 0}
    monkeypatch.setattr(cso_mod._store, "load_overhead", lambda: dict(overhead_data))

    def _mock_save_oh_sync(data):
        overhead_data.update(data)

    async def _mock_save_oh(data):
        _mock_save_oh_sync(data)

    monkeypatch.setattr(cso_mod._store, "save_overhead", _mock_save_oh)
    monkeypatch.setattr(cso_mod._store, "save_overhead_sync", _mock_save_oh_sync)

    return tasks, overhead_data


# ---------------------------------------------------------------------------
# list_sales_tasks
# ---------------------------------------------------------------------------

class TestListSalesTasks:
    def test_returns_empty_list(self, monkeypatch):
        from onemancompany.agents import cso_agent as cso_mod

        tasks, _ = _mock_sales_store(monkeypatch, [])
        monkeypatch.setattr(cso_mod, "_append_activity", lambda entry: None)

        result = cso_mod.list_sales_tasks.invoke({})
        assert result == []

    def test_returns_all_sales_tasks(self, monkeypatch):
        from onemancompany.agents import cso_agent as cso_mod

        task_list = [
            _make_sales_task_dict("s1", client_name="Alpha"),
            _make_sales_task_dict("s2", client_name="Beta"),
        ]
        tasks, _ = _mock_sales_store(monkeypatch, task_list)
        monkeypatch.setattr(cso_mod, "_append_activity", lambda entry: None)

        result = cso_mod.list_sales_tasks.invoke({})
        assert len(result) == 2
        clients = {r["client_name"] for r in result}
        assert "Alpha" in clients
        assert "Beta" in clients


# ---------------------------------------------------------------------------
# review_contract
# ---------------------------------------------------------------------------

class TestReviewContract:
    def test_approve_dispatches_to_coo(self, monkeypatch):
        from onemancompany.agents import cso_agent as cso_mod

        task_list = [_make_sales_task_dict("s1")]
        tasks, _ = _mock_sales_store(monkeypatch, task_list)

        activity_log = []
        monkeypatch.setattr(cso_mod, "_append_activity", lambda entry: activity_log.append(entry))

        mock_loop = MagicMock()
        monkeypatch.setattr(
            "onemancompany.core.agent_loop.get_agent_loop",
            lambda eid: mock_loop,
        )

        result = cso_mod.review_contract.invoke({
            "task_id": "s1", "approved": True, "notes": "Looks good",
        })

        assert result["status"] == "approved"
        # Verify the task was updated on disk
        updated = [t for t in tasks if t["id"] == "s1"][0]
        assert updated["status"] == "in_production"
        assert updated["contract_approved"] is True
        mock_loop.push_task.assert_called_once()
        assert len(activity_log) == 1
        assert activity_log[0]["type"] == "contract_approved"

    def test_reject_records_reason(self, monkeypatch):
        from onemancompany.agents import cso_agent as cso_mod

        task_list = [_make_sales_task_dict("s1")]
        tasks, _ = _mock_sales_store(monkeypatch, task_list)

        activity_log = []
        monkeypatch.setattr(cso_mod, "_append_activity", lambda entry: activity_log.append(entry))

        result = cso_mod.review_contract.invoke({
            "task_id": "s1", "approved": False, "notes": "Scope unclear",
        })

        assert result["status"] == "rejected"
        updated = [t for t in tasks if t["id"] == "s1"][0]
        assert updated["status"] == "rejected"
        assert len(activity_log) == 1
        assert activity_log[0]["type"] == "contract_rejected"

    def test_review_nonexistent_task(self, monkeypatch):
        from onemancompany.agents import cso_agent as cso_mod

        _mock_sales_store(monkeypatch, [])
        monkeypatch.setattr(cso_mod, "_append_activity", lambda entry: None)

        result = cso_mod.review_contract.invoke({
            "task_id": "nonexistent", "approved": True,
        })
        assert result["status"] == "error"

    def test_cannot_review_already_in_production(self, monkeypatch):
        from onemancompany.agents import cso_agent as cso_mod

        task_list = [_make_sales_task_dict("s1", status="in_production")]
        _mock_sales_store(monkeypatch, task_list)
        monkeypatch.setattr(cso_mod, "_append_activity", lambda entry: None)

        result = cso_mod.review_contract.invoke({
            "task_id": "s1", "approved": True,
        })
        assert result["status"] == "error"

    def test_approve_with_no_coo_loop(self, monkeypatch):
        """Approving when COO loop is not available still updates status."""
        from onemancompany.agents import cso_agent as cso_mod

        task_list = [_make_sales_task_dict("s1")]
        tasks, _ = _mock_sales_store(monkeypatch, task_list)
        monkeypatch.setattr(cso_mod, "_append_activity", lambda entry: None)
        monkeypatch.setattr(
            "onemancompany.core.agent_loop.get_agent_loop",
            lambda eid: None,
        )

        result = cso_mod.review_contract.invoke({
            "task_id": "s1", "approved": True, "notes": "OK",
        })
        assert result["status"] == "approved"
        updated = [t for t in tasks if t["id"] == "s1"][0]
        assert updated["status"] == "in_production"


# ---------------------------------------------------------------------------
# complete_delivery
# ---------------------------------------------------------------------------

class TestCompleteDelivery:
    def test_marks_delivered(self, monkeypatch):
        from onemancompany.agents import cso_agent as cso_mod

        task_list = [_make_sales_task_dict("s1", status="in_production")]
        tasks, _ = _mock_sales_store(monkeypatch, task_list)
        monkeypatch.setattr(cso_mod, "_append_activity", lambda entry: None)

        result = cso_mod.complete_delivery.invoke({
            "task_id": "s1", "delivery_summary": "Built feature X",
        })

        assert result["status"] == "delivered"
        updated = [t for t in tasks if t["id"] == "s1"][0]
        assert updated["status"] == "delivered"
        assert updated["delivery"] == "Built feature X"

    def test_cannot_deliver_pending_task(self, monkeypatch):
        from onemancompany.agents import cso_agent as cso_mod

        task_list = [_make_sales_task_dict("s1", status="pending")]
        _mock_sales_store(monkeypatch, task_list)
        monkeypatch.setattr(cso_mod, "_append_activity", lambda entry: None)

        result = cso_mod.complete_delivery.invoke({
            "task_id": "s1", "delivery_summary": "Done",
        })
        assert result["status"] == "error"

    def test_nonexistent_task(self, monkeypatch):
        from onemancompany.agents import cso_agent as cso_mod

        _mock_sales_store(monkeypatch, [])
        monkeypatch.setattr(cso_mod, "_append_activity", lambda entry: None)

        result = cso_mod.complete_delivery.invoke({
            "task_id": "bad", "delivery_summary": "X",
        })
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# settle_task
# ---------------------------------------------------------------------------

class TestSettleTask:
    def test_collects_tokens(self, monkeypatch):
        from onemancompany.agents import cso_agent as cso_mod

        task_list = [_make_sales_task_dict("s1", status="delivered", budget_tokens=200)]
        tasks, overhead = _mock_sales_store(monkeypatch, task_list)
        monkeypatch.setattr(cso_mod, "_append_activity", lambda entry: None)

        result = cso_mod.settle_task.invoke({"task_id": "s1"})

        assert result["status"] == "settled"
        assert result["tokens_earned"] == 200
        updated = [t for t in tasks if t["id"] == "s1"][0]
        assert updated["status"] == "settled"
        assert updated["settlement_tokens"] == 200
        assert overhead["company_tokens"] == 200

    def test_cumulative_tokens(self, monkeypatch):
        from onemancompany.agents import cso_agent as cso_mod

        task_list = [_make_sales_task_dict("s1", status="delivered", budget_tokens=100)]
        tasks, overhead = _mock_sales_store(monkeypatch, task_list)
        overhead["company_tokens"] = 500
        monkeypatch.setattr(cso_mod, "_append_activity", lambda entry: None)

        result = cso_mod.settle_task.invoke({"task_id": "s1"})
        assert result["company_total_tokens"] == 600

    def test_cannot_settle_non_delivered(self, monkeypatch):
        from onemancompany.agents import cso_agent as cso_mod

        task_list = [_make_sales_task_dict("s1", status="in_production")]
        _mock_sales_store(monkeypatch, task_list)
        monkeypatch.setattr(cso_mod, "_append_activity", lambda entry: None)

        result = cso_mod.settle_task.invoke({"task_id": "s1"})
        assert result["status"] == "error"

    def test_settle_nonexistent_task(self, monkeypatch):
        from onemancompany.agents import cso_agent as cso_mod

        _mock_sales_store(monkeypatch, [])
        monkeypatch.setattr(cso_mod, "_append_activity", lambda entry: None)

        result = cso_mod.settle_task.invoke({"task_id": "nope"})
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# CSOAgent
# ---------------------------------------------------------------------------

class TestCSOAgent:
    def _make_agent(self, monkeypatch, cs=None, emp_overrides=None):
        from onemancompany.agents import cso_agent as cso_mod
        from onemancompany.agents import base as base_mod
        from onemancompany.core import state as state_mod
        from onemancompany.core import config as config_mod

        if cs is None:
            cs = _make_cs()
        emp = _make_emp(config_mod.CSO_ID)
        emps = {config_mod.CSO_ID: emp}
        if emp_overrides:
            emps.update(emp_overrides)
        _mock_store_for_employees(monkeypatch, emps)

        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(base_mod, "company_state", cs)
        monkeypatch.setattr(base_mod, "make_llm", lambda eid: MagicMock())
        monkeypatch.setattr(base_mod, "load_employee_skills", lambda eid: {})
        monkeypatch.setattr(base_mod, "EMPLOYEES_DIR", Path("/nonexistent"))
        monkeypatch.setattr(base_mod, "SHARED_PROMPTS_DIR", Path("/nonexistent"))
        monkeypatch.setattr(cso_mod, "create_react_agent", lambda model, tools: MagicMock())

        from onemancompany.agents.cso_agent import CSOAgent
        return CSOAgent()

    def test_init(self, monkeypatch):
        from onemancompany.core.config import CSO_ID

        agent = self._make_agent(monkeypatch)
        assert agent.role == "CSO"
        assert agent.employee_id == CSO_ID

    def test_build_prompt_contains_cso_prompt(self, monkeypatch):
        agent = self._make_agent(monkeypatch)
        prompt = agent._build_prompt()
        assert "Authorized Tools" in prompt
        assert len(prompt) > 500

    def test_build_prompt_contains_context(self, monkeypatch):
        agent = self._make_agent(monkeypatch)
        prompt = agent._build_prompt()
        assert "Current Context" in prompt

    def test_guidance_not_in_system_prompt(self, monkeypatch):
        """Guidance is injected via _build_company_context_block in task prompt, not system prompt."""
        from onemancompany.core import config as config_mod, store as store_mod

        cs = _make_cs()
        emp = _make_emp(config_mod.CSO_ID, guidance_notes=["Focus on revenue"])
        agent = self._make_agent(monkeypatch, cs=cs,
                                  emp_overrides={config_mod.CSO_ID: emp})
        monkeypatch.setattr(store_mod, "load_employee_guidance",
                            lambda eid: ["Focus on revenue"])
        prompt = agent._build_prompt()
        assert "Focus on revenue" not in prompt

    @pytest.mark.asyncio
    async def test_run(self, monkeypatch):
        from onemancompany.agents import cso_agent as cso_mod
        from onemancompany.agents import base as base_mod
        from onemancompany.core import state as state_mod, events as events_mod
        from onemancompany.core import config as config_mod

        cs = _make_cs()
        emp = _make_emp(config_mod.CSO_ID)
        _mock_store_for_employees(monkeypatch, {config_mod.CSO_ID: emp})
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(base_mod, "company_state", cs)
        monkeypatch.setattr(base_mod, "make_llm", lambda eid: MagicMock())
        monkeypatch.setattr(base_mod, "load_employee_skills", lambda eid: {})
        monkeypatch.setattr(base_mod, "EMPLOYEES_DIR", Path("/nonexistent"))
        monkeypatch.setattr(base_mod, "SHARED_PROMPTS_DIR", Path("/nonexistent"))

        mock_publish = AsyncMock()
        monkeypatch.setattr(events_mod, "event_bus", MagicMock(publish=mock_publish))
        monkeypatch.setattr(base_mod, "event_bus", MagicMock(publish=mock_publish))

        monkeypatch.setattr(
            "onemancompany.core.agent_loop._current_loop",
            MagicMock(get=lambda x=None: None),
        )

        final_msg = MagicMock()
        final_msg.content = "Contract reviewed"
        mock_agent = MagicMock()
        mock_agent.ainvoke = AsyncMock(return_value={"messages": [final_msg]})
        monkeypatch.setattr(cso_mod, "create_react_agent", lambda model, tools: mock_agent)

        from onemancompany.agents.cso_agent import CSOAgent
        agent = CSOAgent()

        result = await agent.run("Review contract X")
        assert result == "Contract reviewed"
        # _set_status is a no-op now; status persisted via store


# ---------------------------------------------------------------------------
# Sales pipeline lifecycle integration
# ---------------------------------------------------------------------------

class TestSalesPipelineLifecycle:
    def test_full_lifecycle(self, monkeypatch):
        """Test pending -> approved -> in_production -> delivered -> settled."""
        from onemancompany.agents import cso_agent as cso_mod

        task_list = [_make_sales_task_dict("s1", budget_tokens=500)]
        tasks, overhead = _mock_sales_store(monkeypatch, task_list)
        monkeypatch.setattr(cso_mod, "_append_activity", lambda entry: None)
        monkeypatch.setattr(
            "onemancompany.core.agent_loop.get_agent_loop",
            lambda eid: MagicMock(),
        )

        # 1. Review and approve
        result = cso_mod.review_contract.invoke({"task_id": "s1", "approved": True})
        assert tasks[0]["status"] == "in_production"

        # 2. Complete delivery
        result = cso_mod.complete_delivery.invoke({
            "task_id": "s1", "delivery_summary": "All done",
        })
        assert tasks[0]["status"] == "delivered"

        # 3. Settle
        result = cso_mod.settle_task.invoke({"task_id": "s1"})
        assert tasks[0]["status"] == "settled"
        assert overhead["company_tokens"] == 500


class TestCSOEdgeCases:
    def test_update_sales_task_no_event_loop(self, monkeypatch):
        """_update_sales_task_sync uses sync fallback when no running event loop."""
        from onemancompany.agents import cso_agent as cso_mod

        tasks = [{"id": "s1", "status": "draft", "value": 100}]
        monkeypatch.setattr(cso_mod, "_store", MagicMock(
            load_sales_tasks=MagicMock(return_value=tasks),
            save_sales_tasks_sync=MagicMock(),
        ))
        # No running loop — should use sync fallback
        cso_mod._update_sales_task_sync("s1", {"status": "active"})
        assert tasks[0]["status"] == "active"
        cso_mod._store.save_sales_tasks_sync.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_sales_task_with_event_loop(self, monkeypatch):
        """Line 56: _update_sales_task_sync uses create_task when loop is running."""
        from onemancompany.agents import cso_agent as cso_mod
        import asyncio

        tasks = [{"id": "s1", "status": "draft", "value": 100}]
        mock_store = MagicMock(
            load_sales_tasks=MagicMock(return_value=tasks),
            save_sales_tasks=AsyncMock(),
        )
        monkeypatch.setattr(cso_mod, "_store", mock_store)

        cso_mod._update_sales_task_sync("s1", {"status": "active"})
        assert tasks[0]["status"] == "active"
        # Allow the created task to run
        await asyncio.sleep(0)

    def test_save_overhead_tokens_no_event_loop(self, monkeypatch):
        """_save_overhead_tokens_sync sync fallback."""
        from onemancompany.agents import cso_agent as cso_mod

        monkeypatch.setattr(cso_mod, "_store", MagicMock(
            load_overhead=MagicMock(return_value={"company_tokens": 0}),
            save_overhead_sync=MagicMock(),
        ))
        cso_mod._save_overhead_tokens_sync(500)
        cso_mod._store.save_overhead_sync.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_overhead_tokens_with_event_loop(self, monkeypatch):
        """Line 75: _save_overhead_tokens_sync uses create_task when loop is running."""
        from onemancompany.agents import cso_agent as cso_mod
        import asyncio

        mock_store = MagicMock(
            load_overhead=MagicMock(return_value={"company_tokens": 0}),
            save_overhead=AsyncMock(),
        )
        monkeypatch.setattr(cso_mod, "_store", mock_store)

        cso_mod._save_overhead_tokens_sync(500)
        await asyncio.sleep(0)

    def test_get_role_identity_section_no_guide(self, monkeypatch):
        """Line 253: returns empty string when role_guide.md doesn't exist."""
        from onemancompany.agents import cso_agent as cso_mod
        from onemancompany.agents import base as base_mod
        from onemancompany.core import config as config_mod

        monkeypatch.setattr(base_mod, "make_llm", lambda eid: MagicMock())
        monkeypatch.setattr(cso_mod, "create_react_agent", lambda model, tools: MagicMock())
        monkeypatch.setattr(config_mod, "EMPLOYEES_DIR", Path("/nonexistent"))

        from onemancompany.agents.cso_agent import CSOAgent
        agent = CSOAgent()
        result = agent._get_role_identity_section()
        assert result == ""
