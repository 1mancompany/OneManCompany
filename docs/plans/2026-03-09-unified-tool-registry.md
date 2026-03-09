# Unified Tool Registry Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Unify internal tools and asset tools into a single ToolRegistry with unified permission filtering, so all agents (company-hosted and self-hosted) get tools from one source.

**Architecture:** New `ToolRegistry` singleton holds all tools with `ToolMeta` metadata. Internal tools register at module import time. Asset tools loaded from `company/assets/tools/`. Single `get_tools_for(employee_id)` entry point replaces all hardcoded tool lists. MCP server becomes a thin registry-to-MCP bridge.

**Tech Stack:** Python, LangChain StructuredTool, FastMCP

---

### Task 1: Create ToolRegistry Core

**Files:**
- Create: `src/onemancompany/core/tool_registry.py`
- Create: `tests/unit/core/test_tool_registry.py`

**Step 1: Write the failing tests**

```python
"""Tests for unified tool registry."""
import pytest
from unittest.mock import MagicMock, patch
from langchain_core.tools import StructuredTool, tool


@tool
def _dummy_read(path: str) -> str:
    """Read a file."""
    return "content"


@tool
def _dummy_bash(command: str) -> str:
    """Run bash."""
    return "output"


@tool
def _dummy_gmail_send(to: str, body: str) -> str:
    """Send email."""
    return "sent"


@tool
def _dummy_search_candidates(query: str) -> str:
    """Search candidates."""
    return "results"


class TestToolMeta:
    def test_base_category(self):
        from onemancompany.core.tool_registry import ToolMeta
        meta = ToolMeta(name="read", category="base")
        assert meta.category == "base"
        assert meta.allowed_roles is None
        assert meta.allowed_users is None

    def test_role_category(self):
        from onemancompany.core.tool_registry import ToolMeta
        meta = ToolMeta(name="search_candidates", category="role", allowed_roles=["HR"])
        assert meta.allowed_roles == ["HR"]


class TestToolRegistry:
    def setup_method(self):
        from onemancompany.core.tool_registry import ToolRegistry
        self.registry = ToolRegistry()

    def test_register_internal(self):
        from onemancompany.core.tool_registry import ToolMeta
        self.registry.register(_dummy_read, ToolMeta(name="read", category="base"))
        assert "read" in self.registry.all_tool_names()

    def test_get_tools_base_always_included(self):
        from onemancompany.core.tool_registry import ToolMeta
        self.registry.register(_dummy_read, ToolMeta(name="read", category="base"))

        mock_emp = MagicMock()
        mock_emp.id = "00099"
        mock_emp.role = "Engineer"
        mock_emp.tool_permissions = []

        with patch("onemancompany.core.tool_registry.company_state") as mock_cs:
            mock_cs.employees = {"00099": mock_emp}
            tools = self.registry.get_tools_for("00099")
        assert any(t.name == "read" for t in tools)

    def test_get_tools_gated_requires_permission(self):
        from onemancompany.core.tool_registry import ToolMeta
        self.registry.register(_dummy_bash, ToolMeta(name="bash", category="gated"))

        mock_emp = MagicMock()
        mock_emp.id = "00099"
        mock_emp.role = "Engineer"
        mock_emp.tool_permissions = []

        with patch("onemancompany.core.tool_registry.company_state") as mock_cs:
            mock_cs.employees = {"00099": mock_emp}
            tools = self.registry.get_tools_for("00099")
        assert not any(t.name == "bash" for t in tools)

    def test_get_tools_gated_with_permission(self):
        from onemancompany.core.tool_registry import ToolMeta
        self.registry.register(_dummy_bash, ToolMeta(name="bash", category="gated"))

        mock_emp = MagicMock()
        mock_emp.id = "00099"
        mock_emp.role = "Engineer"
        mock_emp.tool_permissions = ["bash"]

        with patch("onemancompany.core.tool_registry.company_state") as mock_cs:
            mock_cs.employees = {"00099": mock_emp}
            tools = self.registry.get_tools_for("00099")
        assert any(t.name == "bash" for t in tools)

    def test_get_tools_role_filter(self):
        from onemancompany.core.tool_registry import ToolMeta
        self.registry.register(
            _dummy_search_candidates,
            ToolMeta(name="search_candidates", category="role", allowed_roles=["HR"]),
        )

        mock_hr = MagicMock()
        mock_hr.id = "00002"
        mock_hr.role = "HR"
        mock_hr.tool_permissions = []

        mock_eng = MagicMock()
        mock_eng.id = "00099"
        mock_eng.role = "Engineer"
        mock_eng.tool_permissions = []

        with patch("onemancompany.core.tool_registry.company_state") as mock_cs:
            mock_cs.employees = {"00002": mock_hr}
            hr_tools = self.registry.get_tools_for("00002")

        with patch("onemancompany.core.tool_registry.company_state") as mock_cs:
            mock_cs.employees = {"00099": mock_eng}
            eng_tools = self.registry.get_tools_for("00099")

        assert any(t.name == "search_candidates" for t in hr_tools)
        assert not any(t.name == "search_candidates" for t in eng_tools)

    def test_get_tools_asset_open(self):
        from onemancompany.core.tool_registry import ToolMeta
        self.registry.register(
            _dummy_gmail_send,
            ToolMeta(name="gmail_send", category="asset", allowed_users=None),
        )

        mock_emp = MagicMock()
        mock_emp.id = "00099"
        mock_emp.role = "EA"
        mock_emp.tool_permissions = []

        with patch("onemancompany.core.tool_registry.company_state") as mock_cs:
            mock_cs.employees = {"00099": mock_emp}
            tools = self.registry.get_tools_for("00099")
        assert any(t.name == "gmail_send" for t in tools)

    def test_get_tools_asset_restricted(self):
        from onemancompany.core.tool_registry import ToolMeta
        self.registry.register(
            _dummy_gmail_send,
            ToolMeta(name="gmail_send", category="asset", allowed_users=["00007"]),
        )

        mock_emp = MagicMock()
        mock_emp.id = "00099"
        mock_emp.role = "EA"
        mock_emp.tool_permissions = []

        with patch("onemancompany.core.tool_registry.company_state") as mock_cs:
            mock_cs.employees = {"00099": mock_emp}
            tools = self.registry.get_tools_for("00099")
        assert not any(t.name == "gmail_send" for t in tools)

    def test_all_tool_names(self):
        from onemancompany.core.tool_registry import ToolMeta
        self.registry.register(_dummy_read, ToolMeta(name="read", category="base"))
        self.registry.register(_dummy_bash, ToolMeta(name="bash", category="gated"))
        names = self.registry.all_tool_names()
        assert "read" in names
        assert "bash" in names
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/core/test_tool_registry.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'onemancompany.core.tool_registry'`

