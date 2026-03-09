"""Unified tool registry — single source of truth for all LangChain tools.

Every tool (base, gated, role-specific, asset) is registered here with
metadata that drives per-employee permission filtering.

Usage:
    from onemancompany.core.tool_registry import tool_registry, ToolMeta

    tool_registry.register(my_tool, ToolMeta(name="my_tool", category="base"))
    tools = tool_registry.get_tools_for("00010")
"""

from __future__ import annotations

from dataclasses import dataclass
from loguru import logger





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
        from onemancompany.core.state import company_state

        emp = company_state.employees.get(employee_id)
        if emp is None:
            logger.warning("get_tools_for: employee %s not found", employee_id)
            return []

        result = []
        for name, tool in self._tools.items():
            meta = self._meta[name]
            if self._is_allowed(meta, emp):
                result.append(tool)
        return result

    @staticmethod
    def _is_allowed(meta: ToolMeta, emp: object) -> bool:
        """Check whether an employee is allowed to use a tool based on its category."""
        category = meta.category

        if category == "base":
            return True

        if category == "gated":
            return meta.name in (emp.tool_permissions or [])

        if category == "role":
            if meta.allowed_roles is None:
                return True
            return emp.role in meta.allowed_roles

        if category == "asset":
            if meta.allowed_users is None:
                return True
            return emp.id in meta.allowed_users

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

            tool_yaml_path = entry / "tool.yaml"
            if not tool_yaml_path.exists():
                continue

            with open(tool_yaml_path) as f:
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

            # Extract allowed_users from tool.yaml
            allowed_users = tool_conf.get("allowed_users")
            # If key is present but value is empty list or null, treat as restricted-to-nobody
            # If key is absent, allowed_users stays None (unrestricted)

            for attr_name in dir(mod):
                attr = getattr(mod, attr_name)
                if isinstance(attr, BaseTool):
                    meta = ToolMeta(
                        name=attr.name,
                        category="asset",
                        allowed_users=allowed_users,
                        source="asset",
                    )
                    self.register(attr, meta)
                    logger.debug("Registered asset tool: %s (from %s)", attr.name, folder_name)


# Module-level singleton
tool_registry = ToolRegistry()
