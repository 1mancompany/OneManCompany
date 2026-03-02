"""Example custom tool for the coding talent.

Each custom tool file exports a LangChain @tool function.
The function name must match the entry in manifest.yaml's custom_tools list.
"""

from __future__ import annotations

from langchain_core.tools import tool


@tool
def custom_build(project_dir: str, build_command: str = "python setup.py build") -> dict:
    """Run a build command in the sandbox for the given project directory.

    Args:
        project_dir: Path to the project directory inside the sandbox.
        build_command: The build command to execute (default: python setup.py build).

    Returns:
        A dict with 'status' and 'output' keys.
    """
    # Placeholder — actual implementation would delegate to the sandbox tool
    return {
        "status": "not_implemented",
        "output": f"Would run '{build_command}' in {project_dir}. "
        "Wire this up to sandbox_run_command for real execution.",
    }