**Step 3: Write the implementation**

```python
"""Unified tool registry — single source of truth for all employee tools.

All tools (internal @tool functions and asset tools from company/assets/tools/)
register here. Agents get their tools via get_tools_for(employee_id) which
applies permission filtering based on category rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from langchain_core.tools import BaseTool

from onemancompany.core.state import company_state


@dataclass
class ToolMeta:
    """Metadata for a registered tool — controls who can access it."""

    name: str
    category: str  # "base" | "gated" | "role" | "asset"
    allowed_roles: list[str] | None = None  # role category: which roles get this tool
    allowed_users: list[str] | None = None  # asset category: None = open to all
    source: str = "internal"  # "internal" | "asset"


class ToolRegistry:
    """Unified tool registry.

    Usage:
        registry = ToolRegistry()
        registry.register(my_tool, ToolMeta(name="my_tool", category="base"))
        tools = registry.get_tools_for("00004")  # returns filtered list
    """

    def __init__(self) -> None:
        self._tools: dict[str, tuple[BaseTool, ToolMeta]] = {}

    def register(self, tool: BaseTool, meta: ToolMeta) -> None:
        """Register a tool with its metadata."""
        if meta.name in self._tools:
            logger.debug("Tool '{}' re-registered (overwrite)", meta.name)
        self._tools[meta.name] = (tool, meta)

    def get_tools_for(self, employee_id: str) -> list[BaseTool]:
        """Return all tools this employee is allowed to use.

        Permission rules by category:
          base  — always included
          gated — included if tool name in employee's tool_permissions
          role  — included if employee's role matches allowed_roles
          asset — included if allowed_users is None (open) or employee_id in allowed_users
        """
        emp = company_state.employees.get(employee_id)
        if not emp:
            logger.warning("get_tools_for: employee {} not found", employee_id)
            return []

        tool_perms = set(emp.tool_permissions) if emp.tool_permissions else set()
        result: list[BaseTool] = []

        for name, (tool, meta) in self._tools.items():
            if meta.category == "base":
                result.append(tool)
            elif meta.category == "gated":
                if name in tool_perms:
                    result.append(tool)
            elif meta.category == "role":
                if meta.allowed_roles and emp.role in meta.allowed_roles:
                    result.append(tool)
            elif meta.category == "asset":
                if meta.allowed_users is None or emp.id in meta.allowed_users:
                    result.append(tool)

        return result

    def get_tool(self, name: str) -> BaseTool | None:
        """Get a single tool by name."""
        entry = self._tools.get(name)
        return entry[0] if entry else None

    def get_meta(self, name: str) -> ToolMeta | None:
        """Get metadata for a tool."""
        entry = self._tools.get(name)
        return entry[1] if entry else None

    def all_tool_names(self) -> list[str]:
        """Return all registered tool names."""
        return list(self._tools.keys())

    def load_asset_tools(self) -> None:
        """Scan company/assets/tools/ and register all asset tools.

        For each tool directory with a .py file:
        1. Import the module
        2. Collect all BaseTool instances
        3. Register each with category="asset" and allowed_users from tool.yaml
        """
        import importlib.util

        import yaml
        from langchain_core.tools import BaseTool as _BaseTool

        from onemancompany.core.config import TOOLS_DIR

        if not TOOLS_DIR.exists():
            return

        for tool_dir in sorted(TOOLS_DIR.iterdir()):
            if not tool_dir.is_dir():
                continue
            py_file = tool_dir / f"{tool_dir.name}.py"
            if not py_file.is_file():
                continue

            # Read tool.yaml for allowed_users
            allowed_users = None  # None = open to all
            tool_yaml = tool_dir / "tool.yaml"
            if tool_yaml.exists():
                with open(tool_yaml) as f:
                    meta_data = yaml.safe_load(f) or {}
                if "allowed_users" in meta_data:
                    allowed_users = meta_data["allowed_users"] or []

            # Import module and collect BaseTool instances
            try:
                spec = importlib.util.spec_from_file_location(
                    f"asset_tool_{tool_dir.name}", str(py_file)
                )
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                for attr_name in dir(mod):
                    attr = getattr(mod, attr_name)
                    if isinstance(attr, _BaseTool):
                        self.register(
                            attr,
                            ToolMeta(
                                name=attr.name,
                                category="asset",
                                allowed_users=allowed_users,
                                source="asset",
                            ),
                        )
            except Exception as e:
                logger.warning("Failed to load asset tool {}: {}", tool_dir.name, e)


# Module-level singleton
tool_registry = ToolRegistry()
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/core/test_tool_registry.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add src/onemancompany/core/tool_registry.py tests/unit/core/test_tool_registry.py
git commit -m "feat: add unified ToolRegistry with permission-based filtering"
```

