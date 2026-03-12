"""Unit tests for agents/cso_agent.py — CSOAgent, sales pipeline tools."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from onemancompany.core.state import CompanyState, Employee, SalesTask


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


def _make_sales_task(task_id: str, **kwargs) -> SalesTask:
    defaults = dict(
        id=task_id,
        client_name="TestClient",
        description="Test task",
        requirements="Build X",
        budget_tokens=100,
        status="pending",
    )
    defaults.update(kwargs)
    return SalesTask(**defaults)


# ---------------------------------------------------------------------------
# list_sales_tasks
# ---------------------------------------------------------------------------

class TestListSalesTasks:
    def test_returns_empty_list(self, monkeypatch):
        from onemancompany.agents import cso_agent as cso_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(cso_mod, "company_state", cs)

        result = cso_mod.list_sales_tasks.invoke({})
        assert result == []

    def test_returns_all_sales_tasks(self, monkeypatch):
        from onemancompany.agents import cso_agent as cso_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        cs.sales_tasks = {
            "s1": _make_sales_task("s1", client_name="Alpha"),
            "s2": _make_sales_task("s2", client_name="Beta"),
        }
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(cso_mod, "company_state", cs)

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
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        task = _make_sales_task("s1")
        cs.sales_tasks["s1"] = task
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(cso_mod, "company_state", cs)

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
        assert task.status == "in_production"
        assert task.contract_approved is True
        mock_loop.push_task.assert_called_once()
        assert len(activity_log) == 1
        assert activity_log[0]["type"] == "contract_approved"

    def test_reject_records_reason(self, monkeypatch):
        from onemancompany.agents import cso_agent as cso_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        task = _make_sales_task("s1")
        cs.sales_tasks["s1"] = task
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(cso_mod, "company_state", cs)

        activity_log = []
        monkeypatch.setattr(cso_mod, "_append_activity", lambda entry: activity_log.append(entry))

        result = cso_mod.review_contract.invoke({
            "task_id": "s1", "approved": False, "notes": "Scope unclear",
        })

        assert result["status"] == "rejected"
        assert task.status == "rejected"
        assert len(activity_log) == 1
        assert activity_log[0]["type"] == "contract_rejected"

    def test_review_nonexistent_task(self, monkeypatch):
        from onemancompany.agents import cso_agent as cso_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(cso_mod, "company_state", cs)

        result = cso_mod.review_contract.invoke({
            "task_id": "nonexistent", "approved": True,
        })
        assert result["status"] == "error"

    def test_cannot_review_already_in_production(self, monkeypatch):
        from onemancompany.agents import cso_agent as cso_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        task = _make_sales_task("s1", status="in_production")
        cs.sales_tasks["s1"] = task
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(cso_mod, "company_state", cs)

        result = cso_mod.review_contract.invoke({
            "task_id": "s1", "approved": True,
        })
        assert result["status"] == "error"

    def test_approve_with_no_coo_loop(self, monkeypatch):
        """Approving when COO loop is not available still updates status."""
        from onemancompany.agents import cso_agent as cso_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        task = _make_sales_task("s1")
        cs.sales_tasks["s1"] = task
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(cso_mod, "company_state", cs)
        monkeypatch.setattr(
            "onemancompany.core.agent_loop.get_agent_loop",
            lambda eid: None,
        )

        result = cso_mod.review_contract.invoke({
            "task_id": "s1", "approved": True, "notes": "OK",
        })
        assert result["status"] == "approved"
        assert task.status == "in_production"


# ---------------------------------------------------------------------------
# complete_delivery
# ---------------------------------------------------------------------------

class TestCompleteDelivery:
    def test_marks_delivered(self, monkeypatch):
        from onemancompany.agents import cso_agent as cso_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        task = _make_sales_task("s1", status="in_production")
        cs.sales_tasks["s1"] = task
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(cso_mod, "company_state", cs)

        result = cso_mod.complete_delivery.invoke({
            "task_id": "s1", "delivery_summary": "Built feature X",
        })

        assert result["status"] == "delivered"
        assert task.status == "delivered"
        assert task.delivery == "Built feature X"

    def test_cannot_deliver_pending_task(self, monkeypatch):
        from onemancompany.agents import cso_agent as cso_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        task = _make_sales_task("s1", status="pending")
        cs.sales_tasks["s1"] = task
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(cso_mod, "company_state", cs)

        result = cso_mod.complete_delivery.invoke({
            "task_id": "s1", "delivery_summary": "Done",
        })
        assert result["status"] == "error"

    def test_nonexistent_task(self, monkeypatch):
        from onemancompany.agents import cso_agent as cso_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(cso_mod, "company_state", cs)

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
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        task = _make_sales_task("s1", status="delivered", budget_tokens=200)
        cs.sales_tasks["s1"] = task
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(cso_mod, "company_state", cs)

        result = cso_mod.settle_task.invoke({"task_id": "s1"})

        assert result["status"] == "settled"
        assert result["tokens_earned"] == 200
        assert task.status == "settled"
        assert task.settlement_tokens == 200
        assert cs.company_tokens == 200

    def test_cumulative_tokens(self, monkeypatch):
        from onemancompany.agents import cso_agent as cso_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        cs.company_tokens = 500
        task = _make_sales_task("s1", status="delivered", budget_tokens=100)
        cs.sales_tasks["s1"] = task
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(cso_mod, "company_state", cs)

        result = cso_mod.settle_task.invoke({"task_id": "s1"})
        assert result["company_total_tokens"] == 600

    def test_cannot_settle_non_delivered(self, monkeypatch):
        from onemancompany.agents import cso_agent as cso_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        task = _make_sales_task("s1", status="in_production")
        cs.sales_tasks["s1"] = task
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(cso_mod, "company_state", cs)

        result = cso_mod.settle_task.invoke({"task_id": "s1"})
        assert result["status"] == "error"

    def test_settle_nonexistent_task(self, monkeypatch):
        from onemancompany.agents import cso_agent as cso_mod
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(cso_mod, "company_state", cs)

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
        monkeypatch.setattr(cso_mod, "company_state", cs)
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
        assert "Chief Sales Officer" in prompt
        assert "Sales Pipeline" in prompt

    def test_build_prompt_contains_context(self, monkeypatch):
        agent = self._make_agent(monkeypatch)
        prompt = agent._build_prompt()
        assert "Current Context" in prompt

    def test_build_prompt_with_guidance(self, monkeypatch):
        from onemancompany.core import config as config_mod, store as store_mod

        cs = _make_cs()
        emp = _make_emp(config_mod.CSO_ID, guidance_notes=["Focus on revenue"])
        agent = self._make_agent(monkeypatch, cs=cs,
                                  emp_overrides={config_mod.CSO_ID: emp})
        monkeypatch.setattr(store_mod, "load_employee_guidance",
                            lambda eid: ["Focus on revenue"])
        prompt = agent._build_prompt()
        assert "Focus on revenue" in prompt

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
        monkeypatch.setattr(cso_mod, "company_state", cs)
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
        from onemancompany.core import state as state_mod

        cs = _make_cs()
        task = _make_sales_task("s1", budget_tokens=500)
        cs.sales_tasks["s1"] = task
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(cso_mod, "company_state", cs)
        monkeypatch.setattr(
            "onemancompany.core.agent_loop.get_agent_loop",
            lambda eid: MagicMock(),
        )

        # 1. Review and approve
        result = cso_mod.review_contract.invoke({"task_id": "s1", "approved": True})
        assert task.status == "in_production"

        # 2. Complete delivery
        result = cso_mod.complete_delivery.invoke({
            "task_id": "s1", "delivery_summary": "All done",
        })
        assert task.status == "delivered"

        # 3. Settle
        result = cso_mod.settle_task.invoke({"task_id": "s1"})
        assert task.status == "settled"
        assert cs.company_tokens == 500
