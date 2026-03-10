"""MCP server — thin bridge from ToolRegistry to MCP protocol.

Spawned as a stdio subprocess per Claude CLI session. Reads OMC_EMPLOYEE_ID
from env, queries the unified ToolRegistry for permitted tools, and exposes
them as MCP tools. Each tool call is proxied to the backend via HTTP.

Environment variables (set by config_builder at launch time):
  OMC_EMPLOYEE_ID  -- the employee running this session
  OMC_TASK_ID      -- current task ID on the employee's board
  OMC_PROJECT_ID   -- project ID (convenience)
  OMC_PROJECT_DIR  -- project workspace path
  OMC_SERVER_URL   -- backend URL (default http://localhost:8000)
"""

from __future__ import annotations

import inspect
import json
import os

import httpx
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Config from environment
# ---------------------------------------------------------------------------

EMPLOYEE_ID = os.environ.get("OMC_EMPLOYEE_ID", "")
TASK_ID = os.environ.get("OMC_TASK_ID", "")
PROJECT_ID = os.environ.get("OMC_PROJECT_ID", "")
PROJECT_DIR = os.environ.get("OMC_PROJECT_DIR", "")
SERVER_URL = os.environ.get("OMC_SERVER_URL", "http://localhost:8000")

# ---------------------------------------------------------------------------
# HTTP bridge — call the backend's generic tool-call endpoint
# ---------------------------------------------------------------------------

_client: httpx.Client | None = None


def _get_client() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(base_url=SERVER_URL, timeout=660.0)
    return _client


def _call_tool(tool_name: str, args: dict) -> str:
    """Call a tool on the backend via the internal API. Returns JSON string."""
    resp = _get_client().post(
        "/api/internal/tool-call",
        json={
            "employee_id": EMPLOYEE_ID,
            "task_id": TASK_ID,
            "tool_name": tool_name,
            "args": args,
        },
    )
    if resp.status_code != 200:
        return json.dumps({"status": "error", "message": f"HTTP {resp.status_code}: {resp.text[:500]}"})
    return json.dumps(resp.json(), ensure_ascii=False)


# ---------------------------------------------------------------------------
# Dynamic tool registration from ToolRegistry
# ---------------------------------------------------------------------------

_TYPE_MAP = {
    "integer": int, "number": float, "boolean": bool,
    "array": list, "object": dict, "string": str,
}

_TYPE_DEFAULTS = {
    int: 0, float: 0.0, bool: False,
    list: list, dict: dict, str: "",
}


def _register_mcp_tool(mcp: FastMCP, tool) -> None:
    """Register a single LangChain StructuredTool as an MCP tool.

    FastMCP inspects function signatures (not just __annotations__) to
    determine parameter names, types, and required/optional status.
    We build a proper signature with named keyword-only params so that
    FastMCP generates the correct JSON schema for Claude CLI.
    """
    schema = tool.args_schema.model_json_schema() if tool.args_schema else {}
    properties = schema.get("properties", {})
    required = set(schema.get("required", []))
    tool_name = tool.name
    param_names = list(properties.keys())

    def make_handler(name: str, params: list[str]):
        async def handler(**kwargs) -> str:
            args = {k: v for k, v in kwargs.items() if k in params}
            return _call_tool(name, args)

        handler.__name__ = name
        handler.__doc__ = tool.description

        # Build proper signature so FastMCP sees named params + defaults
        sig_params = []
        annotations = {}
        for p in params:
            prop = properties.get(p, {})
            py_type = _TYPE_MAP.get(prop.get("type", "string"), str)
            annotations[p] = py_type

            if p in required:
                sig_params.append(
                    inspect.Parameter(p, inspect.Parameter.KEYWORD_ONLY, annotation=py_type)
                )
            else:
                default = prop.get("default", _TYPE_DEFAULTS.get(py_type, ""))
                sig_params.append(
                    inspect.Parameter(p, inspect.Parameter.KEYWORD_ONLY, default=default, annotation=py_type)
                )

        annotations["return"] = str
        handler.__annotations__ = annotations
        handler.__signature__ = inspect.Signature(sig_params, return_annotation=str)
        return handler

    fn = make_handler(tool_name, param_names)
    mcp.tool()(fn)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main():
    """Build MCP server dynamically from ToolRegistry."""
    # Import agent modules to trigger tool registration into registry
    from onemancompany.agents import common_tools as _  # noqa: F401
    from onemancompany.agents import coo_agent as _  # noqa: F401
    from onemancompany.agents import cso_agent as _  # noqa: F401
    from onemancompany.agents import hr_agent as _  # noqa: F401
    from onemancompany.agents import tree_tools as _  # noqa: F401
    from onemancompany.core.tool_registry import tool_registry

    # Load asset tools (gmail, roblox, etc.)
    tool_registry.load_asset_tools()

    mcp = FastMCP("onemancompany")

    for tool in tool_registry.get_tools_for(EMPLOYEE_ID):
        _register_mcp_tool(mcp, tool)

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