---

### Task 2: Register Internal Tools into Registry

**Files:**
- Modify: `src/onemancompany/agents/common_tools.py` (lines 1656-1714 — replace tool lists with registry calls)
- Modify: `src/onemancompany/agents/coo_agent.py` (register COO-specific tools)
- Modify: `src/onemancompany/agents/hr_agent.py` (register HR-specific tools)
- Modify: `src/onemancompany/agents/cso_agent.py` (register CSO-specific tools)

**Step 1: In `common_tools.py`, replace BASE_TOOLS/GATED_TOOLS/COMMON_TOOLS with registry calls**

Delete the three list definitions (lines 1656-1714) and replace with:

```python
# ---------------------------------------------------------------------------
# Tool registration — register all internal tools into the unified registry
# ---------------------------------------------------------------------------

def _register_all_internal_tools() -> None:
    """Register all internal tools into the global ToolRegistry.

    Called once at import time. Categories:
      base  — available to all employees
      gated — requires tool_permissions grant
    """
    from onemancompany.core.tool_registry import ToolMeta, tool_registry

    _base = [
        list_colleagues, read, ls, write, edit, pull_meeting,
        create_subtask, dispatch_task, dispatch_team_tasks,
        report_to_ceo, request_tool_access,
    ]
    for t in _base:
        tool_registry.register(t, ToolMeta(name=t.name, category="base"))

    _gated = {
        "bash": bash,
        "use_tool": use_tool,
        "set_acceptance_criteria": set_acceptance_criteria,
        "accept_project": accept_project,
        "ea_review_project": ea_review_project,
        "set_project_budget": set_project_budget,
        "save_project_plan": save_project_plan,
        "manage_tool_access": manage_tool_access,
        "set_cron": set_cron,
        "stop_cron_job": stop_cron_job,
        "setup_webhook": setup_webhook,
        "remove_webhook": remove_webhook,
        "list_automations": list_automations,
    }
    for name, t in _gated.items():
        tool_registry.register(t, ToolMeta(name=name, category="gated"))

    # Sandbox tools
    for t in SANDBOX_TOOLS:
        tool_registry.register(t, ToolMeta(name=t.name, category="gated"))


_register_all_internal_tools()
```

