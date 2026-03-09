"""Build MCP config for Claude CLI sessions.

Generates a JSON config dict that tells Claude CLI to spawn the
OneManCompany MCP server as a stdio subprocess with the right
environment variables for tool context.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from onemancompany.core.config import EMPLOYEES_DIR, FOUNDING_IDS, employee_configs


def _resolve_tool_list(employee_id: str) -> list[str]:
    """Determine which tools an employee is allowed to use."""
    from onemancompany.agents.common_tools import BASE_TOOLS, GATED_TOOLS

    # Start with base tools (always available)
    tools = [getattr(fn, "name", None) or fn.__name__ for fn in BASE_TOOLS]

    # Founding employees get all tools
    if employee_id in FOUNDING_IDS:
        tools.extend(GATED_TOOLS.keys())
        # Also add dispatch_team_tasks and manage_tool_access
        tools.append("dispatch_team_tasks")
        tools.append("manage_tool_access")
    else:
        # Regular employees: check tool_permissions
        cfg = employee_configs.get(employee_id)
        if cfg and cfg.tool_permissions:
            for name in cfg.tool_permissions:
                if name in GATED_TOOLS:
                    tools.append(name)

    return sorted(set(tools))


def build_mcp_config(
    employee_id: str,
    task_id: str = "",
    project_id: str = "",
    project_dir: str = "",
    server_url: str = "http://localhost:8000",
) -> dict:
    """Build MCP config dict for Claude CLI.

    Returns a dict matching Claude's ``--mcp-config`` JSON format.
    """
    tools = _resolve_tool_list(employee_id)
    python_path = sys.executable  # Use the same Python as the running server

    return {
        "mcpServers": {
            "onemancompany": {
                "command": python_path,
                "args": ["-m", "onemancompany.mcp.server"],
                "env": {
                    "OMC_EMPLOYEE_ID": employee_id,
                    "OMC_TASK_ID": task_id,
                    "OMC_PROJECT_ID": project_id,
                    "OMC_PROJECT_DIR": project_dir,
                    "OMC_SERVER_URL": server_url,
                    "OMC_TOOLS": ",".join(tools),
                },
            },
        },
    }


def write_mcp_config(
    employee_id: str,
    task_id: str = "",
    project_id: str = "",
    project_dir: str = "",
    server_url: str = "http://localhost:8000",
) -> Path:
    """Build and write MCP config to the employee's directory.

    Returns the path to the written config file.
    """
    config = build_mcp_config(employee_id, task_id, project_id, project_dir, server_url)
    config_path = EMPLOYEES_DIR / employee_id / "mcp_config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return config_path
