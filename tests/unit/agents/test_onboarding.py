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

        # Setup talent directory with folder-based skill
        talent_dir = tmp_path / "talents" / "coding"
        (talent_dir / "skills" / "python").mkdir(parents=True)
        (talent_dir / "tools").mkdir(parents=True)
        (talent_dir / "skills" / "python" / "SKILL.md").write_text("# Python skill")
        (talent_dir / "tools" / "manifest.yaml").write_text("builtin_tools: []\ncustom_tools: []")

        # Setup employee directory
        emp_dir = tmp_path / "emp"
        emp_dir.mkdir()

        onboarding.copy_talent_assets(talent_dir, emp_dir)

        assert (emp_dir / "skills" / "python" / "SKILL.md").exists()
        assert (emp_dir / "skills" / "python" / "SKILL.md").read_text() == "# Python skill"
        assert (emp_dir / "tools" / "manifest.yaml").exists()

    def test_skips_existing_skills(self, tmp_path, monkeypatch):
        from onemancompany.agents import onboarding

        talent_dir = tmp_path / "talents" / "coding"
        (talent_dir / "skills" / "python").mkdir(parents=True)
        (talent_dir / "skills" / "python" / "SKILL.md").write_text("NEW content")

        emp_dir = tmp_path / "emp"
        (emp_dir / "skills" / "python").mkdir(parents=True)
        (emp_dir / "skills" / "python" / "SKILL.md").write_text("EXISTING content")

        onboarding.copy_talent_assets(talent_dir, emp_dir)

        # Should NOT overwrite existing
        assert (emp_dir / "skills" / "python" / "SKILL.md").read_text() == "EXISTING content"

    def test_nonexistent_talent_no_error(self, tmp_path, monkeypatch):
        from onemancompany.agents import onboarding

        nonexistent = tmp_path / "talents" / "nonexistent"
        emp_dir = tmp_path / "emp"
        emp_dir.mkdir()

        # Should not raise
        onboarding.copy_talent_assets(nonexistent, emp_dir)

    def test_only_copies_skill_folders(self, tmp_path, monkeypatch):
        from onemancompany.agents import onboarding

        talent_dir = tmp_path / "talents" / "coding"
        (talent_dir / "skills" / "python").mkdir(parents=True)
        (talent_dir / "skills" / "python" / "SKILL.md").write_text("# Python")
        (talent_dir / "skills" / "notes.txt").write_text("not a skill")

        emp_dir = tmp_path / "emp"
        emp_dir.mkdir()

        onboarding.copy_talent_assets(talent_dir, emp_dir)

        assert (emp_dir / "skills" / "python" / "SKILL.md").exists()
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
        monkeypatch.setattr(onboarding, "_TALENTS_CLONE_DIR", tmp_path / "talents")

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

        # Mock store.append_activity
        monkeypatch.setattr(onboarding._store, "append_activity", AsyncMock())

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

        # After Task 10 refactoring, employee data is persisted to disk via
        # store.save_employee (not in-memory cs.employees). The conftest bridge
        # swallows disk writes in test isolation mode, so we verify the returned
        # Employee object and the directory structure created by ensure_employee_dir.

        # Verify profile saved
        emp_dir = emp_base / emp.id
        assert emp_dir.exists()
        assert (emp_dir / "skills").is_dir()

        # Verify work-principles skill created (autoloaded)
        wp_path = emp_dir / "skills" / "work-principles" / "SKILL.md"
        assert wp_path.exists()
        content = wp_path.read_text()
        assert "Test Developer" in content
        assert "追风" in content
        assert "autoload: true" in content

        # Verify skill stubs created (folder-based)
        assert (emp_dir / "skills" / "python" / "SKILL.md").exists()
        assert (emp_dir / "skills" / "typescript" / "SKILL.md").exists()

        # Verify event published
        assert len(published_events) == 1
        assert published_events[0].type == "employee_hired"

        # Verify activity log written via store (not in-memory cs.activity_log)
        # append_activity was called by onboarding via _store
        onboarding._store.append_activity.assert_awaited()
        call_entry = onboarding._store.append_activity.call_args[0][0]
        assert call_entry["type"] == "employee_hired"

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
        monkeypatch.setattr(onboarding, "_TALENTS_CLONE_DIR", tmp_path / "talents")

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
        monkeypatch.setattr(onboarding, "_TALENTS_CLONE_DIR", tmp_path / "talents")

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
            monkeypatch.setattr(onboarding, "_TALENTS_CLONE_DIR", tmp_path / "talents")
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
        monkeypatch.setattr(onboarding, "_TALENTS_CLONE_DIR", tmp_path / "talents")
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
        monkeypatch.setattr(onboarding, "_TALENTS_CLONE_DIR", tmp_path / "talents")
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


# ---------------------------------------------------------------------------
# generate_nickname — additional edge cases
# ---------------------------------------------------------------------------