Note: `dispatch_team_tasks` moves from COMMON_TOOLS-only to `base` — it was already available to self-hosted employees via MCP. `manage_tool_access` moves to `gated` — only COO should use it, controlled by `tool_permissions`.

**Step 2: In `coo_agent.py`, register COO-specific tools**

After the tool function definitions (after `deposit_company_knowledge`, around line 895), add:

```python
def _register_coo_tools() -> None:
    from onemancompany.core.tool_registry import ToolMeta, tool_registry

    for t in [
        register_asset, remove_tool, list_tools,
        grant_tool_access, revoke_tool_access,
        list_assets, list_meeting_rooms, book_meeting_room,
        release_meeting_room, add_meeting_room,
        request_hiring, deposit_company_knowledge,
    ]:
        tool_registry.register(t, ToolMeta(name=t.name, category="role", allowed_roles=["COO"]))

_register_coo_tools()
```

**Step 3: In `hr_agent.py`, register HR-specific tools**

After imports (around line 112), replace `HIRING_TOOLS = ...` with:

```python
def _register_hr_tools() -> None:
    from onemancompany.core.tool_registry import ToolMeta, tool_registry

    for t in [search_candidates, list_open_positions]:
        tool_registry.register(t, ToolMeta(name=t.name, category="role", allowed_roles=["HR"]))

_register_hr_tools()
```

Delete `HIRING_TOOLS` line.

**Step 4: In `cso_agent.py`, register CSO-specific tools**

After tool function definitions (around line 208), add:

```python
def _register_cso_tools() -> None:
    from onemancompany.core.tool_registry import ToolMeta, tool_registry

    for t in [list_sales_tasks, review_contract, complete_delivery, settle_task]:
        tool_registry.register(t, ToolMeta(name=t.name, category="role", allowed_roles=["CSO"]))

_register_cso_tools()
```

**Step 5: Verify compilation**

Run: `.venv/bin/python -c "from onemancompany.agents.common_tools import _register_all_internal_tools; from onemancompany.agents.coo_agent import COOAgent; from onemancompany.agents.hr_agent import HRAgent; print('OK')"`
Expected: `OK`

**Step 6: Commit**

```bash
git add src/onemancompany/agents/common_tools.py src/onemancompany/agents/coo_agent.py src/onemancompany/agents/hr_agent.py src/onemancompany/agents/cso_agent.py
git commit -m "feat: register all internal tools into unified ToolRegistry"
```

---

### Task 3: Wire Agents to Use Registry

**Files:**
- Modify: `src/onemancompany/agents/ea_agent.py:80-84`
- Modify: `src/onemancompany/agents/hr_agent.py:119-123`
- Modify: `src/onemancompany/agents/coo_agent.py:901-918`
- Modify: `src/onemancompany/agents/cso_agent.py:210-219`
- Modify: `src/onemancompany/agents/base.py:605-634`

**Step 1: Update all agent `__init__` methods to use registry**

Each agent class `__init__` becomes the same pattern:

