"""Unified tool registry — single source of truth for all tools.

Every tool (base, gated, role-specific, asset) is registered here with
metadata that drives per-employee permission filtering.

All tool execution — whether from LangChain agents or Claude CLI via MCP —
flows through the same ``execute_tool()`` path which handles context setup
and permission checks.

Usage:
    from onemancompany.core.tool_registry import tool_registry, ToolMeta

    tool_registry.register(my_tool, ToolMeta(name="my_tool", category="base"))
    tools = tool_registry.get_proxied_tools_for("00010")
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass

from loguru import logger

from onemancompany.core.config import TOOL_YAML_FILENAME, open_utf





@dataclass
class ToolMeta:
    """Metadata attached to every registered tool."""

    name: str
    category: str  # "base" | "gated" | "role" | "asset"
    allowed_roles: list[str] | None = None
    allowed_users: list[str] | None = None
    source: str = "internal"  # "internal" | "asset"


# Category → checker mapping lives inside ToolRegistry._is_allowed to keep
# the dispatch table close to the logic it governs.


class ToolRegistry:
    """Central registry for all LangChain tools with permission-based filtering."""

    def __init__(self) -> None:
        self._tools: dict[str, object] = {}  # name → tool instance
        self._meta: dict[str, ToolMeta] = {}  # name → metadata

    # ------------------------------------------------------------------
    # Registration & lookup
    # ------------------------------------------------------------------

    def register(self, tool: object, meta: ToolMeta) -> None:
        """Register a tool with its metadata. Overwrites if name already exists."""
        self._tools[meta.name] = tool
        self._meta[meta.name] = meta

    def get_tool(self, name: str) -> object | None:
        """Return a single tool by name, or None if not found."""
        return self._tools.get(name)

    def get_meta(self, name: str) -> ToolMeta | None:
        """Return metadata for a tool, or None if not found."""
        return self._meta.get(name)

    def all_tool_names(self) -> list[str]:
        """Return all registered tool names."""
        return list(self._tools.keys())

    # ------------------------------------------------------------------
    # Permission-based filtering
    # ------------------------------------------------------------------

    def get_tools_for(self, employee_id: str) -> list:
        """Return filtered list of tools an employee is authorized to use.

        Filtering rules by category:
        - base: always included
        - gated: included if tool name in employee's tool_permissions
        - role: included if employee's role in meta.allowed_roles
        - asset: included if allowed_users is None OR employee_id in allowed_users
        """
        from onemancompany.core.store import load_employee

        emp_data = load_employee(employee_id)
        if not emp_data:
            logger.warning("get_tools_for: employee %s not found", employee_id)
            return []

        result = []
        for name, tool in self._tools.items():
            meta = self._meta[name]
            if self._is_allowed(meta, emp_data, employee_id):
                result.append(tool)
        return result

    @staticmethod
    def _is_allowed(meta: ToolMeta, emp_data: dict, employee_id: str) -> bool:
        """Check whether an employee is allowed to use a tool based on its category."""
        category = meta.category

        if category in ("base", "gated"):
            return True

        if category == "role":
            if meta.allowed_roles is None:
                return True
            return emp_data.get("role", "") in meta.allowed_roles

        if category == "asset":
            # Company-provided asset tools: available to all employees
            if meta.source != "talent":
                return True
            # Talent-brought tools: filter by allowed_users/allowed_roles
            if meta.allowed_users is None and meta.allowed_roles is None:
                return True
            if meta.allowed_users and employee_id in meta.allowed_users:
                return True
            if meta.allowed_roles and emp_data.get("role", "") in meta.allowed_roles:
                return True
            return False

        logger.warning("Unknown tool category %r for tool %s", category, meta.name)
        return False

    # ------------------------------------------------------------------
    # Asset tool loading
    # ------------------------------------------------------------------

    def load_asset_tools(self, tools_dir=None) -> None:
        """Scan company/assets/tools/ and register langchain_module tools.

        For each subdirectory with a tool.yaml where type == "langchain_module",
        imports the Python module and collects all BaseTool instances.

        Args:
            tools_dir: Override directory to scan (default: TOOLS_DIR from config).
        """
        if tools_dir is None:
            from onemancompany.core.config import TOOLS_DIR
            tools_dir = TOOLS_DIR

        if not tools_dir.exists():
            logger.debug("Asset tools directory does not exist: %s", tools_dir)
            return

        import importlib.util

        import yaml
        from langchain_core.tools import BaseTool

        for entry in sorted(tools_dir.iterdir()):
            if not entry.is_dir():
                continue

            tool_yaml_path = entry / TOOL_YAML_FILENAME
            if not tool_yaml_path.exists():
                continue

            with open_utf(tool_yaml_path) as f:
                tool_conf = yaml.safe_load(f) or {}

            # Only load Python-based tool modules
            if tool_conf.get("type") != "langchain_module":
                continue

            folder_name = entry.name
            py_file = entry / f"{folder_name}.py"
            if not py_file.is_file():
                logger.debug("No %s.py found in asset tool %s", folder_name, folder_name)
                continue

            # Import the module and collect BaseTool instances
            try:
                spec = importlib.util.spec_from_file_location(
                    f"asset_tool_{folder_name}", str(py_file)
                )
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
            except Exception as exc:
                logger.warning("Failed to import asset tool %s: %s", folder_name, exc)
                continue

            # Extract allowed_users and allowed_roles from tool.yaml
            allowed_users = tool_conf.get("allowed_users")
            allowed_roles = tool_conf.get("allowed_roles")
            # If key is present but value is empty list or null, treat as restricted-to-nobody
            # If key is absent, stays None (unrestricted)

            # Talent-brought tools have source_talent in tool.yaml
            source = "talent" if tool_conf.get("source_talent") else "asset"

            for attr_name in dir(mod):
                attr = getattr(mod, attr_name)
                if isinstance(attr, BaseTool):
                    meta = ToolMeta(
                        name=attr.name,
                        category="asset",
                        allowed_users=allowed_users,
                        allowed_roles=allowed_roles,
                        source=source,
                    )
                    self.register(attr, meta)
                    logger.debug("Registered asset tool: {} (from {})", attr.name, folder_name)


    # ------------------------------------------------------------------
    # Proxied tools — unified execution path for all employee types
    # ------------------------------------------------------------------

    def get_proxied_tools_for(self, employee_id: str) -> list:
        """Return LangChain tools that route through execute_tool().

        Unlike get_tools_for() which returns direct tool instances,
        this returns wrapper tools that go through the unified execution
        path (same as MCP). This ensures consistent context setup
        for both LangChain agents and Claude CLI agents.
        """
        from langchain_core.tools import StructuredTool

        direct_tools = self.get_tools_for(employee_id)
        proxied = []
        for tool in direct_tools:
            tool_name = tool.name

            # Build async wrapper that calls execute_tool
            async def _proxy(emp_id=employee_id, tname=tool_name, **kwargs):
                return await execute_tool(emp_id, tname, kwargs)

            wrapper = StructuredTool.from_function(
                coroutine=_proxy,
                name=tool.name,
                description=tool.description,
                args_schema=tool.args_schema if hasattr(tool, "args_schema") else None,
            )
            proxied.append(wrapper)
        return proxied


# Module-level singleton
tool_registry = ToolRegistry()


# ------------------------------------------------------------------
# Unified tool execution — single path for all tool calls
# ------------------------------------------------------------------

async def execute_tool(employee_id: str, tool_name: str, args: dict) -> dict:
    """Execute a tool with proper context setup.

    This is the single execution path for ALL tool calls, whether from
    LangChain agents (via proxied tools) or Claude CLI (via MCP HTTP bridge).
    """
    from onemancompany.core.vessel import (
        _current_vessel, _current_task_id, employee_manager,
    )

    fn = tool_registry.get_tool(tool_name)
    if not fn:
        return {"status": "error", "message": f"Tool '{tool_name}' not found"}

    # Context vars may already be set by vessel._execute_task for
    # company-hosted agents. For MCP calls they won't be set yet.
    # Only override if not already set.
    vessel_token = None
    try:
        existing_vessel = _current_vessel.get(None)
        if existing_vessel is None and employee_id:
            vessel = employee_manager.get_handle(employee_id)
            if vessel:
                vessel_token = _current_vessel.set(vessel)

        # task_id is set per-tool-call for MCP, per-task for LangChain
        # Don't override if already set by vessel
        existing_task = _current_task_id.get(None)
        if existing_task is None:
            # For MCP calls, task_id comes from args or env — handled by caller
            pass

        # Call the tool
        if hasattr(fn, "ainvoke"):
            result = await fn.ainvoke(args)
        elif hasattr(fn, "invoke"):
            result = fn.invoke(args)
        elif inspect.iscoroutinefunction(fn):
            result = await fn(**args)
        else:
            result = fn(**args)

        # Normalize result
        if isinstance(result, dict):
            return result
        if isinstance(result, list):
            return {"result": result}
        return {"result": str(result)}
    except Exception as e:
        logger.error("Tool '{}' failed: {}", tool_name, e)
        return {"status": "error", "message": str(e)}
    finally:
        if vessel_token is not None:
            _current_vessel.reset(vessel_token)
