"""Unit tests for core/tool_registry.py — ToolRegistry and ToolMeta."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_tool(name: str) -> MagicMock:
    """Create a mock LangChain BaseTool with the given name."""
    tool = MagicMock()
    tool.name = name
    return tool


def _make_employee(
    emp_id: str,
    role: str = "Engineer",
    tool_permissions: list[str] | None = None,
) -> MagicMock:
    """Create a mock Employee object."""
    emp = MagicMock()
    emp.id = emp_id
    emp.role = role
    emp.tool_permissions = tool_permissions or []
    return emp


# ---------------------------------------------------------------------------
# ToolMeta dataclass
# ---------------------------------------------------------------------------

class TestToolMeta:
    def test_defaults(self):
        from onemancompany.core.tool_registry import ToolMeta

        meta = ToolMeta(name="foo", category="base")
        assert meta.name == "foo"
        assert meta.category == "base"
        assert meta.allowed_roles is None
        assert meta.allowed_users is None
        assert meta.source == "internal"

    def test_custom_fields(self):
        from onemancompany.core.tool_registry import ToolMeta

        meta = ToolMeta(
            name="bar",
            category="role",
            allowed_roles=["Engineer", "Designer"],
            allowed_users=["00007"],
            source="asset",
        )
        assert meta.allowed_roles == ["Engineer", "Designer"]
        assert meta.allowed_users == ["00007"]
        assert meta.source == "asset"


# ---------------------------------------------------------------------------
# ToolRegistry — registration and lookup
# ---------------------------------------------------------------------------

class TestToolRegistryBasics:
    def test_register_and_get_tool(self):
        from onemancompany.core.tool_registry import ToolMeta, ToolRegistry

        reg = ToolRegistry()
        tool = _make_mock_tool("my_tool")
        meta = ToolMeta(name="my_tool", category="base")
        reg.register(tool, meta)

        assert reg.get_tool("my_tool") is tool
        assert reg.get_meta("my_tool") is meta

    def test_get_tool_missing_returns_none(self):
        from onemancompany.core.tool_registry import ToolRegistry

        reg = ToolRegistry()
        assert reg.get_tool("nonexistent") is None

    def test_get_meta_missing_returns_none(self):
        from onemancompany.core.tool_registry import ToolRegistry

        reg = ToolRegistry()
        assert reg.get_meta("nonexistent") is None

    def test_all_tool_names(self):
        from onemancompany.core.tool_registry import ToolMeta, ToolRegistry

        reg = ToolRegistry()
        for name in ["alpha", "beta", "gamma"]:
            reg.register(_make_mock_tool(name), ToolMeta(name=name, category="base"))

        names = reg.all_tool_names()
        assert set(names) == {"alpha", "beta", "gamma"}

    def test_all_tool_names_empty(self):
        from onemancompany.core.tool_registry import ToolRegistry

        reg = ToolRegistry()
        assert reg.all_tool_names() == []

    def test_register_overwrites_existing(self):
        from onemancompany.core.tool_registry import ToolMeta, ToolRegistry

        reg = ToolRegistry()
        tool_v1 = _make_mock_tool("t")
        tool_v2 = _make_mock_tool("t")
        reg.register(tool_v1, ToolMeta(name="t", category="base"))
        reg.register(tool_v2, ToolMeta(name="t", category="gated"))

        assert reg.get_tool("t") is tool_v2
        assert reg.get_meta("t").category == "gated"


# ---------------------------------------------------------------------------
# ToolRegistry — get_tools_for (permission filtering)
# ---------------------------------------------------------------------------

class TestGetToolsFor:
    def _build_registry(self):
        """Build a registry with one tool in each category."""
        from onemancompany.core.tool_registry import ToolMeta, ToolRegistry

        reg = ToolRegistry()

        # base — always included
        reg.register(
            _make_mock_tool("list_colleagues"),
            ToolMeta(name="list_colleagues", category="base"),
        )

        # gated — only if tool name in employee's tool_permissions
        reg.register(
            _make_mock_tool("bash"),
            ToolMeta(name="bash", category="gated"),
        )

        # role — only if employee role matches
        reg.register(
            _make_mock_tool("deploy"),
            ToolMeta(name="deploy", category="role", allowed_roles=["Engineer"]),
        )

        # asset — unrestricted (allowed_users is None)
        reg.register(
            _make_mock_tool("gmail"),
            ToolMeta(name="gmail", category="asset", source="asset"),
        )

        # asset — restricted to specific users
        reg.register(
            _make_mock_tool("roblox"),
            ToolMeta(name="roblox", category="asset", allowed_users=["00007"], source="asset"),
        )

        return reg

    @patch("onemancompany.core.state.company_state")
    def test_base_tools_always_included(self, mock_state):
        reg = self._build_registry()
        emp = _make_employee("00010", role="Marketing", tool_permissions=[])
        mock_state.employees = {"00010": emp}

        tools = reg.get_tools_for("00010")
        tool_names = [t.name for t in tools]
        assert "list_colleagues" in tool_names

    @patch("onemancompany.core.state.company_state")
    def test_gated_tools_always_included(self, mock_state):
        """Gated category now treated same as base — always included."""
        reg = self._build_registry()
        emp = _make_employee("00010", role="Designer", tool_permissions=[])
        mock_state.employees = {"00010": emp}

        tools = reg.get_tools_for("00010")
        tool_names = [t.name for t in tools]
        # bash is registered as "gated" in _build_registry but now always allowed
        assert "bash" in tool_names

    @patch("onemancompany.core.state.company_state")
    def test_role_tools_matching_role(self, mock_state):
        reg = self._build_registry()
        emp = _make_employee("00010", role="Engineer")
        mock_state.employees = {"00010": emp}

        tools = reg.get_tools_for("00010")
        tool_names = [t.name for t in tools]
        assert "deploy" in tool_names

    @patch("onemancompany.core.state.company_state")
    def test_role_tools_non_matching_role(self, mock_state):
        reg = self._build_registry()
        emp = _make_employee("00010", role="Designer")
        mock_state.employees = {"00010": emp}

        tools = reg.get_tools_for("00010")
        tool_names = [t.name for t in tools]
        assert "deploy" not in tool_names

    @patch("onemancompany.core.state.company_state")
    def test_asset_tools_unrestricted(self, mock_state):
        reg = self._build_registry()
        emp = _make_employee("00010", role="Marketing")
        mock_state.employees = {"00010": emp}

        tools = reg.get_tools_for("00010")
        tool_names = [t.name for t in tools]
        assert "gmail" in tool_names

    @patch("onemancompany.core.state.company_state")
    def test_asset_tools_restricted_allowed(self, mock_state):
        reg = self._build_registry()
        emp = _make_employee("00007", role="Engineer")
        mock_state.employees = {"00007": emp}

        tools = reg.get_tools_for("00007")
        tool_names = [t.name for t in tools]
        assert "roblox" in tool_names

    @patch("onemancompany.core.state.company_state")
    def test_asset_tools_restricted_denied(self, mock_state):
        reg = self._build_registry()
        emp = _make_employee("00010", role="Engineer")
        mock_state.employees = {"00010": emp}

        tools = reg.get_tools_for("00010")
        tool_names = [t.name for t in tools]
        assert "roblox" not in tool_names

    @patch("onemancompany.core.state.company_state")
    def test_unknown_employee_returns_empty(self, mock_state):
        reg = self._build_registry()
        mock_state.employees = {}

        tools = reg.get_tools_for("99999")
        assert tools == []

    @patch("onemancompany.core.state.company_state")
    def test_full_access_engineer(self, mock_state):
        """An engineer with bash permission should get base + gated + role + unrestricted asset tools."""
        reg = self._build_registry()
        emp = _make_employee("00010", role="Engineer", tool_permissions=["bash"])
        mock_state.employees = {"00010": emp}

        tools = reg.get_tools_for("00010")
        tool_names = [t.name for t in tools]
        assert "list_colleagues" in tool_names
        assert "bash" in tool_names
        assert "deploy" in tool_names
        assert "gmail" in tool_names
        assert "roblox" not in tool_names  # restricted to 00007


# ---------------------------------------------------------------------------
# ToolRegistry — load_asset_tools
# ---------------------------------------------------------------------------

class TestLoadAssetTools:
    def _make_tool_dir(self, base_dir: Path, name: str, tool_yaml: dict, py_code: str) -> Path:
        """Create a tool folder with tool.yaml and {name}.py."""
        tool_dir = base_dir / name
        tool_dir.mkdir(parents=True, exist_ok=True)
        with open(tool_dir / "tool.yaml", "w") as f:
            yaml.dump(tool_yaml, f)
        (tool_dir / f"{name}.py").write_text(py_code, encoding="utf-8")
        return tool_dir

    def test_load_asset_tools_discovers_modules(self, tmp_path):
        from onemancompany.core.tool_registry import ToolRegistry

        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()

        py_code = """
