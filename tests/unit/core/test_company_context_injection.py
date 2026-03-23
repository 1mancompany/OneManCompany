"""Tests for unified company context injection into task prompts."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from onemancompany.core.vessel import EmployeeManager, build_role_identity


_STORE = "onemancompany.core.store"
_CONFIG = "onemancompany.core.config"

# Mock profile for a regular (non-founding) employee
_MOCK_PROFILE = {
    "name": "TestDev",
    "nickname": "测试侠",
    "role": "Engineer",
    "department": "Engineering",
    "level": 2,
}


def _patch_profile(profile=None):
    """Patch load_employee_profile_yaml to return a controlled profile."""
    return patch(
        f"{_CONFIG}.load_employee_profile_yaml",
        return_value=profile if profile is not None else _MOCK_PROFILE,
    )


def _make_manager(employee_id: str = "00010") -> EmployeeManager:
    """Create a minimal EmployeeManager with a non-LangChain executor stub."""
    mgr = EmployeeManager.__new__(EmployeeManager)
    mgr.executors = {employee_id: MagicMock()}  # not LangChainExecutor
    return mgr


class TestBuildRoleIdentity:
    """build_role_identity() produces correct output for different archetypes."""

    @_patch_profile({})
    def test_empty_for_founding(self, _prof):
        """Founding employees get empty identity (they define their own)."""
        assert build_role_identity("00003") == ""

    @_patch_profile(_MOCK_PROFILE)
    def test_executor_archetype(self, _prof):
        result = build_role_identity("00010")
        assert "## Who You Are" in result
        assert "TestDev" in result
        assert "executor" in result
        assert "outside your role" in result
        assert "Mid-level" in result

    @_patch_profile({"name": "Alice", "role": "PM", "department": "Marketing", "level": 2})
    def test_manager_archetype(self, _prof):
        result = build_role_identity("00010")
        assert "coordinator" in result
        assert "dispatch_child()" in result
        assert "Do NOT write code" in result

    @_patch_profile({"name": "Bob", "role": "Engineer", "department": "Engineering", "level": 1})
    def test_junior_level(self, _prof):
        result = build_role_identity("00010")
        assert "Junior" in result

    @_patch_profile(_MOCK_PROFILE)
    def test_never_do_and_core_actions(self, _prof):
        result = build_role_identity("00010")
        assert "Things you must NEVER do" in result
        assert "Your core actions" in result


class TestBuildCompanyContextBlock:
    """EmployeeManager._build_company_context_block produces correct output."""

    @_patch_profile({})
    @patch(f"{_STORE}.load_employee_work_principles", return_value="")
    @patch(f"{_STORE}.load_employee_guidance", return_value=[])
    @patch(f"{_CONFIG}.load_workflows", return_value={})
    @patch(f"{_STORE}.load_culture", return_value=[])
    def test_empty_when_no_data_founding(self, _cult, _wf, _guid, _wp, _prof):
        """Founding employees get no role identity from this block."""
        mgr = _make_manager("00003")
        mgr.executors = {"00003": MagicMock()}
        result = mgr._build_company_context_block("00003")
        assert result == ""

    @_patch_profile(_MOCK_PROFILE)
    @patch(f"{_STORE}.load_employee_work_principles", return_value="")
    @patch(f"{_STORE}.load_employee_guidance", return_value=[])
    @patch(f"{_CONFIG}.load_workflows", return_value={})
    @patch(f"{_STORE}.load_culture", return_value=[])
    def test_role_identity_for_non_langchain(self, _cult, _wf, _guid, _wp, _prof):
        """Non-LangChain employees get role identity in company context block."""
        mgr = _make_manager()
        result = mgr._build_company_context_block("00010")
        assert "[Company Context]" in result
        assert "## Who You Are" in result
        assert "TestDev" in result

    @_patch_profile(_MOCK_PROFILE)
    @patch(f"{_STORE}.load_employee_work_principles", return_value="")
    @patch(f"{_STORE}.load_employee_guidance", return_value=[])
    @patch(f"{_CONFIG}.load_workflows", return_value={})
    @patch(f"{_STORE}.load_culture", return_value=[])
    def test_no_identity_for_langchain(self, _cult, _wf, _guid, _wp, _prof):
        """LangChain employees do NOT get role identity in company context block."""
        from onemancompany.core.vessel import LangChainExecutor
        mgr = _make_manager()
        mgr.executors["00010"] = MagicMock(spec=LangChainExecutor)
        result = mgr._build_company_context_block("00010")
        # No identity, no other data → empty
        assert result == ""

    @_patch_profile(_MOCK_PROFILE)
    @patch(f"{_STORE}.load_employee_work_principles", return_value="")
    @patch(f"{_STORE}.load_employee_guidance", return_value=[])
    @patch(f"{_CONFIG}.load_workflows", return_value={})
    @patch(f"{_STORE}.load_culture", return_value=[
        {"content": "Users first"},
        {"content": "Stay in your lane"},
    ])
    def test_culture_injected(self, _cult, _wf, _guid, _wp, _prof):
        mgr = _make_manager()
        result = mgr._build_company_context_block("00010")
        assert "## Company Culture" in result
        assert "Users first" in result
        assert "Stay in your lane" in result

    @_patch_profile(_MOCK_PROFILE)
    @patch(f"{_STORE}.load_employee_work_principles", return_value="")
    @patch(f"{_STORE}.load_employee_guidance", return_value=[])
    @patch(f"{_CONFIG}.load_workflows", return_value={
        "task_dispatch_sop": "# Task Dispatch\nMust specify workspace path.",
    })
    @patch(f"{_STORE}.load_culture", return_value=[])
    def test_sops_injected(self, _cult, _wf, _guid, _wp, _prof):
        mgr = _make_manager()
        result = mgr._build_company_context_block("00010")
        assert "## SOPs & Workflows" in result
        assert "task_dispatch_sop: Task Dispatch" in result
        assert "read(" in result

    @_patch_profile(_MOCK_PROFILE)
    @patch(f"{_STORE}.load_employee_work_principles", return_value="")
    @patch(f"{_STORE}.load_employee_guidance", return_value=[
        "Always verify deliverables on disk",
        "Communicate progress proactively",
    ])
    @patch(f"{_CONFIG}.load_workflows", return_value={})
    @patch(f"{_STORE}.load_culture", return_value=[])
    def test_guidance_injected(self, _cult, _wf, _guid, _wp, _prof):
        mgr = _make_manager()
        result = mgr._build_company_context_block("00010")
        assert "## CEO Guidance" in result
        assert "Always verify deliverables on disk" in result

    @_patch_profile(_MOCK_PROFILE)
    @patch(f"{_STORE}.load_employee_work_principles", return_value="Write clean, tested code. Always run tests before submitting.")
    @patch(f"{_STORE}.load_employee_guidance", return_value=[])
    @patch(f"{_CONFIG}.load_workflows", return_value={})
    @patch(f"{_STORE}.load_culture", return_value=[])
    def test_work_principles_injected(self, _cult, _wf, _guid, _wp, _prof):
        mgr = _make_manager()
        result = mgr._build_company_context_block("00010")
        assert "## Your Work Principles" in result
        assert "Write clean, tested code" in result

    @_patch_profile(_MOCK_PROFILE)
    @patch(f"{_STORE}.load_employee_work_principles", return_value="Be thorough.")
    @patch(f"{_STORE}.load_employee_guidance", return_value=["Ship fast"])
    @patch(f"{_CONFIG}.load_workflows", return_value={
        "intake_sop": "# Intake\nStep 1",
    })
    @patch(f"{_STORE}.load_culture", return_value=[{"content": "Users first"}])
    def test_all_sections_present(self, _cult, _wf, _guid, _wp, _prof):
        mgr = _make_manager()
        result = mgr._build_company_context_block("00010")
        assert "[Company Context]" in result
        assert "[/Company Context]" in result
        assert "## Who You Are" in result
        assert "## Company Culture" in result
        assert "## SOPs & Workflows" in result
        assert "## CEO Guidance" in result
        assert "## Your Work Principles" in result

    @_patch_profile({})
    @patch(f"{_STORE}.load_employee_work_principles", return_value="   \n  ")
    @patch(f"{_STORE}.load_employee_guidance", return_value=[])
    @patch(f"{_CONFIG}.load_workflows", return_value={})
    @patch(f"{_STORE}.load_culture", return_value=[])
    def test_whitespace_only_principles_skipped(self, _cult, _wf, _guid, _wp, _prof):
        """Founding employee with whitespace-only principles → empty."""
        mgr = _make_manager("00003")
        mgr.executors = {"00003": MagicMock()}
        result = mgr._build_company_context_block("00003")
        assert result == ""
