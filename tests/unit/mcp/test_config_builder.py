"""Tests for MCP config builder."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from onemancompany.mcp.config_builder import build_mcp_config, write_mcp_config, _resolve_tool_list


# ---------------------------------------------------------------------------
# _resolve_tool_list
# ---------------------------------------------------------------------------

class TestResolveToolList:
    def test_founding_employee_gets_all_tools(self):
        """Founding employees (in FOUNDING_IDS) get all tools including gated."""
        with patch("onemancompany.mcp.config_builder.FOUNDING_IDS", frozenset({"00002"})):
            tools = _resolve_tool_list("00002")
            assert "dispatch_task" in tools
            assert "set_acceptance_criteria" in tools
            assert "manage_tool_access" in tools
            assert "dispatch_team_tasks" in tools

    def test_regular_employee_base_only(self):
        """Regular employees without tool_permissions get only base tools."""
        fake_cfg = MagicMock()
        fake_cfg.tool_permissions = []
        with patch("onemancompany.mcp.config_builder.FOUNDING_IDS", frozenset({"00002"})), \
             patch("onemancompany.mcp.config_builder.employee_configs", {"00099": fake_cfg}):
            tools = _resolve_tool_list("00099")
            assert "dispatch_task" in tools  # base tool
            assert "list_colleagues" in tools  # base tool
            assert "set_acceptance_criteria" not in tools  # gated
            assert "manage_tool_access" not in tools  # gated

    def test_regular_employee_with_permissions(self):
        """Regular employees with tool_permissions get those gated tools."""
        fake_cfg = MagicMock()
        fake_cfg.tool_permissions = ["read_file", "use_tool"]
        with patch("onemancompany.mcp.config_builder.FOUNDING_IDS", frozenset({"00002"})), \
             patch("onemancompany.mcp.config_builder.employee_configs", {"00099": fake_cfg}):
            tools = _resolve_tool_list("00099")
            assert "read_file" in tools
            assert "use_tool" in tools
            assert "set_acceptance_criteria" not in tools


# ---------------------------------------------------------------------------
# build_mcp_config
# ---------------------------------------------------------------------------

class TestBuildMcpConfig:
    def test_config_structure(self):
        """Config has the correct Claude MCP config format."""
        with patch("onemancompany.mcp.config_builder._resolve_tool_list", return_value=["dispatch_task"]):
            cfg = build_mcp_config("00002", task_id="t1", project_id="p1", project_dir="/tmp/proj")

        assert "mcpServers" in cfg
        assert "onemancompany" in cfg["mcpServers"]
        server = cfg["mcpServers"]["onemancompany"]
        assert "command" in server
        assert "-m" in server["args"]
        assert "onemancompany.mcp.server" in server["args"]

        env = server["env"]
        assert env["OMC_EMPLOYEE_ID"] == "00002"
        assert env["OMC_TASK_ID"] == "t1"
        assert env["OMC_PROJECT_ID"] == "p1"
        assert env["OMC_PROJECT_DIR"] == "/tmp/proj"
        assert env["OMC_TOOLS"] == "dispatch_task"

    def test_default_server_url(self):
        with patch("onemancompany.mcp.config_builder._resolve_tool_list", return_value=[]):
            cfg = build_mcp_config("00002")
        assert cfg["mcpServers"]["onemancompany"]["env"]["OMC_SERVER_URL"] == "http://localhost:8000"


# ---------------------------------------------------------------------------
# write_mcp_config
# ---------------------------------------------------------------------------

class TestWriteMcpConfig:
    def test_writes_json_file(self, tmp_path):
        emp_dir = tmp_path / "00002"
        emp_dir.mkdir()
        with patch("onemancompany.mcp.config_builder.EMPLOYEES_DIR", tmp_path), \
             patch("onemancompany.mcp.config_builder._resolve_tool_list", return_value=["dispatch_task"]):
            path = write_mcp_config("00002", task_id="t1")

        assert path.exists()
        data = json.loads(path.read_text())
        assert data["mcpServers"]["onemancompany"]["env"]["OMC_EMPLOYEE_ID"] == "00002"