class TestGenerateNicknameEdgeCases:
    @pytest.mark.asyncio
    async def test_fewer_chinese_chars_than_needed(self, monkeypatch):
        """When LLM returns fewer Chinese chars than required, use what we get."""
        from onemancompany.agents import onboarding, base as base_mod

        monkeypatch.setattr(onboarding, "_get_existing_nicknames", lambda: set())

        mock_llm = MagicMock()
        call_count = 0

        async def fake_tracked_ainvoke(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_result = MagicMock()
            # Return only 1 Chinese char when 2 are needed
            if call_count == 1:
                mock_result.content = "风"
            else:
                mock_result.content = "追风"
            return mock_result

        monkeypatch.setattr(base_mod, "make_llm", lambda _: mock_llm)
        monkeypatch.setattr(base_mod, "tracked_ainvoke", fake_tracked_ainvoke)

        nickname = await onboarding.generate_nickname("Test", "Engineer", is_founding=False)
        # First attempt returns 1 char (fewer than 2, but non-empty) — accepted
        assert nickname == "风"

    @pytest.mark.asyncio
    async def test_no_chinese_chars_at_all_retries(self, monkeypatch):
        """LLM returns no Chinese chars — should retry and eventually return empty."""
        from onemancompany.agents import onboarding, base as base_mod

        monkeypatch.setattr(onboarding, "_get_existing_nicknames", lambda: set())

        mock_llm = MagicMock()

        async def fake_tracked_ainvoke(*args, **kwargs):
            mock_result = MagicMock()
            mock_result.content = "no chinese here at all"
            return mock_result

        monkeypatch.setattr(base_mod, "make_llm", lambda _: mock_llm)
        monkeypatch.setattr(base_mod, "tracked_ainvoke", fake_tracked_ainvoke)

        nickname = await onboarding.generate_nickname("Test", "Engineer")
        assert nickname == ""

    @pytest.mark.asyncio
    async def test_avoid_clause_with_existing_nicknames(self, monkeypatch):
        """When there are existing nicknames, the prompt should mention them."""
        from onemancompany.agents import onboarding, base as base_mod

        existing = {"追风", "凌霄", "破军"}
        monkeypatch.setattr(onboarding, "_get_existing_nicknames", lambda: existing)

        mock_llm = MagicMock()
        mock_result = MagicMock()
        mock_result.content = "天机"

        captured_messages = []
        async def fake_tracked_ainvoke(llm, messages, **kwargs):
            captured_messages.extend(messages)
            return mock_result

        monkeypatch.setattr(base_mod, "make_llm", lambda _: mock_llm)
        monkeypatch.setattr(base_mod, "tracked_ainvoke", fake_tracked_ainvoke)

        nickname = await onboarding.generate_nickname("Test", "Engineer")
        assert nickname == "天机"
        # The prompt should contain at least one of the existing nicknames
        prompt_text = captured_messages[1].content
        assert any(n in prompt_text for n in existing)


# ---------------------------------------------------------------------------
# _update_tool_allowed_users / register_tool_user / unregister_tool_user
# ---------------------------------------------------------------------------

class TestToolUserRegistration:
    def test_add_user_to_tool(self, tmp_path, monkeypatch):
        from onemancompany.agents import onboarding

        # Create tool.yaml
        tool_dir = tmp_path / "tools" / "my_tool"
        tool_dir.mkdir(parents=True)
        tool_yaml = tool_dir / "tool.yaml"
        tool_yaml.write_text("id: my_tool\nallowed_users: []\n")

        monkeypatch.setattr(onboarding, "TOOLS_DIR", tmp_path / "tools")

        onboarding.register_tool_user("my_tool", "00010")

        import yaml
        data = yaml.safe_load(tool_yaml.read_text())
        assert "00010" in data["allowed_users"]

    def test_remove_user_from_tool(self, tmp_path, monkeypatch):
        from onemancompany.agents import onboarding

        tool_dir = tmp_path / "tools" / "my_tool"
        tool_dir.mkdir(parents=True)
        tool_yaml = tool_dir / "tool.yaml"
        tool_yaml.write_text("id: my_tool\nallowed_users:\n- '00010'\n- '00011'\n")

        monkeypatch.setattr(onboarding, "TOOLS_DIR", tmp_path / "tools")

        onboarding.unregister_tool_user("my_tool", "00010")

        import yaml
        data = yaml.safe_load(tool_yaml.read_text())
        assert "00010" not in data["allowed_users"]
        assert "00011" in data["allowed_users"]

    def test_add_user_idempotent(self, tmp_path, monkeypatch):
        from onemancompany.agents import onboarding

        tool_dir = tmp_path / "tools" / "my_tool"
        tool_dir.mkdir(parents=True)
        tool_yaml = tool_dir / "tool.yaml"
        tool_yaml.write_text("id: my_tool\nallowed_users:\n- '00010'\n")

        monkeypatch.setattr(onboarding, "TOOLS_DIR", tmp_path / "tools")

        onboarding.register_tool_user("my_tool", "00010")

        import yaml
        data = yaml.safe_load(tool_yaml.read_text())
        assert data["allowed_users"].count("00010") == 1

    def test_remove_nonexistent_user_no_error(self, tmp_path, monkeypatch):
        from onemancompany.agents import onboarding

        tool_dir = tmp_path / "tools" / "my_tool"
        tool_dir.mkdir(parents=True)
        tool_yaml = tool_dir / "tool.yaml"
        tool_yaml.write_text("id: my_tool\nallowed_users: []\n")

        monkeypatch.setattr(onboarding, "TOOLS_DIR", tmp_path / "tools")

        # Should not raise
        onboarding.unregister_tool_user("my_tool", "99999")

    def test_nonexistent_tool_yaml_no_error(self, tmp_path, monkeypatch):
        from onemancompany.agents import onboarding

        monkeypatch.setattr(onboarding, "TOOLS_DIR", tmp_path / "tools")

        # tool.yaml doesn't exist — should return silently
        onboarding.register_tool_user("nonexistent_tool", "00010")
        onboarding.unregister_tool_user("nonexistent_tool", "00010")


# ---------------------------------------------------------------------------
# _validate_tool_module
# ---------------------------------------------------------------------------

class TestValidateToolModule:
    def test_valid_module_with_base_tool(self, tmp_path):
        from onemancompany.agents.onboarding import _validate_tool_module

        # Create a Python file with a BaseTool instance
        py_file = tmp_path / "my_tool.py"
        py_file.write_text(
            "from langchain_core.tools import tool\n"
            "@tool\n"
            "def my_tool(x: str) -> str:\n"
            "    '''A test tool.'''\n"
            "    return x\n"
        )

        assert _validate_tool_module(py_file) is True

    def test_module_without_base_tool(self, tmp_path):
        from onemancompany.agents.onboarding import _validate_tool_module

        py_file = tmp_path / "no_tool.py"
        py_file.write_text("x = 42\n")

        assert _validate_tool_module(py_file) is False

    def test_module_with_import_error(self, tmp_path):
        from onemancompany.agents.onboarding import _validate_tool_module

        py_file = tmp_path / "bad_module.py"
        py_file.write_text("import nonexistent_module_xyz\n")

        assert _validate_tool_module(py_file) is False

    def test_nonexistent_module(self, tmp_path):
        from onemancompany.agents.onboarding import _validate_tool_module

        py_file = tmp_path / "does_not_exist.py"
        assert _validate_tool_module(py_file) is False


# ---------------------------------------------------------------------------
# install_talent_functions
# ---------------------------------------------------------------------------

class TestInstallTalentFunctions:
    def test_no_manifest_returns_empty(self, tmp_path, monkeypatch):
        from onemancompany.agents import onboarding

        talent_dir = tmp_path / "talents" / "test_talent"
        talent_dir.mkdir(parents=True)
        monkeypatch.setattr(onboarding, "_TALENTS_CLONE_DIR", tmp_path / "talents")
        monkeypatch.setattr(onboarding, "TOOLS_DIR", tmp_path / "tools")

        result = onboarding.install_talent_functions(tmp_path / "talents" / "test_talent", tmp_path / "emp", "00010")
        assert result == []

    def test_empty_functions_returns_empty(self, tmp_path, monkeypatch):
        from onemancompany.agents import onboarding

        talent_dir = tmp_path / "talents" / "test_talent" / "functions"
        talent_dir.mkdir(parents=True)
        manifest = talent_dir / "manifest.yaml"
        manifest.write_text("functions: []\n")

        monkeypatch.setattr(onboarding, "_TALENTS_CLONE_DIR", tmp_path / "talents")
        monkeypatch.setattr(onboarding, "TOOLS_DIR", tmp_path / "tools")

        result = onboarding.install_talent_functions(tmp_path / "talents" / "test_talent", tmp_path / "emp", "00010")
        assert result == []

    def test_function_name_empty_skipped(self, tmp_path, monkeypatch):
        from onemancompany.agents import onboarding

        talent_dir = tmp_path / "talents" / "test_talent" / "functions"
        talent_dir.mkdir(parents=True)
        manifest = talent_dir / "manifest.yaml"
        manifest.write_text("functions:\n  - name: ''\n    description: 'no name'\n")

        monkeypatch.setattr(onboarding, "_TALENTS_CLONE_DIR", tmp_path / "talents")
        monkeypatch.setattr(onboarding, "TOOLS_DIR", tmp_path / "tools")

        result = onboarding.install_talent_functions(tmp_path / "talents" / "test_talent", tmp_path / "emp", "00010")
        assert result == []

    def test_missing_py_file_skipped(self, tmp_path, monkeypatch):
        from onemancompany.agents import onboarding

        talent_dir = tmp_path / "talents" / "test_talent" / "functions"
        talent_dir.mkdir(parents=True)
        manifest = talent_dir / "manifest.yaml"
        manifest.write_text("functions:\n  - name: missing_tool\n    description: 'tool'\n")
        # .py file doesn't exist

        monkeypatch.setattr(onboarding, "_TALENTS_CLONE_DIR", tmp_path / "talents")
        monkeypatch.setattr(onboarding, "TOOLS_DIR", tmp_path / "tools")

        result = onboarding.install_talent_functions(tmp_path / "talents" / "test_talent", tmp_path / "emp", "00010")
        assert result == []

    def test_installs_valid_function(self, tmp_path, monkeypatch):
        from onemancompany.agents import onboarding

        talent_dir = tmp_path / "talents" / "test_talent" / "functions"
        talent_dir.mkdir(parents=True)
        manifest = talent_dir / "manifest.yaml"
        manifest.write_text(
            "functions:\n"
            "  - name: my_func\n"
            "    description: 'A function'\n"
            "    scope: personal\n"
        )
        # Create a valid Python tool file
        py_file = talent_dir / "my_func.py"
        py_file.write_text(
            "from langchain_core.tools import tool\n"
            "@tool\n"
            "def my_func(x: str) -> str:\n"
            "    '''A test tool.'''\n"
            "    return x\n"
        )

        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        monkeypatch.setattr(onboarding, "_TALENTS_CLONE_DIR", tmp_path / "talents")
        monkeypatch.setattr(onboarding, "TOOLS_DIR", tools_dir)

        result = onboarding.install_talent_functions(tmp_path / "talents" / "test_talent", tmp_path / "emp", "00010")
        assert "my_func" in result

        # tool.yaml should be created in central registry
        tool_yaml = tools_dir / "my_func" / "tool.yaml"
        assert tool_yaml.exists()
        import yaml
        data = yaml.safe_load(tool_yaml.read_text())
        assert data["name"] == "my_func"
        assert "00010" in data["allowed_users"]

    def test_existing_tool_dir_registers_user_only(self, tmp_path, monkeypatch):
        from onemancompany.agents import onboarding

        talent_dir = tmp_path / "talents" / "test_talent" / "functions"
        talent_dir.mkdir(parents=True)
        manifest = talent_dir / "manifest.yaml"
        manifest.write_text(
            "functions:\n"
            "  - name: shared_tool\n"
            "    description: 'shared'\n"
        )
        py_file = talent_dir / "shared_tool.py"
        py_file.write_text(
            "from langchain_core.tools import tool\n"
            "@tool\n"
            "def shared_tool(x: str) -> str:\n"
            "    '''A test tool.'''\n"
            "    return x\n"
        )

        tools_dir = tmp_path / "tools"
        tool_dir = tools_dir / "shared_tool"
        tool_dir.mkdir(parents=True)
        # Pre-existing tool.yaml with another user
        (tool_dir / "tool.yaml").write_text(
            "id: shared_tool\nname: shared_tool\nallowed_users:\n- '00001'\n"
        )

        monkeypatch.setattr(onboarding, "_TALENTS_CLONE_DIR", tmp_path / "talents")
        monkeypatch.setattr(onboarding, "TOOLS_DIR", tools_dir)

        result = onboarding.install_talent_functions(tmp_path / "talents" / "test_talent", tmp_path / "emp", "00010")
        assert "shared_tool" in result

        # Both users should be registered
        import yaml
        data = yaml.safe_load((tool_dir / "tool.yaml").read_text())
        assert "00001" in data["allowed_users"]
        assert "00010" in data["allowed_users"]

    def test_invalid_tool_module_skipped(self, tmp_path, monkeypatch):
        """Function whose .py file has no BaseTool should be skipped."""
        from onemancompany.agents import onboarding

        talent_dir = tmp_path / "talents" / "test_talent" / "functions"
        talent_dir.mkdir(parents=True)
        manifest = talent_dir / "manifest.yaml"
        manifest.write_text(
            "functions:\n"
            "  - name: no_tool_func\n"
            "    description: 'has no BaseTool'\n"
        )
        # .py file exists but contains no BaseTool instance
        py_file = talent_dir / "no_tool_func.py"
        py_file.write_text("x = 42\n")

        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        monkeypatch.setattr(onboarding, "_TALENTS_CLONE_DIR", tmp_path / "talents")
        monkeypatch.setattr(onboarding, "TOOLS_DIR", tools_dir)

        result = onboarding.install_talent_functions(tmp_path / "talents" / "test_talent", tmp_path / "emp", "00010")
        assert result == []
        # Tool dir should NOT be created
        assert not (tools_dir / "no_tool_func").exists()

    def test_company_scope_installs_and_registers(self, tmp_path, monkeypatch):
        """Company scope omits allowed_users initially but register_tool_user adds it."""
        from onemancompany.agents import onboarding

        talent_dir = tmp_path / "talents" / "test_talent" / "functions"
        talent_dir.mkdir(parents=True)
        manifest = talent_dir / "manifest.yaml"
        manifest.write_text(
            "functions:\n"
            "  - name: company_func\n"
            "    description: 'company wide'\n"
            "    scope: company\n"
        )
        py_file = talent_dir / "company_func.py"
        py_file.write_text(
            "from langchain_core.tools import tool\n"
            "@tool\n"
            "def company_func(x: str) -> str:\n"
            "    '''A test tool.'''\n"
            "    return x\n"
        )

        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        monkeypatch.setattr(onboarding, "_TALENTS_CLONE_DIR", tmp_path / "talents")
        monkeypatch.setattr(onboarding, "TOOLS_DIR", tools_dir)

        result = onboarding.install_talent_functions(tmp_path / "talents" / "test_talent", tmp_path / "emp", "00010")
        assert "company_func" in result

        import yaml
        data = yaml.safe_load((tools_dir / "company_func" / "tool.yaml").read_text())
        # register_tool_user is called after creation, so employee ends up in allowed_users
        assert "00010" in data["allowed_users"]
        assert data["type"] == "langchain_module"
        assert data["source_talent"] == "test_talent"


# ---------------------------------------------------------------------------
# install_talent_agent_config
# ---------------------------------------------------------------------------

class TestInstallTalentAgentConfig:
    def test_no_agent_dir_returns_none(self, tmp_path, monkeypatch):
        from onemancompany.agents import onboarding

        talent_dir = tmp_path / "talents" / "test_talent"
        talent_dir.mkdir(parents=True)
        monkeypatch.setattr(onboarding, "_TALENTS_CLONE_DIR", tmp_path / "talents")

        result = onboarding.install_talent_agent_config(tmp_path / "talents" / "test_talent", tmp_path / "emp", "00010")
        assert result is None

    def test_copies_agent_dir_and_returns_manifest(self, tmp_path, monkeypatch):
        from onemancompany.agents import onboarding

        talent_dir = tmp_path / "talents" / "test_talent"
        agent_dir = talent_dir / "agent"
        agent_dir.mkdir(parents=True)
        manifest = agent_dir / "manifest.yaml"
        manifest.write_text("runner:\n  module: my_runner\n  class: MyRunner\n")
        (agent_dir / "my_runner.py").write_text("# runner code\n")

        monkeypatch.setattr(onboarding, "_TALENTS_CLONE_DIR", tmp_path / "talents")

        emp_dir = tmp_path / "emp"
        emp_dir.mkdir()

        result = onboarding.install_talent_agent_config(tmp_path / "talents" / "test_talent", emp_dir, "00010")
        assert result is not None
        assert result["runner"]["module"] == "my_runner"
        # Agent dir should be copied
        assert (emp_dir / "agent" / "manifest.yaml").exists()
        assert (emp_dir / "agent" / "my_runner.py").exists()

    def test_overwrites_existing_agent_dir(self, tmp_path, monkeypatch):
        from onemancompany.agents import onboarding

        talent_dir = tmp_path / "talents" / "test_talent"
        agent_dir = talent_dir / "agent"
        agent_dir.mkdir(parents=True)
        (agent_dir / "manifest.yaml").write_text("runner: {}\n")

        monkeypatch.setattr(onboarding, "_TALENTS_CLONE_DIR", tmp_path / "talents")

        emp_dir = tmp_path / "emp"
        dst_agent = emp_dir / "agent"
        dst_agent.mkdir(parents=True)
        (dst_agent / "old_file.txt").write_text("old")

        onboarding.install_talent_agent_config(tmp_path / "talents" / "test_talent", emp_dir, "00010")

        # old_file.txt should be gone after overwrite
        assert not (dst_agent / "old_file.txt").exists()
        assert (dst_agent / "manifest.yaml").exists()

    def test_validates_hooks_module(self, tmp_path, monkeypatch):
        """Should validate hooks module and not raise even if hook function is missing."""
        from onemancompany.agents import onboarding

        talent_dir = tmp_path / "talents" / "test_talent"
        agent_dir = talent_dir / "agent"
        agent_dir.mkdir(parents=True)
        manifest = agent_dir / "manifest.yaml"
        manifest.write_text(
            "hooks:\n"
            "  module: my_hooks\n"
            "  pre_task: nonexistent_fn\n"
        )
        (agent_dir / "my_hooks.py").write_text("# hooks file with no functions\n")

        monkeypatch.setattr(onboarding, "_TALENTS_CLONE_DIR", tmp_path / "talents")

        emp_dir = tmp_path / "emp"
        emp_dir.mkdir()

        # Should not raise — just logs a warning
        result = onboarding.install_talent_agent_config(tmp_path / "talents" / "test_talent", emp_dir, "00010")
        assert result is not None


# ---------------------------------------------------------------------------
# _create_agent_runner
# ---------------------------------------------------------------------------

class TestCreateAgentRunner:
    def test_default_runner_when_no_manifest(self, tmp_path, monkeypatch):
        from onemancompany.agents import onboarding

        emp_dir = tmp_path / "emp"
        emp_dir.mkdir()

        # Mock EmployeeAgent at its source (imported inside _create_agent_runner)
        mock_employee_agent = MagicMock()
        with patch("onemancompany.agents.base.EmployeeAgent", return_value=mock_employee_agent):
            runner = onboarding._create_agent_runner("00010", emp_dir)

        assert runner is mock_employee_agent

    def test_custom_runner_from_manifest(self, tmp_path, monkeypatch):
        from onemancompany.agents import onboarding
        from onemancompany.agents.base import BaseAgentRunner

        emp_dir = tmp_path / "emp"
        agent_dir = emp_dir / "agent"
        agent_dir.mkdir(parents=True)
        manifest = agent_dir / "manifest.yaml"
        manifest.write_text("runner:\n  module: custom_runner\n  class: CustomRunner\n")

        # Create a Python file with a valid runner class
        runner_py = agent_dir / "custom_runner.py"
        runner_py.write_text(
            "from onemancompany.agents.base import BaseAgentRunner\n"
            "class CustomRunner(BaseAgentRunner):\n"
            "    def __init__(self, employee_id):\n"
            "        self.employee_id = employee_id\n"
            "        self._custom = True\n"
        )

        runner = onboarding._create_agent_runner("00010", emp_dir)
        assert runner.employee_id == "00010"
        assert hasattr(runner, "_custom")

    def test_falls_back_on_invalid_runner(self, tmp_path, monkeypatch):
        from onemancompany.agents import onboarding

        emp_dir = tmp_path / "emp"
        agent_dir = emp_dir / "agent"
        agent_dir.mkdir(parents=True)
        manifest = agent_dir / "manifest.yaml"
        manifest.write_text("runner:\n  module: bad_runner\n  class: Missing\n")
        (agent_dir / "bad_runner.py").write_text("raise ImportError('broken')\n")

        mock_employee_agent = MagicMock()
        with patch("onemancompany.agents.base.EmployeeAgent", return_value=mock_employee_agent):
            runner = onboarding._create_agent_runner("00010", emp_dir)

        assert runner is mock_employee_agent


# ---------------------------------------------------------------------------
# _load_hooks_from_config
# ---------------------------------------------------------------------------

class TestLoadHooksFromConfig:
    def test_no_manifest_returns_empty(self, tmp_path):
        from onemancompany.agents.onboarding import _load_hooks_from_config

        emp_dir = tmp_path / "emp"
        emp_dir.mkdir()

        hooks = _load_hooks_from_config(emp_dir)
        assert hooks == {}

    def test_no_hooks_key_returns_empty(self, tmp_path):
        from onemancompany.agents.onboarding import _load_hooks_from_config

        emp_dir = tmp_path / "emp"
        agent_dir = emp_dir / "agent"
        agent_dir.mkdir(parents=True)
        (agent_dir / "manifest.yaml").write_text("runner:\n  module: x\n")

        hooks = _load_hooks_from_config(emp_dir)
        assert hooks == {}

    def test_no_module_name_returns_empty(self, tmp_path):
        from onemancompany.agents.onboarding import _load_hooks_from_config

        emp_dir = tmp_path / "emp"
        agent_dir = emp_dir / "agent"
        agent_dir.mkdir(parents=True)
        (agent_dir / "manifest.yaml").write_text("hooks:\n  pre_task: fn\n")

        hooks = _load_hooks_from_config(emp_dir)
        assert hooks == {}

    def test_missing_hooks_py_returns_empty(self, tmp_path):
        from onemancompany.agents.onboarding import _load_hooks_from_config

        emp_dir = tmp_path / "emp"
        agent_dir = emp_dir / "agent"
        agent_dir.mkdir(parents=True)
        (agent_dir / "manifest.yaml").write_text("hooks:\n  module: missing\n  pre_task: fn\n")

        hooks = _load_hooks_from_config(emp_dir)
        assert hooks == {}

    def test_loads_valid_hooks(self, tmp_path):
        from onemancompany.agents.onboarding import _load_hooks_from_config

        emp_dir = tmp_path / "emp"
        agent_dir = emp_dir / "agent"
        agent_dir.mkdir(parents=True)
        (agent_dir / "manifest.yaml").write_text(
            "hooks:\n  module: my_hooks\n  pre_task: before\n  post_task: after\n"
        )
        (agent_dir / "my_hooks.py").write_text(
            "def before(task): pass\n"
            "def after(task): pass\n"
        )

        hooks = _load_hooks_from_config(emp_dir)
        assert "pre_task" in hooks
        assert "post_task" in hooks
        assert callable(hooks["pre_task"])
        assert callable(hooks["post_task"])

    def test_skips_non_callable_hooks(self, tmp_path):
        from onemancompany.agents.onboarding import _load_hooks_from_config

        emp_dir = tmp_path / "emp"
        agent_dir = emp_dir / "agent"
        agent_dir.mkdir(parents=True)
        (agent_dir / "manifest.yaml").write_text(
            "hooks:\n  module: my_hooks\n  pre_task: not_a_fn\n"
        )
        (agent_dir / "my_hooks.py").write_text("not_a_fn = 42\n")

        hooks = _load_hooks_from_config(emp_dir)
        assert "pre_task" not in hooks

    def test_broken_module_returns_empty(self, tmp_path):
        from onemancompany.agents.onboarding import _load_hooks_from_config

        emp_dir = tmp_path / "emp"
        agent_dir = emp_dir / "agent"
        agent_dir.mkdir(parents=True)
        (agent_dir / "manifest.yaml").write_text(
            "hooks:\n  module: broken_hooks\n  pre_task: fn\n"
        )
        (agent_dir / "broken_hooks.py").write_text("raise RuntimeError('broken')\n")

        hooks = _load_hooks_from_config(emp_dir)
        assert hooks == {}


# ---------------------------------------------------------------------------
# _register_employee_hooks
# ---------------------------------------------------------------------------

class TestRegisterEmployeeHooks:
    def test_no_hooks_does_nothing(self, tmp_path, monkeypatch):
        from onemancompany.agents import onboarding

        emp_dir = tmp_path / "emp"
        emp_dir.mkdir()

        # Should not raise
        onboarding._register_employee_hooks("00010", emp_dir)

    def test_registers_hooks_when_present(self, tmp_path, monkeypatch):
        from onemancompany.agents import onboarding

        emp_dir = tmp_path / "emp"
        agent_dir = emp_dir / "agent"
        agent_dir.mkdir(parents=True)
        (agent_dir / "manifest.yaml").write_text(
            "hooks:\n  module: my_hooks\n  pre_task: before\n"
        )
        (agent_dir / "my_hooks.py").write_text("def before(task): pass\n")

        mock_em = MagicMock()
        with patch("onemancompany.core.agent_loop.employee_manager", mock_em):
            onboarding._register_employee_hooks("00010", emp_dir)

        mock_em.register_hooks.assert_called_once()
        call_args = mock_em.register_hooks.call_args
        assert call_args[0][0] == "00010"
        assert "pre_task" in call_args[0][1]


# ---------------------------------------------------------------------------
# copy_talent_assets — additional coverage
# ---------------------------------------------------------------------------

class TestCopyTalentAssetsAdditional:
    def test_copies_persona_from_profile_yaml(self, tmp_path, monkeypatch):
        """Should copy system_prompt_template to prompts/talent_persona.md."""
        from onemancompany.agents import onboarding

        talent_dir = tmp_path / "talents" / "coding"
        talent_dir.mkdir(parents=True)
        (talent_dir / "profile.yaml").write_text(
            "system_prompt_template: 'You are a senior engineer.'\n"
        )

        monkeypatch.setattr(onboarding, "_TALENTS_CLONE_DIR", tmp_path / "talents")

        emp_dir = tmp_path / "emp"
        emp_dir.mkdir()

        onboarding.copy_talent_assets(talent_dir, emp_dir)

        persona_path = emp_dir / "prompts" / "talent_persona.md"
        assert persona_path.exists()
        assert "senior engineer" in persona_path.read_text()

    def test_copies_claude_md(self, tmp_path, monkeypatch):
        """Should copy CLAUDE.md from talent to employee dir."""
        from onemancompany.agents import onboarding

        talent_dir = tmp_path / "talents" / "coding"
        talent_dir.mkdir(parents=True)
        (talent_dir / "CLAUDE.md").write_text("# Claude instructions\n")

        monkeypatch.setattr(onboarding, "_TALENTS_CLONE_DIR", tmp_path / "talents")

        emp_dir = tmp_path / "emp"
        emp_dir.mkdir()

        onboarding.copy_talent_assets(talent_dir, emp_dir)

        assert (emp_dir / "CLAUDE.md").exists()
        assert "Claude instructions" in (emp_dir / "CLAUDE.md").read_text()

    def test_skips_py_files_in_tools(self, tmp_path, monkeypatch):
        """Should skip .py files in tools/ (they live centrally)."""
        from onemancompany.agents import onboarding

        talent_dir = tmp_path / "talents" / "coding"
        tools_dir = talent_dir / "tools"
        tools_dir.mkdir(parents=True)
        (tools_dir / "my_tool.py").write_text("# tool code")
        (tools_dir / "config.yaml").write_text("key: value")
        (tools_dir / "manifest.yaml").write_text("builtin_tools: []\ncustom_tools: []")

        monkeypatch.setattr(onboarding, "_TALENTS_CLONE_DIR", tmp_path / "talents")

        emp_dir = tmp_path / "emp"
        emp_dir.mkdir()

        onboarding.copy_talent_assets(talent_dir, emp_dir)

        # .py should NOT be copied
        assert not (emp_dir / "tools" / "my_tool.py").exists()
        # config.yaml should be copied
        assert (emp_dir / "tools" / "config.yaml").exists()

    def test_registers_custom_tool_users(self, tmp_path, monkeypatch):
        """Custom tools in manifest.yaml should register the employee."""
        from onemancompany.agents import onboarding

        talent_dir = tmp_path / "talents" / "coding"
        tools_dir = talent_dir / "tools"
        tools_dir.mkdir(parents=True)
        (tools_dir / "manifest.yaml").write_text(
            "builtin_tools: []\ncustom_tools:\n  - my_custom_tool\n"
        )

        # Create central tool dir with tool.yaml
        central_tool_dir = tmp_path / "central_tools" / "my_custom_tool"
        central_tool_dir.mkdir(parents=True)
        (central_tool_dir / "tool.yaml").write_text("id: my_custom_tool\nallowed_users: []\n")

        monkeypatch.setattr(onboarding, "_TALENTS_CLONE_DIR", tmp_path / "talents")
        monkeypatch.setattr(onboarding, "TOOLS_DIR", tmp_path / "central_tools")

        emp_dir = tmp_path / "00010"
        emp_dir.mkdir()

        onboarding.copy_talent_assets(talent_dir, emp_dir)

        import yaml
        data = yaml.safe_load((central_tool_dir / "tool.yaml").read_text())
        assert "00010" in data["allowed_users"]

    def test_installs_functions_and_updates_manifest(self, tmp_path, monkeypatch):
        """install_talent_functions results should update emp tools/manifest.yaml."""
        from onemancompany.agents import onboarding

        talent_dir = tmp_path / "talents" / "coding"
        talent_dir.mkdir(parents=True)

        # Setup functions
        fn_dir = talent_dir / "functions"
        fn_dir.mkdir()
        (fn_dir / "manifest.yaml").write_text(
            "functions:\n"
            "  - name: my_fn\n"
            "    description: test\n"
            "    scope: personal\n"
        )
        (fn_dir / "my_fn.py").write_text(
            "from langchain_core.tools import tool\n"
            "@tool\n"
            "def my_fn(x: str) -> str:\n"
            "    '''test'''\n"
            "    return x\n"
        )

        tools_dir = tmp_path / "central_tools"
        tools_dir.mkdir()
        monkeypatch.setattr(onboarding, "_TALENTS_CLONE_DIR", tmp_path / "talents")
        monkeypatch.setattr(onboarding, "TOOLS_DIR", tools_dir)

        emp_dir = tmp_path / "00010"
        emp_dir.mkdir()

        onboarding.copy_talent_assets(talent_dir, emp_dir)

        # Employee's tools/manifest.yaml should list the installed function
        emp_manifest = emp_dir / "tools" / "manifest.yaml"
        assert emp_manifest.exists()
        import yaml
        data = yaml.safe_load(emp_manifest.read_text())
        assert "my_fn" in data.get("custom_tools", [])

    def test_empty_system_prompt_not_copied(self, tmp_path, monkeypatch):
        """Empty or whitespace-only system_prompt_template should not create persona file."""
        from onemancompany.agents import onboarding

        talent_dir = tmp_path / "talents" / "coding"
        talent_dir.mkdir(parents=True)
        (talent_dir / "profile.yaml").write_text(
            "system_prompt_template: '   '\n"
        )

        monkeypatch.setattr(onboarding, "_TALENTS_CLONE_DIR", tmp_path / "talents")

        emp_dir = tmp_path / "emp"
        emp_dir.mkdir()

        onboarding.copy_talent_assets(talent_dir, emp_dir)

        assert not (emp_dir / "prompts" / "talent_persona.md").exists()

    def test_does_not_overwrite_existing_claude_md(self, tmp_path, monkeypatch):
        """Existing CLAUDE.md should not be overwritten."""
        from onemancompany.agents import onboarding

        talent_dir = tmp_path / "talents" / "coding"
        talent_dir.mkdir(parents=True)
        (talent_dir / "CLAUDE.md").write_text("NEW content")

        monkeypatch.setattr(onboarding, "_TALENTS_CLONE_DIR", tmp_path / "talents")

        emp_dir = tmp_path / "emp"
        emp_dir.mkdir()
        (emp_dir / "CLAUDE.md").write_text("EXISTING content")

        onboarding.copy_talent_assets(talent_dir, emp_dir)

        assert (emp_dir / "CLAUDE.md").read_text() == "EXISTING content"


# ---------------------------------------------------------------------------
# execute_hire — additional coverage
# ---------------------------------------------------------------------------

class TestExecuteHireAdditional:
    def _setup_hire(self, tmp_path, monkeypatch):
        """Common setup for execute_hire tests."""
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
        monkeypatch.setattr(onboarding, "_TALENTS_CLONE_DIR", tmp_path / "talents")
        monkeypatch.setattr(onboarding, "settings", MagicMock(host="localhost", port=8000))
        monkeypatch.setattr("onemancompany.agents.onboarding.get_next_desk_for_department", lambda cs, d: (1, 1))
        monkeypatch.setattr("onemancompany.agents.onboarding.compute_layout", lambda cs: {})
        monkeypatch.setattr("onemancompany.agents.onboarding.persist_all_desk_positions", lambda cs: None)
        monkeypatch.setattr("onemancompany.agents.onboarding.event_bus.publish", AsyncMock())
        monkeypatch.setattr("onemancompany.core.model_costs.compute_salary", lambda m: 5.0)
        monkeypatch.setattr("onemancompany.core.agent_loop.get_agent_loop", lambda eid: None)
        monkeypatch.setattr("onemancompany.core.agent_loop.register_and_start_agent", AsyncMock())

        return cs, onboarding

    @pytest.mark.asyncio
    async def test_hire_copies_talent_assets_when_not_remote(self, tmp_path, monkeypatch):
        cs, onboarding = self._setup_hire(tmp_path, monkeypatch)

        # Create a talent directory with folder-based skills
        talent_dir = tmp_path / "talents" / "my_talent"
        (talent_dir / "skills" / "coding").mkdir(parents=True)
        (talent_dir / "skills" / "coding" / "SKILL.md").write_text("# Coding skill")

        emp = await onboarding.execute_hire(
            name="Dev", nickname="测试", role="Engineer", skills=[],
            talent_id="my_talent",
        )

        emp_dir = tmp_path / "employees" / emp.id
        assert (emp_dir / "skills" / "coding" / "SKILL.md").exists()

    @pytest.mark.asyncio
    async def test_hire_does_not_copy_assets_when_remote(self, tmp_path, monkeypatch):
        cs, onboarding = self._setup_hire(tmp_path, monkeypatch)

        talent_dir = tmp_path / "talents" / "my_talent"
        (talent_dir / "skills" / "coding").mkdir(parents=True)
        (talent_dir / "skills" / "coding" / "SKILL.md").write_text("# Coding skill")

        emp = await onboarding.execute_hire(
            name="Remote Dev", nickname="测试", role="Engineer", skills=[],
            talent_id="my_talent", remote=True,
        )

        emp_dir = tmp_path / "employees" / emp.id
        # Skills should NOT be copied for remote employees
        assert not (emp_dir / "skills" / "coding" / "SKILL.md").exists()

    @pytest.mark.asyncio
    async def test_hire_self_hosted_copies_launch_sh(self, tmp_path, monkeypatch):
        cs, onboarding = self._setup_hire(tmp_path, monkeypatch)

        talent_dir = tmp_path / "talents" / "my_talent"
        talent_dir.mkdir(parents=True)
        (talent_dir / "launch.sh").write_text("#!/bin/bash\necho hello\n")

        mock_self_hosted = MagicMock()
        monkeypatch.setattr("onemancompany.core.agent_loop.register_self_hosted", mock_self_hosted)

        emp = await onboarding.execute_hire(
            name="Self Hosted", nickname="测试", role="Engineer", skills=[],
            talent_id="my_talent", hosting="self",
        )

        emp_dir = tmp_path / "employees" / emp.id
        launch_path = emp_dir / "launch.sh"
        assert launch_path.exists()
        # Should be executable
        import stat
        assert launch_path.stat().st_mode & stat.S_IXUSR

    @pytest.mark.asyncio
    async def test_hire_copies_heartbeat_sh(self, tmp_path, monkeypatch):
        cs, onboarding = self._setup_hire(tmp_path, monkeypatch)

        talent_dir = tmp_path / "talents" / "my_talent"
        talent_dir.mkdir(parents=True)
        (talent_dir / "heartbeat.sh").write_text("#!/bin/bash\necho alive\n")

        emp = await onboarding.execute_hire(
            name="Dev", nickname="测试", role="Engineer", skills=[],
            talent_id="my_talent",
        )

        emp_dir = tmp_path / "employees" / emp.id
        hb_path = emp_dir / "heartbeat.sh"
        assert hb_path.exists()
        import stat
        assert hb_path.stat().st_mode & stat.S_IXUSR

    @pytest.mark.asyncio
    async def test_hire_non_openrouter_zero_salary(self, tmp_path, monkeypatch):
        cs, onboarding = self._setup_hire(tmp_path, monkeypatch)

        emp = await onboarding.execute_hire(
            name="Anthropic Dev", nickname="测试", role="Engineer", skills=[],
            api_provider="anthropic", llm_model="claude-sonnet",
        )

        assert emp.salary_per_1m_tokens == 0.0

    @pytest.mark.asyncio
    async def test_hire_agent_already_registered_skips_registration(self, tmp_path, monkeypatch):
        cs, onboarding = self._setup_hire(tmp_path, monkeypatch)

        # get_agent_loop returns truthy (already registered)
        monkeypatch.setattr("onemancompany.core.agent_loop.get_agent_loop", lambda eid: MagicMock())
        mock_register = AsyncMock()
        monkeypatch.setattr("onemancompany.core.agent_loop.register_and_start_agent", mock_register)

        emp = await onboarding.execute_hire(
            name="Already Reg", nickname="测试", role="Engineer", skills=[],
        )

        mock_register.assert_not_called()


# ---------------------------------------------------------------------------
# _validate_tool_module — spec is None edge case
# ---------------------------------------------------------------------------

class TestValidateToolModuleSpecNone:
    def test_spec_none_returns_false(self, tmp_path, monkeypatch):
        """When spec_from_file_location returns None, should return False."""
        from onemancompany.agents import onboarding
        import importlib.util

        py_file = tmp_path / "tool.py"
        py_file.write_text("x = 1\n")

        monkeypatch.setattr(importlib.util, "spec_from_file_location", lambda *a, **kw: None)

        result = onboarding._validate_tool_module(py_file)
        assert result is False


# ---------------------------------------------------------------------------
# install_talent_agent_config — runner and hooks exception handling
# ---------------------------------------------------------------------------

class TestInstallTalentAgentConfigExceptions:
    def test_runner_validation_exception_handled(self, tmp_path, monkeypatch):
        """Runner module that raises on import should be caught."""
        from onemancompany.agents import onboarding

        talent_dir = tmp_path / "talents" / "test_talent"
        agent_dir = talent_dir / "agent"
        agent_dir.mkdir(parents=True)
        manifest = agent_dir / "manifest.yaml"
        manifest.write_text(
            "runner:\n  module: broken_runner\n  class: MyRunner\n"
        )
        (agent_dir / "broken_runner.py").write_text("raise RuntimeError('broken')\n")

        monkeypatch.setattr(onboarding, "_TALENTS_CLONE_DIR", tmp_path / "talents")

        emp_dir = tmp_path / "emp"
        emp_dir.mkdir()

        # Should not raise, just log warning
        result = onboarding.install_talent_agent_config(tmp_path / "talents" / "test_talent", emp_dir, "00010")
        assert result is not None
        assert result["runner"]["module"] == "broken_runner"

    def test_hooks_validation_exception_handled(self, tmp_path, monkeypatch):
        """Hooks module that raises on import should be caught."""
        from onemancompany.agents import onboarding

        talent_dir = tmp_path / "talents" / "test_talent"
        agent_dir = talent_dir / "agent"
        agent_dir.mkdir(parents=True)
        manifest = agent_dir / "manifest.yaml"
        manifest.write_text(
            "hooks:\n  module: broken_hooks\n  pre_task: my_fn\n"
        )
        (agent_dir / "broken_hooks.py").write_text("raise RuntimeError('broken hooks')\n")

        monkeypatch.setattr(onboarding, "_TALENTS_CLONE_DIR", tmp_path / "talents")

        emp_dir = tmp_path / "emp"
        emp_dir.mkdir()

        # Should not raise, just log warning
        result = onboarding.install_talent_agent_config(tmp_path / "talents" / "test_talent", emp_dir, "00010")
        assert result is not None

    def test_runner_class_not_subclass_of_base(self, tmp_path, monkeypatch):
        """Runner class that is not a BaseAgentRunner subclass should log warning."""
        from onemancompany.agents import onboarding

        talent_dir = tmp_path / "talents" / "test_talent"
        agent_dir = talent_dir / "agent"
        agent_dir.mkdir(parents=True)
        manifest = agent_dir / "manifest.yaml"
        manifest.write_text(
            "runner:\n  module: wrong_runner\n  class: NotARunner\n"
        )
        (agent_dir / "wrong_runner.py").write_text(
            "class NotARunner:\n"
            "    pass\n"
        )

        monkeypatch.setattr(onboarding, "_TALENTS_CLONE_DIR", tmp_path / "talents")

        emp_dir = tmp_path / "emp"
        emp_dir.mkdir()

        # Should not raise, logs a warning about not being a BaseAgentRunner subclass
        result = onboarding.install_talent_agent_config(tmp_path / "talents" / "test_talent", emp_dir, "00010")
        assert result is not None

    def test_runner_class_not_found_in_module(self, tmp_path, monkeypatch):
        """Runner class that doesn't exist in module should log warning."""
        from onemancompany.agents import onboarding

        talent_dir = tmp_path / "talents" / "test_talent"
        agent_dir = talent_dir / "agent"
        agent_dir.mkdir(parents=True)
        manifest = agent_dir / "manifest.yaml"
        manifest.write_text(
            "runner:\n  module: some_runner\n  class: NonexistentClass\n"
        )
        (agent_dir / "some_runner.py").write_text("# no class here\n")

        monkeypatch.setattr(onboarding, "_TALENTS_CLONE_DIR", tmp_path / "talents")

        emp_dir = tmp_path / "emp"
        emp_dir.mkdir()

        result = onboarding.install_talent_agent_config(tmp_path / "talents" / "test_talent", emp_dir, "00010")
        assert result is not None

    def test_hooks_with_valid_callable(self, tmp_path, monkeypatch):
        """Hooks with valid callable functions should not produce warnings."""
        from onemancompany.agents import onboarding

        talent_dir = tmp_path / "talents" / "test_talent"
        agent_dir = talent_dir / "agent"
        agent_dir.mkdir(parents=True)
        manifest = agent_dir / "manifest.yaml"
        manifest.write_text(
            "hooks:\n  module: good_hooks\n  pre_task: before\n  post_task: after\n"
        )
        (agent_dir / "good_hooks.py").write_text(
            "def before(task): pass\ndef after(task): pass\n"
        )

        monkeypatch.setattr(onboarding, "_TALENTS_CLONE_DIR", tmp_path / "talents")

        emp_dir = tmp_path / "emp"
        emp_dir.mkdir()

        result = onboarding.install_talent_agent_config(tmp_path / "talents" / "test_talent", emp_dir, "00010")
        assert result is not None


# ---------------------------------------------------------------------------
# copy_talent_assets — existing employee manifest merge
# ---------------------------------------------------------------------------

class TestCopyTalentAssetsManifestMerge:
    def test_merges_with_existing_emp_manifest(self, tmp_path, monkeypatch):
        """When emp already has a tools/manifest.yaml, should merge custom_tools."""
        from onemancompany.agents import onboarding

        talent_dir = tmp_path / "talents" / "coding"
        talent_dir.mkdir(parents=True)

        # Setup functions
        fn_dir = talent_dir / "functions"
        fn_dir.mkdir()
        (fn_dir / "manifest.yaml").write_text(
            "functions:\n"
            "  - name: new_fn\n"
            "    description: test\n"
            "    scope: personal\n"
        )
        (fn_dir / "new_fn.py").write_text(
            "from langchain_core.tools import tool\n"
            "@tool\n"
            "def new_fn(x: str) -> str:\n"
            "    '''test'''\n"
            "    return x\n"
        )

        tools_dir = tmp_path / "central_tools"
        tools_dir.mkdir()
        monkeypatch.setattr(onboarding, "_TALENTS_CLONE_DIR", tmp_path / "talents")
        monkeypatch.setattr(onboarding, "TOOLS_DIR", tools_dir)

        emp_dir = tmp_path / "00010"
        emp_dir.mkdir()
        # Pre-existing manifest with old custom tools
        emp_tools = emp_dir / "tools"
        emp_tools.mkdir()
        emp_manifest = emp_tools / "manifest.yaml"
        emp_manifest.write_text(
            "builtin_tools: []\ncustom_tools:\n  - existing_tool\n"
        )

        onboarding.copy_talent_assets(talent_dir, emp_dir)

        import yaml
        data = yaml.safe_load(emp_manifest.read_text())
        assert "existing_tool" in data["custom_tools"]
        assert "new_fn" in data["custom_tools"]

    def test_does_not_duplicate_existing_custom_tool(self, tmp_path, monkeypatch):
        """If function is already in custom_tools, should not add duplicate."""
        from onemancompany.agents import onboarding

        talent_dir = tmp_path / "talents" / "coding"
        talent_dir.mkdir(parents=True)

        fn_dir = talent_dir / "functions"
        fn_dir.mkdir()
        (fn_dir / "manifest.yaml").write_text(
            "functions:\n"
            "  - name: my_fn\n"
            "    description: test\n"
        )
        (fn_dir / "my_fn.py").write_text(
            "from langchain_core.tools import tool\n"
            "@tool\n"
            "def my_fn(x: str) -> str:\n"
            "    '''test'''\n"
            "    return x\n"
        )

        tools_dir = tmp_path / "central_tools"
        tools_dir.mkdir()
        monkeypatch.setattr(onboarding, "_TALENTS_CLONE_DIR", tmp_path / "talents")
        monkeypatch.setattr(onboarding, "TOOLS_DIR", tools_dir)

        emp_dir = tmp_path / "00010"
        emp_dir.mkdir()
        emp_tools = emp_dir / "tools"
        emp_tools.mkdir()
        emp_manifest = emp_tools / "manifest.yaml"
        emp_manifest.write_text(
            "builtin_tools: []\ncustom_tools:\n  - my_fn\n"
        )

        onboarding.copy_talent_assets(talent_dir, emp_dir)

        import yaml
        data = yaml.safe_load(emp_manifest.read_text())
        assert data["custom_tools"].count("my_fn") == 1
