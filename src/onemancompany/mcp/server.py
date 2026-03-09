"""MCP server exposing OneManCompany tools to self-hosted Claude CLI employees.

Runs as a stdio subprocess per Claude CLI session. All tool calls are proxied
to the main FastAPI backend via HTTP, so the MCP server stays stateless.

Environment variables (set by config_builder at launch time):
  OMC_EMPLOYEE_ID  -- the employee running this session
  OMC_TASK_ID      -- current task ID on the employee's board
  OMC_PROJECT_ID   -- project ID (convenience)
  OMC_PROJECT_DIR  -- project workspace path
  OMC_SERVER_URL   -- backend URL (default http://localhost:8000)
  OMC_TOOLS        -- comma-separated list of tool names to register
"""

from __future__ import annotations

import json
import os
import sys

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
TOOLS_CSV = os.environ.get("OMC_TOOLS", "")

# ---------------------------------------------------------------------------
# HTTP bridge — call the backend's generic tool-call endpoint
# ---------------------------------------------------------------------------

_client: httpx.Client | None = None


def _get_client() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(base_url=SERVER_URL, timeout=300.0)
    return _client


def _call_tool(tool_name: str, args: dict) -> dict:
    """Call a tool on the backend via the internal API."""
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
        return {"status": "error", "message": f"HTTP {resp.status_code}: {resp.text[:500]}"}
    return resp.json()


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------
# Each tool is a thin wrapper that forwards to _call_tool.
# Docstrings are important — they become the tool descriptions in Claude CLI.

TOOL_DEFS: dict[str, dict] = {
    "list_colleagues": {
        "desc": "List all employees in the company with their roles, skills, and status.",
        "params": {},
    },
    "save_to_project": {
        "desc": "Save a file to the current project workspace.",
        "params": {"project_dir": "str", "filename": "str", "content": "str"},
    },
    "list_project_workspace": {
        "desc": "List all files in a project workspace directory.",
        "params": {"project_dir": "str"},
    },
    "pull_meeting": {
        "desc": "Initiate a meeting with specific colleagues to discuss a topic.",
        "params": {"topic": "str", "participant_ids": "list[str]", "agenda": "str", "initiator_id": "str"},
    },
    "create_subtask": {
        "desc": "Create a sub-task under the current task.",
        "params": {"description": "str"},
    },
    "dispatch_task": {
        "desc": "Dispatch a task to another employee. The task will be queued and executed autonomously.",
        "params": {"employee_id": "str", "task_description": "str"},
    },
    "dispatch_team_tasks": {
        "desc": "Dispatch multiple tasks to different employees with optional phasing.",
        "params": {"tasks": "list[dict]"},
    },
    "report_to_ceo": {
        "desc": "Send a report to the CEO. Use for status updates, decisions needed, or completed work.",
        "params": {"subject": "str", "report": "str", "action_required": "bool"},
    },
    "request_tool_access": {
        "desc": "Request access to a gated tool.",
        "params": {"tool_name": "str", "reason": "str", "employee_id": "str"},
    },
    "read_file": {
        "desc": "Read a file from the company directory or source code.",
        "params": {"file_path": "str", "employee_id": "str"},
    },
    "list_directory": {
        "desc": "List contents of a directory in the company or source tree.",
        "params": {"dir_path": "str", "employee_id": "str"},
    },
    "propose_file_edit": {
        "desc": "Propose an edit to a company file (requires CEO approval).",
        "params": {"file_path": "str", "new_content": "str", "reason": "str", "proposed_by": "str", "employee_id": "str"},
    },
    "use_tool": {
        "desc": "Use a company tool/equipment (e.g. web_search, code_sandbox).",
        "params": {"tool_name_or_id": "str", "employee_id": "str"},
    },
    "set_acceptance_criteria": {
        "desc": "Set acceptance criteria and responsible officer for the current project.",
        "params": {"criteria": "list[str]", "responsible_officer_id": "str"},
    },
    "accept_project": {
        "desc": "Accept or reject a project deliverable (COO/CSO only).",
        "params": {"accepted": "bool", "notes": "str"},
    },
    "ea_review_project": {
        "desc": "EA review of a project after acceptance (EA only).",
        "params": {"approved": "bool", "review_notes": "str"},
    },
    "set_project_budget": {
        "desc": "Set the estimated budget for the current project.",
        "params": {"budget_usd": "float"},
    },
    "save_project_plan": {
        "desc": "Save a structured project plan.",
        "params": {
            "plan_title": "str", "background": "str", "market_research": "str",
            "goals": "list[str]", "non_goals": "list[str]", "technical_approach": "str",
            "phases": "list[dict]", "team_assignments": "list[dict]",
            "risks": "list[dict]", "acceptance_criteria": "list[str]",
        },
    },
    "manage_tool_access": {
        "desc": "Grant or revoke tool access for an employee (managers only).",
        "params": {"employee_id": "str", "tool_name": "str", "action": "str", "manager_id": "str"},
    },
}


# ---------------------------------------------------------------------------
# Dynamic MCP server construction
# ---------------------------------------------------------------------------

mcp = FastMCP("onemancompany")


def _make_tool_fn(name: str):
    """Create a closure that calls _call_tool for a given tool name."""
    def tool_fn(**kwargs) -> str:
        # Fill in employee_id defaults where the param exists
        if "employee_id" in kwargs and not kwargs["employee_id"]:
            kwargs["employee_id"] = EMPLOYEE_ID
        if "initiator_id" in kwargs and not kwargs["initiator_id"]:
            kwargs["initiator_id"] = EMPLOYEE_ID
        # Fill in project_dir default
        if "project_dir" in kwargs and not kwargs["project_dir"]:
            kwargs["project_dir"] = PROJECT_DIR

        result = _call_tool(name, kwargs)
        return json.dumps(result, ensure_ascii=False)
    tool_fn.__name__ = name
    return tool_fn


def _register_tools():
    """Register MCP tools based on OMC_TOOLS env var."""
    allowed = set(TOOLS_CSV.split(",")) if TOOLS_CSV else set(TOOL_DEFS.keys())

    for name, defn in TOOL_DEFS.items():
        if name not in allowed:
            continue
        fn = _make_tool_fn(name)
        # Build parameter descriptions for the tool
        param_desc = ", ".join(f"{k}: {v}" for k, v in defn["params"].items())
        fn.__doc__ = defn["desc"]
        if param_desc:
            fn.__doc__ += f"\n\nParameters: {param_desc}"

        # Register with FastMCP
        mcp.tool()(fn)


_register_tools()


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main():
    """Run the MCP server on stdio."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