```python
# EAAgent.__init__
def __init__(self) -> None:
    from onemancompany.core.tool_registry import tool_registry
    self._agent = create_react_agent(
        model=make_llm(self.employee_id),
        tools=tool_registry.get_tools_for(self.employee_id),
    )

# HRAgent.__init__ — identical pattern
# COOAgent.__init__ — identical pattern
# CSOAgent.__init__ — identical pattern
```

For `EmployeeAgent` (base.py GenericAgentRunner), replace lines 605-634:

```python
def __init__(self, employee_id: str) -> None:
    from onemancompany.core.tool_registry import tool_registry

    self.employee_id = employee_id
    emp = company_state.employees.get(employee_id)
    self.role = emp.role if emp else "Employee"

    all_tools = tool_registry.get_tools_for(employee_id)

    # Track which gated tools are authorized/unauthorized (for prompt injection)
    self._authorized_tool_names: list[str] = [t.name for t in all_tools]
    self._unauthorized_tool_names: list[str] = []
    # Compute unauthorized by checking what gated tools exist but weren't granted
    for name in tool_registry.all_tool_names():
        meta = tool_registry.get_meta(name)
        if meta and meta.category == "gated" and name not in {t.name for t in all_tools}:
            self._unauthorized_tool_names.append(name)

    self._agent = create_react_agent(
        model=make_llm(employee_id),
        tools=all_tools,
    )
```

**Step 2: Remove old imports**

In `ea_agent.py`: remove `from onemancompany.agents.common_tools import COMMON_TOOLS`
In `hr_agent.py`: remove `HIRING_TOOLS` reference, remove `from onemancompany.agents.common_tools import COMMON_TOOLS`
In `coo_agent.py`: remove `from onemancompany.agents.common_tools import COMMON_TOOLS`
In `cso_agent.py`: remove `from onemancompany.agents.common_tools import COMMON_TOOLS`
In `base.py`: remove `from onemancompany.agents.common_tools import BASE_TOOLS, GATED_TOOLS` and `from onemancompany.core.config import load_employee_custom_tools`

**Step 3: Verify compilation**

Run: `.venv/bin/python -c "from onemancompany.agents.ea_agent import EAAgent; from onemancompany.agents.hr_agent import HRAgent; from onemancompany.agents.coo_agent import COOAgent; print('OK')"`
Expected: `OK`

**Step 4: Run existing tests**

Run: `.venv/bin/python -m pytest tests/unit/ -v --timeout=30`
Expected: All pass (some may need mock updates — fix in next task)

**Step 5: Commit**

```bash
git add src/onemancompany/agents/ea_agent.py src/onemancompany/agents/hr_agent.py src/onemancompany/agents/coo_agent.py src/onemancompany/agents/cso_agent.py src/onemancompany/agents/base.py
git commit -m "feat: all agents get tools from unified ToolRegistry"
```

---

### Task 4: Load Asset Tools at Startup

**Files:**
- Modify: `src/onemancompany/main.py` (call `tool_registry.load_asset_tools()` at startup)
- Modify: `src/onemancompany/core/state.py` (call in hot-reload path if applicable)
- Modify: `src/onemancompany/core/config.py` (delete `load_employee_custom_tools()`)

**Step 1: In `main.py`, add registry initialization after asset loading**

Find the line that says `# Eagerly load assets (tools, meeting rooms) into company_state` (around line 349). After that block, add:

```python
# Register asset tools from company/assets/tools/ into unified registry
from onemancompany.core.tool_registry import tool_registry
tool_registry.load_asset_tools()
```

**Step 2: In hot-reload handler in `state.py`, reload asset tools**

Find the assets reload section (around line 626, `# --- 4. Reload assets`). After `load_assets()`, add:

```python
from onemancompany.core.tool_registry import tool_registry
tool_registry.load_asset_tools()
```

**Step 3: Delete `load_employee_custom_tools()` from `config.py`**

Remove the function at lines 774-844 and any imports of it elsewhere.

**Step 4: Delete employee `tools/manifest.yaml` files**

```bash
rm -f company/human_resource/employees/00004/tools/manifest.yaml
rm -f company/human_resource/employees/00006/tools/manifest.yaml
rm -f company/human_resource/employees/00007/tools/manifest.yaml
```

**Step 5: Verify startup**

Run: `.venv/bin/python -c "from onemancompany.core.tool_registry import tool_registry; tool_registry.load_asset_tools(); print(tool_registry.all_tool_names())"`
Expected: Shows gmail tools + any other asset tools

