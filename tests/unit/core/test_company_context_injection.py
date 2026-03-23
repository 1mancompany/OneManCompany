"""Tests for unified company context injection into task prompts."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from onemancompany.core.vessel import EmployeeManager


_STORE = "onemancompany.core.store"


class TestBuildCompanyContextBlock:
    """EmployeeManager._build_company_context_block produces correct output."""

    @patch(f"{_STORE}.load_employee_work_principles", return_value="")
    @patch(f"{_STORE}.load_employee_guidance", return_value=[])
    @patch("onemancompany.core.config.load_workflows", return_value={})
    @patch(f"{_STORE}.load_culture", return_value=[])
    def test_empty_when_no_data(self, _cult, _wf, _guid, _wp):
        result = EmployeeManager._build_company_context_block("00010")
        assert result == ""

    @patch(f"{_STORE}.load_employee_work_principles", return_value="")
    @patch(f"{_STORE}.load_employee_guidance", return_value=[])
    @patch("onemancompany.core.config.load_workflows", return_value={})
    @patch(f"{_STORE}.load_culture", return_value=[
        {"content": "Users first"},
        {"content": "Stay in your lane"},
    ])
    def test_culture_injected(self, _cult, _wf, _guid, _wp):
        result = EmployeeManager._build_company_context_block("00010")
        assert "[Company Context]" in result
        assert "## Company Culture" in result
        assert "Users first" in result
        assert "Stay in your lane" in result

    @patch(f"{_STORE}.load_employee_work_principles", return_value="")
    @patch(f"{_STORE}.load_employee_guidance", return_value=[])
    @patch("onemancompany.core.config.load_workflows", return_value={
        "task_dispatch_sop": "# Task Dispatch\nMust specify workspace path.",
    })
    @patch(f"{_STORE}.load_culture", return_value=[])
    def test_sops_injected(self, _cult, _wf, _guid, _wp):
        result = EmployeeManager._build_company_context_block("00010")
        assert "## SOPs & Workflows" in result
        assert "task_dispatch_sop: Task Dispatch" in result
        assert "read(" in result

    @patch(f"{_STORE}.load_employee_work_principles", return_value="")
    @patch(f"{_STORE}.load_employee_guidance", return_value=[
        "Always verify deliverables on disk",
        "Communicate progress proactively",
    ])
    @patch("onemancompany.core.config.load_workflows", return_value={})
    @patch(f"{_STORE}.load_culture", return_value=[])
    def test_guidance_injected(self, _cult, _wf, _guid, _wp):
        result = EmployeeManager._build_company_context_block("00010")
        assert "## CEO Guidance" in result
        assert "Always verify deliverables on disk" in result

    @patch(f"{_STORE}.load_employee_work_principles", return_value="Write clean, tested code. Always run tests before submitting.")
    @patch(f"{_STORE}.load_employee_guidance", return_value=[])
    @patch("onemancompany.core.config.load_workflows", return_value={})
    @patch(f"{_STORE}.load_culture", return_value=[])
    def test_work_principles_injected(self, _cult, _wf, _guid, _wp):
        result = EmployeeManager._build_company_context_block("00010")
        assert "## Your Work Principles" in result
        assert "Write clean, tested code" in result

    @patch(f"{_STORE}.load_employee_work_principles", return_value="Be thorough.")
    @patch(f"{_STORE}.load_employee_guidance", return_value=["Ship fast"])
    @patch("onemancompany.core.config.load_workflows", return_value={
        "intake_sop": "# Intake\nStep 1",
    })
    @patch(f"{_STORE}.load_culture", return_value=[{"content": "Users first"}])
    def test_all_sections_present(self, _cult, _wf, _guid, _wp):
        result = EmployeeManager._build_company_context_block("00010")
        assert "[Company Context]" in result
        assert "[/Company Context]" in result
        assert "## Company Culture" in result
        assert "## SOPs & Workflows" in result
        assert "## CEO Guidance" in result
        assert "## Your Work Principles" in result

    @patch(f"{_STORE}.load_employee_work_principles", return_value="   \n  ")
    @patch(f"{_STORE}.load_employee_guidance", return_value=[])
    @patch("onemancompany.core.config.load_workflows", return_value={})
    @patch(f"{_STORE}.load_culture", return_value=[])
    def test_whitespace_only_principles_skipped(self, _cult, _wf, _guid, _wp):
        result = EmployeeManager._build_company_context_block("00010")
        assert result == ""
