"""Unit tests for agents/onboarding.py — employee hire execution & nickname generation."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from onemancompany.core.state import CompanyState, Employee


# ---------------------------------------------------------------------------
# Helpers — isolated CompanyState for testing
# ---------------------------------------------------------------------------

def _make_company_state() -> CompanyState:
    """Create a fresh CompanyState with no employees."""
    cs = CompanyState()
    cs._next_employee_number = 100  # start from 00100
    return cs


def _make_employee(emp_id: str, nickname: str = "", **kwargs) -> Employee:
    defaults = dict(
        id=emp_id, name=f"Emp {emp_id}", role="Engineer",
        skills=["python"], employee_number=emp_id, nickname=nickname,
    )
    defaults.update(kwargs)
    return Employee(**defaults)


# ---------------------------------------------------------------------------
# _get_existing_nicknames
# ---------------------------------------------------------------------------

class TestGetExistingNicknames:
    def test_collects_from_employees_and_ex(self, monkeypatch):
        from onemancompany.agents import onboarding
        from onemancompany.core import state as state_mod

        cs = _make_company_state()
        cs.employees = {
            "001": _make_employee("001", nickname="追风"),
            "002": _make_employee("002", nickname="凌霄"),
        }
        cs.ex_employees = {
            "003": _make_employee("003", nickname="破军"),
        }
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(onboarding, "company_state", cs)

        nicknames = onboarding._get_existing_nicknames()
        assert nicknames == {"追风", "凌霄", "破军"}

    def test_empty_when_no_employees(self, monkeypatch):
        from onemancompany.agents import onboarding
        from onemancompany.core import state as state_mod

        cs = _make_company_state()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(onboarding, "company_state", cs)

        nicknames = onboarding._get_existing_nicknames()
        assert nicknames == set()

    def test_skips_empty_nicknames(self, monkeypatch):
        from onemancompany.agents import onboarding
        from onemancompany.core import state as state_mod

        cs = _make_company_state()
        cs.employees = {
            "001": _make_employee("001", nickname="追风"),
            "002": _make_employee("002", nickname=""),  # no nickname
        }
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(onboarding, "company_state", cs)

        nicknames = onboarding._get_existing_nicknames()
        assert nicknames == {"追风"}


# ---------------------------------------------------------------------------
# generate_nickname
# ---------------------------------------------------------------------------

class TestGenerateNickname:
    @pytest.mark.asyncio
    async def test_generates_unique_2char_nickname(self, monkeypatch):
        from onemancompany.agents import onboarding, base as base_mod

        monkeypatch.setattr(onboarding, "_get_existing_nicknames", lambda: set())

        mock_llm = MagicMock()
        mock_result = MagicMock()
        mock_result.content = "追风"

        async def fake_tracked_ainvoke(*args, **kwargs):
            return mock_result

        monkeypatch.setattr(base_mod, "make_llm", lambda _: mock_llm)
        monkeypatch.setattr(base_mod, "tracked_ainvoke", fake_tracked_ainvoke)

        nickname = await onboarding.generate_nickname("Test Dev", "Engineer", is_founding=False)
        assert nickname == "追风"
        assert len(nickname) == 2

    @pytest.mark.asyncio
    async def test_generates_3char_for_founding(self, monkeypatch):
        from onemancompany.agents import onboarding, base as base_mod

        monkeypatch.setattr(onboarding, "_get_existing_nicknames", lambda: set())

        mock_llm = MagicMock()
        mock_result = MagicMock()
        mock_result.content = "逍遥子"

        async def fake_tracked_ainvoke(*args, **kwargs):
            return mock_result

        monkeypatch.setattr(base_mod, "make_llm", lambda _: mock_llm)
        monkeypatch.setattr(base_mod, "tracked_ainvoke", fake_tracked_ainvoke)

        nickname = await onboarding.generate_nickname("Boss", "COO", is_founding=True)
        assert nickname == "逍遥子"
        assert len(nickname) == 3

    @pytest.mark.asyncio
    async def test_retries_on_duplicate(self, monkeypatch):
        from onemancompany.agents import onboarding, base as base_mod

        monkeypatch.setattr(onboarding, "_get_existing_nicknames", lambda: {"追风"})

        mock_llm = MagicMock()
        call_count = 0

        async def fake_tracked_ainvoke(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_result = MagicMock()
            if call_count == 1:
                mock_result.content = "追风"  # duplicate
            else:
                mock_result.content = "凌霄"  # unique
            return mock_result

        monkeypatch.setattr(base_mod, "make_llm", lambda _: mock_llm)
        monkeypatch.setattr(base_mod, "tracked_ainvoke", fake_tracked_ainvoke)

        nickname = await onboarding.generate_nickname("Dev", "Engineer")
        assert nickname == "凌霄"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_returns_empty_after_max_retries(self, monkeypatch):
        from onemancompany.agents import onboarding, base as base_mod

        monkeypatch.setattr(onboarding, "_get_existing_nicknames", lambda: {"追风"})

        mock_llm = MagicMock()
        mock_result = MagicMock()
        mock_result.content = "追风"  # always duplicate

        async def fake_tracked_ainvoke(*args, **kwargs):
            return mock_result

        monkeypatch.setattr(base_mod, "make_llm", lambda _: mock_llm)
        monkeypatch.setattr(base_mod, "tracked_ainvoke", fake_tracked_ainvoke)

        nickname = await onboarding.generate_nickname("Dev", "Engineer")
        assert nickname == ""

    @pytest.mark.asyncio
    async def test_extracts_chinese_from_noisy_output(self, monkeypatch):
        from onemancompany.agents import onboarding, base as base_mod

        monkeypatch.setattr(onboarding, "_get_existing_nicknames", lambda: set())

        mock_llm = MagicMock()
        mock_result = MagicMock()
        mock_result.content = "The nickname is: 凌霄 (ling xiao)"  # noisy LLM output

        async def fake_tracked_ainvoke(*args, **kwargs):
            return mock_result

        monkeypatch.setattr(base_mod, "make_llm", lambda _: mock_llm)
        monkeypatch.setattr(base_mod, "tracked_ainvoke", fake_tracked_ainvoke)

        nickname = await onboarding.generate_nickname("Dev", "Engineer")
        assert nickname == "凌霄"


# ---------------------------------------------------------------------------
# copy_talent_assets
# ---------------------------------------------------------------------------

class TestCopyTalentAssets:
    def test_copies_skills_and_tools(self, tmp_path, monkeypatch):
        from onemancompany.agents import onboarding

        # Setup talent directory
        talent_dir = tmp_path / "talents" / "coding"
        (talent_dir / "skills").mkdir(parents=True)
        (talent_dir / "tools").mkdir(parents=True)
        (talent_dir / "skills" / "python.md").write_text("# Python skill")
        (talent_dir / "tools" / "manifest.yaml").write_text("builtin_tools: []\ncustom_tools: []")

        monkeypatch.setattr(onboarding, "TALENTS_DIR", tmp_path / "talents")

        # Setup employee directory
        emp_dir = tmp_path / "emp"
        emp_dir.mkdir()

        onboarding.copy_talent_assets("coding", emp_dir)

        assert (emp_dir / "skills" / "python.md").exists()
        assert (emp_dir / "skills" / "python.md").read_text() == "# Python skill"
        assert (emp_dir / "tools" / "manifest.yaml").exists()

    def test_skips_existing_files(self, tmp_path, monkeypatch):
        from onemancompany.agents import onboarding

        talent_dir = tmp_path / "talents" / "coding"
        (talent_dir / "skills").mkdir(parents=True)
        (talent_dir / "skills" / "python.md").write_text("NEW content")

        monkeypatch.setattr(onboarding, "TALENTS_DIR", tmp_path / "talents")

        emp_dir = tmp_path / "emp"
        (emp_dir / "skills").mkdir(parents=True)
        (emp_dir / "skills" / "python.md").write_text("EXISTING content")

        onboarding.copy_talent_assets("coding", emp_dir)

        # Should NOT overwrite existing
        assert (emp_dir / "skills" / "python.md").read_text() == "EXISTING content"

    def test_nonexistent_talent_no_error(self, tmp_path, monkeypatch):
        from onemancompany.agents import onboarding

        monkeypatch.setattr(onboarding, "TALENTS_DIR", tmp_path / "talents")
        emp_dir = tmp_path / "emp"
        emp_dir.mkdir()

        # Should not raise
        onboarding.copy_talent_assets("nonexistent", emp_dir)

    def test_only_copies_md_files_for_skills(self, tmp_path, monkeypatch):
        from onemancompany.agents import onboarding

        talent_dir = tmp_path / "talents" / "coding"
        (talent_dir / "skills").mkdir(parents=True)
        (talent_dir / "skills" / "python.md").write_text("# Python")
        (talent_dir / "skills" / "notes.txt").write_text("not a skill")

        monkeypatch.setattr(onboarding, "TALENTS_DIR", tmp_path / "talents")

        emp_dir = tmp_path / "emp"
        emp_dir.mkdir()

        onboarding.copy_talent_assets("coding", emp_dir)

        assert (emp_dir / "skills" / "python.md").exists()
        assert not (emp_dir / "skills" / "notes.txt").exists()


# ---------------------------------------------------------------------------
# execute_hire
# ---------------------------------------------------------------------------

class TestExecuteHire:
    @pytest.mark.asyncio
    async def test_basic_hire_flow(self, tmp_path, monkeypatch):
        """Test the core hire flow: employee creation, profile save, layout, event."""
        from onemancompany.agents import onboarding
        from onemancompany.core import config as config_mod
        from onemancompany.core import state as state_mod

        # Fresh state
        cs = _make_company_state()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(onboarding, "company_state", cs)

        # Redirect file system to tmp_path
        emp_base = tmp_path / "employees"
        emp_base.mkdir()
        monkeypatch.setattr(config_mod, "EMPLOYEES_DIR", emp_base)
        monkeypatch.setattr(config_mod, "PROFILE_TEMPLATE", tmp_path / "nonexistent_template.yaml")
        monkeypatch.setattr(onboarding, "TALENTS_DIR", tmp_path / "talents")

        # Mock settings for connection.json
        mock_settings = MagicMock()
        mock_settings.host = "localhost"
        mock_settings.port = 8000
        monkeypatch.setattr(onboarding, "settings", mock_settings)

        # Mock layout functions
        monkeypatch.setattr(
            "onemancompany.agents.onboarding.get_next_desk_for_department",
            lambda cs, dept: (5, 3),
        )
        monkeypatch.setattr(
            "onemancompany.agents.onboarding.compute_layout",
            lambda cs: {},
        )
        monkeypatch.setattr(
            "onemancompany.agents.onboarding.persist_all_desk_positions",
            lambda cs: None,
        )

        # Mock event bus
        published_events = []

        async def mock_publish(event):
            published_events.append(event)

        monkeypatch.setattr(
            "onemancompany.agents.onboarding.event_bus.publish",
            mock_publish,
        )

        # Mock agent registration
        monkeypatch.setattr(
            "onemancompany.core.agent_loop.get_agent_loop",
            lambda eid: None,
        )
        mock_register = AsyncMock()
        monkeypatch.setattr(
            "onemancompany.core.agent_loop.register_and_start_agent",
            mock_register,
        )

        # Mock model cost
        monkeypatch.setattr(
            "onemancompany.core.model_costs.compute_salary",
            lambda model: 5.0,
        )

        emp = await onboarding.execute_hire(
            name="Test Developer",
            nickname="追风",
            role="Engineer",
            skills=["python", "typescript"],
            llm_model="test-model",
            sprite="employee_blue",
        )

        # Verify employee created
        assert emp.name == "Test Developer"
        assert emp.nickname == "追风"
        assert emp.role == "Engineer"
        assert emp.level == 1
        assert emp.department == "Engineering"
        assert emp.desk_position == (5, 3)
        assert "python" in emp.skills

        # Verify in company_state
        assert emp.id in cs.employees
        assert cs.employees[emp.id] is emp

        # Verify profile saved
        emp_dir = emp_base / emp.id
        assert emp_dir.exists()
        assert (emp_dir / "skills").is_dir()

        # Verify work_principles.md created
        wp_path = emp_dir / "work_principles.md"
        assert wp_path.exists()
        content = wp_path.read_text()
        assert "Test Developer" in content
        assert "追风" in content

        # Verify skill stubs created
        assert (emp_dir / "skills" / "python.md").exists()
        assert (emp_dir / "skills" / "typescript.md").exists()

        # Verify event published
        assert len(published_events) == 1
        assert published_events[0].type == "employee_hired"

        # Verify activity log
        assert len(cs.activity_log) == 1
        assert cs.activity_log[0]["type"] == "employee_hired"

    @pytest.mark.asyncio
    async def test_hire_remote_employee(self, tmp_path, monkeypatch):
        """Remote employees get desk_position (-1,-1) and connection.json."""
        from onemancompany.agents import onboarding
        from onemancompany.core import config as config_mod
        from onemancompany.core import state as state_mod

        cs = _make_company_state()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(onboarding, "company_state", cs)

        emp_base = tmp_path / "employees"
        emp_base.mkdir()
        monkeypatch.setattr(config_mod, "EMPLOYEES_DIR", emp_base)
        monkeypatch.setattr(config_mod, "PROFILE_TEMPLATE", tmp_path / "no_template.yaml")
        monkeypatch.setattr(onboarding, "TALENTS_DIR", tmp_path / "talents")

        mock_settings = MagicMock()
        mock_settings.host = "localhost"
        mock_settings.port = 8000
        monkeypatch.setattr(onboarding, "settings", mock_settings)

        monkeypatch.setattr("onemancompany.agents.onboarding.compute_layout", lambda cs: {})
        monkeypatch.setattr("onemancompany.agents.onboarding.persist_all_desk_positions", lambda cs: None)
        monkeypatch.setattr("onemancompany.agents.onboarding.event_bus.publish", AsyncMock())
        monkeypatch.setattr("onemancompany.core.model_costs.compute_salary", lambda m: 3.0)

        emp = await onboarding.execute_hire(
            name="Remote Worker",
            nickname="飞鸿",
            role="Engineer",
            skills=["python"],
            remote=True,
            talent_id="remote_talent",
        )

        assert emp.remote is True
        assert emp.desk_position == (-1, -1)

        # connection.json should be created for remote
        conn_path = emp_base / emp.id / "connection.json"
        assert conn_path.exists()
        conn = json.loads(conn_path.read_text())
        assert conn["employee_id"] == emp.id
        assert conn["talent_id"] == "remote_talent"

    @pytest.mark.asyncio
    async def test_hire_self_hosted(self, tmp_path, monkeypatch):
        """Self-hosted employees get connection.json and register_self_hosted called."""
        from onemancompany.agents import onboarding
        from onemancompany.core import config as config_mod
        from onemancompany.core import state as state_mod

        cs = _make_company_state()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(onboarding, "company_state", cs)

        emp_base = tmp_path / "employees"
        emp_base.mkdir()
        monkeypatch.setattr(config_mod, "EMPLOYEES_DIR", emp_base)
        monkeypatch.setattr(config_mod, "PROFILE_TEMPLATE", tmp_path / "no_template.yaml")
        monkeypatch.setattr(onboarding, "TALENTS_DIR", tmp_path / "talents")

        mock_settings = MagicMock()
        mock_settings.host = "localhost"
        mock_settings.port = 8000
        monkeypatch.setattr(onboarding, "settings", mock_settings)

        monkeypatch.setattr("onemancompany.agents.onboarding.get_next_desk_for_department", lambda cs, d: (3, 3))
        monkeypatch.setattr("onemancompany.agents.onboarding.compute_layout", lambda cs: {})
        monkeypatch.setattr("onemancompany.agents.onboarding.persist_all_desk_positions", lambda cs: None)
        monkeypatch.setattr("onemancompany.agents.onboarding.event_bus.publish", AsyncMock())
        monkeypatch.setattr("onemancompany.core.model_costs.compute_salary", lambda m: 0.0)

        mock_self_hosted = MagicMock()
        monkeypatch.setattr("onemancompany.core.agent_loop.get_agent_loop", lambda eid: None)
        monkeypatch.setattr("onemancompany.core.agent_loop.register_self_hosted", mock_self_hosted)

        emp = await onboarding.execute_hire(
            name="Claude Worker",
            nickname="千机",
            role="Engineer",
            skills=["coding"],
            hosting="self",
            auth_method="oauth",
            api_provider="anthropic",
            talent_id="claude_code_onsite",
        )

        # connection.json for self-hosted
        conn_path = emp_base / emp.id / "connection.json"
        assert conn_path.exists()

        # register_self_hosted should be called (not register_and_start_agent)
        mock_self_hosted.assert_called_once_with(emp.id)

    @pytest.mark.asyncio
    async def test_hire_department_assignment(self, tmp_path, monkeypatch):
        """Each role should be auto-assigned to correct department."""
        from onemancompany.agents import onboarding
        from onemancompany.core import config as config_mod
        from onemancompany.core import state as state_mod

        role_dept_expected = [
            ("Engineer", "Engineering"),
            ("Designer", "Design"),
            ("Analyst", "Analytics"),
            ("Marketing", "Marketing"),
            ("DevOps", "Engineering"),
            ("QA", "Engineering"),
            ("UnknownRole", "General"),
        ]

        for role, expected_dept in role_dept_expected:
            cs = _make_company_state()
            monkeypatch.setattr(state_mod, "company_state", cs)
            monkeypatch.setattr(onboarding, "company_state", cs)

            emp_base = tmp_path / f"employees_{role}"
            emp_base.mkdir(exist_ok=True)
            monkeypatch.setattr(config_mod, "EMPLOYEES_DIR", emp_base)
            monkeypatch.setattr(config_mod, "PROFILE_TEMPLATE", tmp_path / "no_template.yaml")
            monkeypatch.setattr(onboarding, "TALENTS_DIR", tmp_path / "talents")
            monkeypatch.setattr(onboarding, "settings", MagicMock(host="localhost", port=8000))
            monkeypatch.setattr("onemancompany.agents.onboarding.get_next_desk_for_department", lambda cs, d: (1, 1))
            monkeypatch.setattr("onemancompany.agents.onboarding.compute_layout", lambda cs: {})
            monkeypatch.setattr("onemancompany.agents.onboarding.persist_all_desk_positions", lambda cs: None)
            monkeypatch.setattr("onemancompany.agents.onboarding.event_bus.publish", AsyncMock())
            monkeypatch.setattr("onemancompany.core.model_costs.compute_salary", lambda m: 0.0)
            monkeypatch.setattr("onemancompany.core.agent_loop.get_agent_loop", lambda eid: None)
            monkeypatch.setattr("onemancompany.core.agent_loop.register_and_start_agent", AsyncMock())

            emp = await onboarding.execute_hire(
                name=f"Test {role}", nickname="测试", role=role, skills=[],
            )
            assert emp.department == expected_dept, f"Role {role} expected {expected_dept}, got {emp.department}"

    @pytest.mark.asyncio
    async def test_hire_auto_generates_nickname_when_empty(self, tmp_path, monkeypatch):
        """When nickname is empty, execute_hire calls generate_nickname."""
        from onemancompany.agents import onboarding
        from onemancompany.core import config as config_mod
        from onemancompany.core import state as state_mod

        cs = _make_company_state()
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(onboarding, "company_state", cs)

        emp_base = tmp_path / "employees"
        emp_base.mkdir()
        monkeypatch.setattr(config_mod, "EMPLOYEES_DIR", emp_base)
        monkeypatch.setattr(config_mod, "PROFILE_TEMPLATE", tmp_path / "no_template.yaml")
        monkeypatch.setattr(onboarding, "TALENTS_DIR", tmp_path / "talents")
        monkeypatch.setattr(onboarding, "settings", MagicMock(host="localhost", port=8000))
        monkeypatch.setattr("onemancompany.agents.onboarding.get_next_desk_for_department", lambda cs, d: (1, 1))
        monkeypatch.setattr("onemancompany.agents.onboarding.compute_layout", lambda cs: {})
        monkeypatch.setattr("onemancompany.agents.onboarding.persist_all_desk_positions", lambda cs: None)
        monkeypatch.setattr("onemancompany.agents.onboarding.event_bus.publish", AsyncMock())
        monkeypatch.setattr("onemancompany.core.model_costs.compute_salary", lambda m: 0.0)
        monkeypatch.setattr("onemancompany.core.agent_loop.get_agent_loop", lambda eid: None)
        monkeypatch.setattr("onemancompany.core.agent_loop.register_and_start_agent", AsyncMock())

        # Mock generate_nickname
        gen_called = False

        async def mock_gen(name, role, is_founding=False):
            nonlocal gen_called
            gen_called = True
            return "星辰"

        monkeypatch.setattr(onboarding, "generate_nickname", mock_gen)

        emp = await onboarding.execute_hire(
            name="Auto Nick", nickname="", role="Engineer", skills=[],
        )
        assert gen_called
        assert emp.nickname == "星辰"

    @pytest.mark.asyncio
    async def test_hire_employee_number_increments(self, tmp_path, monkeypatch):
        """Each hire should get a unique, incrementing employee number."""
        from onemancompany.agents import onboarding
        from onemancompany.core import config as config_mod
        from onemancompany.core import state as state_mod

        cs = _make_company_state()  # starts at 100
        monkeypatch.setattr(state_mod, "company_state", cs)
        monkeypatch.setattr(onboarding, "company_state", cs)

        emp_base = tmp_path / "employees"
        emp_base.mkdir()
        monkeypatch.setattr(config_mod, "EMPLOYEES_DIR", emp_base)
        monkeypatch.setattr(config_mod, "PROFILE_TEMPLATE", tmp_path / "no_template.yaml")
        monkeypatch.setattr(onboarding, "TALENTS_DIR", tmp_path / "talents")
        monkeypatch.setattr(onboarding, "settings", MagicMock(host="localhost", port=8000))
        monkeypatch.setattr("onemancompany.agents.onboarding.get_next_desk_for_department", lambda cs, d: (1, 1))
        monkeypatch.setattr("onemancompany.agents.onboarding.compute_layout", lambda cs: {})
        monkeypatch.setattr("onemancompany.agents.onboarding.persist_all_desk_positions", lambda cs: None)
        monkeypatch.setattr("onemancompany.agents.onboarding.event_bus.publish", AsyncMock())
        monkeypatch.setattr("onemancompany.core.model_costs.compute_salary", lambda m: 0.0)
        monkeypatch.setattr("onemancompany.core.agent_loop.get_agent_loop", lambda eid: None)
        monkeypatch.setattr("onemancompany.core.agent_loop.register_and_start_agent", AsyncMock())

        emp1 = await onboarding.execute_hire(name="A", nickname="甲", role="Engineer", skills=[])
        emp2 = await onboarding.execute_hire(name="B", nickname="乙", role="Engineer", skills=[])

        assert emp1.id == "00100"
        assert emp2.id == "00101"
        assert int(emp2.employee_number) == int(emp1.employee_number) + 1
