"""Build MCP config for Claude CLI sessions.

Generates a JSON config dict that tells Claude CLI to spawn the
OneManCompany MCP server as a stdio subprocess with the right
environment variables for tool context.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from onemancompany.core.config import (
    EMPLOYEES_DIR, ENV_OMC_EMPLOYEE_ID, ENV_OMC_PROJECT_DIR,
    ENV_OMC_PROJECT_ID, ENV_OMC_SERVER_URL, ENV_OMC_TASK_ID,
    MCP_CONFIG_FILENAME, TOOLS_DIR, WORKSPACE_DIR_NAME,
    write_text_utf,
)


def build_mcp_config(
    employee_id: str,
    task_id: str = "",
    project_id: str = "",
    project_dir: str = "",
    server_url: str = "http://localhost:8000",
) -> dict:
    """Build MCP config dict for Claude CLI.

    Returns a dict matching Claude's ``--mcp-config`` JSON format.
    Tool permissions are resolved dynamically by the MCP server via ToolRegistry.
    """
    python_path = sys.executable

    servers: dict = {
        "onemancompany": {
            "command": python_path,
            "args": ["-m", "onemancompany.tools.mcp.server"],
            "env": {
                ENV_OMC_EMPLOYEE_ID: employee_id,
                ENV_OMC_TASK_ID: task_id,
                ENV_OMC_PROJECT_ID: project_id,
                ENV_OMC_PROJECT_DIR: project_dir,
                ENV_OMC_SERVER_URL: server_url,
            },
        },
    }

    # Add Gmail MCP server if employee has access and server script exists
    gmail_mcp = TOOLS_DIR / "gmail" / "mcp_server.py"
    if gmail_mcp.exists():
        servers["gmail"] = {
            "command": python_path,
            "args": [str(gmail_mcp)],
        }

    # FastSkills MCP — available to all employees with API key configured
    from onemancompany.core.config import settings
    if settings.skillsmp_api_key:
        emp_dir = EMPLOYEES_DIR / employee_id
        skills_dir = emp_dir / "skills"
        workdir = emp_dir / WORKSPACE_DIR_NAME
        servers["fastskills"] = {
            "command": "uvx",
            "args": [
                "fastskills",
                "--skills-dir", str(skills_dir),
                "--workdir", str(workdir),
            ],
            "env": {
                "SKILLSMP_API_KEY": settings.skillsmp_api_key,
            },
        }

    return {"mcpServers": servers}


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
    config_path = EMPLOYEES_DIR / employee_id / MCP_CONFIG_FILENAME
    config_path.parent.mkdir(parents=True, exist_ok=True)
    write_text_utf(config_path, json.dumps(config, indent=2))
    return config_path