**Step 6: Commit**

```bash
git add src/onemancompany/main.py src/onemancompany/core/state.py src/onemancompany/core/config.py
git rm company/human_resource/employees/00004/tools/manifest.yaml company/human_resource/employees/00006/tools/manifest.yaml company/human_resource/employees/00007/tools/manifest.yaml
git commit -m "feat: load asset tools into registry at startup, remove manifest.yaml"
```

---

### Task 5: Rewrite MCP Server as Registry Bridge

**Files:**
- Rewrite: `src/onemancompany/tools/mcp/server.py`
- Modify: `src/onemancompany/tools/mcp/config_builder.py` (remove OMC_TOOLS)

**Step 1: Rewrite `server.py`**

Replace entire file with:

```python
"""MCP server — thin bridge from ToolRegistry to MCP protocol.

Spawned as a stdio subprocess per Claude CLI session. Reads OMC_EMPLOYEE_ID
from env, queries the unified ToolRegistry, and exposes all permitted tools
as MCP tools. Each tool call is proxied to the backend via HTTP.
"""

from __future__ import annotations

import json
import os

import httpx
from mcp.server.fastmcp import FastMCP

EMPLOYEE_ID = os.environ.get("OMC_EMPLOYEE_ID", "")
TASK_ID = os.environ.get("OMC_TASK_ID", "")
PROJECT_ID = os.environ.get("OMC_PROJECT_ID", "")
PROJECT_DIR = os.environ.get("OMC_PROJECT_DIR", "")
SERVER_URL = os.environ.get("OMC_SERVER_URL", "http://localhost:8000")


def _call_tool(tool_name: str, args: dict) -> str:
    """Proxy a tool call to the backend via HTTP."""
    resp = httpx.post(
        f"{SERVER_URL}/api/internal/tool-call",
        json={
            "employee_id": EMPLOYEE_ID,
            "task_id": TASK_ID,
            "tool_name": tool_name,
            "args": args,
        },
        timeout=300,
    )
    data = resp.json()
    if isinstance(data, dict) and data.get("status") == "error":
        return f"Error: {data.get('message', 'unknown error')}"
    return json.dumps(data, ensure_ascii=False, indent=2) if not isinstance(data, str) else data


def main() -> None:
    """Build MCP server dynamically from ToolRegistry."""
    # Import registry — triggers internal tool registration via module imports
    from onemancompany.agents import common_tools as _  # noqa: F401 — registers base/gated tools
    from onemancompany.agents import coo_agent as _  # noqa: F401 — registers COO tools
    from onemancompany.agents import cso_agent as _  # noqa: F401 — registers CSO tools
    from onemancompany.agents import hr_agent as _  # noqa: F401 — registers HR tools
    from onemancompany.core.tool_registry import tool_registry

    # Load asset tools (gmail, roblox, etc.)
    tool_registry.load_asset_tools()

    mcp = FastMCP("onemancompany")

    for tool in tool_registry.get_tools_for(EMPLOYEE_ID):
        _register_mcp_tool(mcp, tool)

    mcp.run(transport="stdio")


def _register_mcp_tool(mcp: FastMCP, tool) -> None:
    """Register a single LangChain tool as an MCP tool."""
    schema = tool.args_schema.schema() if tool.args_schema else {}
    properties = schema.get("properties", {})
    required = schema.get("required", [])

    # Build parameter descriptions for the MCP tool
    param_names = list(properties.keys())

    # Create a closure that captures tool.name
    tool_name = tool.name

    def make_handler(name: str, params: list[str]):
        async def handler(**kwargs) -> str:
            # Filter to only declared params
            args = {k: v for k, v in kwargs.items() if k in params}
            return _call_tool(name, args)
        # Set function metadata for FastMCP
        handler.__name__ = name
        handler.__doc__ = tool.description
        # Build annotations from schema
        annotations = {}
        for p in params:
            prop = properties.get(p, {})
            ptype = prop.get("type", "string")
            if ptype == "integer":
                annotations[p] = int
            elif ptype == "number":
                annotations[p] = float
            elif ptype == "boolean":
                annotations[p] = bool
            elif ptype == "array":
                annotations[p] = list
            elif ptype == "object":
                annotations[p] = dict
            else:
                annotations[p] = str
        annotations["return"] = str
        handler.__annotations__ = annotations
        return handler

    fn = make_handler(tool_name, param_names)
    mcp.tool()(fn)


if __name__ == "__main__":
    main()
```