from langchain_core.tools import tool

@tool
def my_asset_tool(query: str) -> str:
    \"\"\"A test asset tool.\"\"\"
    return "result"
"""
        self._make_tool_dir(
            tools_dir, "my_asset",
            {"id": "my_asset", "name": "My Asset", "type": "langchain_module"},
            py_code,
        )

        reg = ToolRegistry()
        reg.load_asset_tools(tools_dir)

        assert "my_asset_tool" in reg.all_tool_names()
        meta = reg.get_meta("my_asset_tool")
        assert meta.category == "asset"
        assert meta.source == "asset"

    def test_load_asset_tools_with_allowed_users(self, tmp_path):
        from onemancompany.core.tool_registry import ToolRegistry

        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()

        py_code = """
from langchain_core.tools import tool

@tool
def restricted_tool(x: str) -> str:
    \"\"\"Restricted tool.\"\"\"
    return x
"""
        self._make_tool_dir(
            tools_dir, "restricted",
            {"id": "restricted", "name": "Restricted", "type": "langchain_module", "allowed_users": ["00007"]},
            py_code,
        )

        reg = ToolRegistry()
        reg.load_asset_tools(tools_dir)

        meta = reg.get_meta("restricted_tool")
        assert meta is not None
        assert meta.allowed_users == ["00007"]

    def test_load_asset_tools_skips_non_langchain_module(self, tmp_path):
        from onemancompany.core.tool_registry import ToolRegistry

        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()

        tool_dir = tools_dir / "template_tool"
        tool_dir.mkdir()
        with open(tool_dir / "tool.yaml", "w") as f:
            yaml.dump({"id": "template_tool", "name": "Template", "type": "template"}, f)

        reg = ToolRegistry()
        reg.load_asset_tools(tools_dir)

        assert "template_tool" not in reg.all_tool_names()

    def test_load_asset_tools_nonexistent_dir(self, tmp_path):
        from onemancompany.core.tool_registry import ToolRegistry

        tools_dir = tmp_path / "nonexistent"

        reg = ToolRegistry()
        reg.load_asset_tools(tools_dir)  # should not raise

        assert reg.all_tool_names() == []

    def test_load_asset_tools_handles_import_error(self, tmp_path):
        from onemancompany.core.tool_registry import ToolRegistry

        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()

        self._make_tool_dir(
            tools_dir, "broken",
            {"id": "broken", "name": "Broken", "type": "langchain_module"},
            "def bad syntax here!!!",
        )

        reg = ToolRegistry()
        reg.load_asset_tools(tools_dir)  # should log warning, not raise

        assert "broken" not in reg.all_tool_names()

    def test_load_asset_tools_collects_multiple_tools(self, tmp_path):
        from onemancompany.core.tool_registry import ToolRegistry

        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()

        py_code = """
from langchain_core.tools import tool

@tool
def tool_alpha(x: str) -> str:
    \"\"\"Alpha.\"\"\"
    return x

@tool
def tool_beta(x: str) -> str:
    \"\"\"Beta.\"\"\"
    return x
"""
        self._make_tool_dir(
            tools_dir, "multi",
            {"id": "multi", "name": "Multi", "type": "langchain_module"},
            py_code,
        )

        reg = ToolRegistry()
        reg.load_asset_tools(tools_dir)

        names = reg.all_tool_names()
        assert "tool_alpha" in names
        assert "tool_beta" in names


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

class TestModuleSingleton:
    def test_singleton_exists(self):
        from onemancompany.core.tool_registry import tool_registry

        assert tool_registry is not None

    def test_singleton_is_tool_registry(self):
        from onemancompany.core.tool_registry import ToolRegistry, tool_registry

        assert isinstance(tool_registry, ToolRegistry)