**Step 2: Simplify `config_builder.py` — remove OMC_TOOLS**

In `_resolve_tool_list()` function and any references to `OMC_TOOLS` in `build_mcp_config()` / `write_mcp_config()`:
- Delete `_resolve_tool_list()` function entirely
- Remove `"OMC_TOOLS"` from the env dict in `build_mcp_config()`

**Step 3: Verify MCP server starts**

Run: `OMC_EMPLOYEE_ID=00004 .venv/bin/python -m onemancompany.tools.mcp.server 2>&1 | head -5`
Expected: MCP server starts without errors (will block on stdin)

**Step 4: Commit**

```bash
git add src/onemancompany/tools/mcp/server.py src/onemancompany/tools/mcp/config_builder.py
git commit -m "feat: rewrite MCP server as generic ToolRegistry-to-MCP bridge"
```

---

### Task 6: Clean Up Dead Code and Update Tests

**Files:**
- Modify: `src/onemancompany/agents/common_tools.py` (delete old list definitions if any remain)
- Modify: `src/onemancompany/agents/onboarding.py` (update tool references)
- Modify: `tests/unit/mcp/test_tool_call.py` (update for new registry-based flow)
- Modify: `tests/unit/core/test_agent_loop.py` (update mocks if they reference old tool lists)
- Modify: `tests/unit/api/test_routes.py` (update if it references old tool lists)

**Step 1: Search for remaining references to deleted symbols**

Run: `.venv/bin/python -m pytest tests/unit/ -v --timeout=30 2>&1 | tail -40`

Fix any import errors or test failures that reference:
- `BASE_TOOLS`, `GATED_TOOLS`, `COMMON_TOOLS`, `HIRING_TOOLS`
- `load_employee_custom_tools`
- `OMC_TOOLS`

**Step 2: Update `_ensure_tool_registry()` in routes.py**

The `/api/internal/tool-call` endpoint uses `_ensure_tool_registry()` (routes.py:4028-4051) which builds its own tool dict from `COMMON_TOOLS + GATED_TOOLS`. Replace with:

```python
def _ensure_tool_registry() -> dict[str, callable]:
    """Get tool registry for internal tool calls."""
    from onemancompany.core.tool_registry import tool_registry
    return {name: tool_registry.get_tool(name) for name in tool_registry.all_tool_names()}
```

**Step 3: Update onboarding.py tool references**

In `onboarding.py`, the `register_tool_user` / `unregister_tool_user` functions modify `tool.yaml` `allowed_users`. These stay as-is since asset permission still uses `allowed_users`. But remove any references to `manifest.yaml` custom_tools.

**Step 4: Run full test suite**

Run: `.venv/bin/python -m pytest tests/unit/ -v --timeout=30`
Expected: All pass

**Step 5: Commit**

```bash
git add -u
git commit -m "chore: clean up dead code and update tests for unified tool registry"
```

---

### Task 7: Integration Test — Full Startup

**Step 1: Start the backend**

```bash
lsof -ti:8000 | xargs kill -9 2>/dev/null; sleep 1
.venv/bin/python -m onemancompany.main &
sleep 3
curl -s http://localhost:8000/ -o /dev/null -w "%{http_code}"
```
Expected: `200`

**Step 2: Verify EA has gmail tools**

Check backend logs for tool registration, verify EA agent has gmail tools loaded.

**Step 3: Verify MCP server for self-hosted employee**

```bash
OMC_EMPLOYEE_ID=00007 .venv/bin/python -c "
from onemancompany.core.tool_registry import tool_registry
from onemancompany.agents import common_tools, coo_agent, hr_agent, cso_agent
tool_registry.load_asset_tools()
tools = tool_registry.get_tools_for('00007')
print([t.name for t in tools])
"
```
Expected: Shows base tools + roblox tools (00007 has roblox access)

**Step 4: Commit if any fixes needed**

```bash
git add -u
git commit -m "fix: integration fixes for unified tool registry"
```
