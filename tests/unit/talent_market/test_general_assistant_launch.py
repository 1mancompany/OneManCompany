"""Regression tests for the built-in general-assistant launch protocol."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).parents[3]
LAUNCH_SCRIPTS = (
    REPO_ROOT / "src/onemancompany/talent_market/talents/general-assistant/launch.sh",
    REPO_ROOT / "src/onemancompany/talent_market/talents/general-assistant/general-assistant/launch.sh",
)


def _run_launch(
    tmp_path: Path,
    script: Path,
    env_updates: dict[str, str],
    *,
    executor_mode: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a launch script with a fake Python agent that completes immediately."""
    launch_script = tmp_path / "launch.sh"
    shutil.copy2(script, launch_script)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    bare_python = fake_bin / "python"
    bare_python.write_text("#!/bin/sh\necho 'bare python must not run' >&2\nexit 127\n")
    bare_python.chmod(0o755)
    preferred_python = fake_bin / "omc python"
    preferred_python.write_text(
        "#!/bin/sh\n: > \"$INTERPRETER_MARKER_DIR/preferred-used\"\ncat\nprintf '<done>COMPLETE</done>\\n'\n"
    )
    preferred_python.chmod(0o755)
    fallback_python = fake_bin / "python3"
    fallback_python.write_text(
        "#!/bin/sh\n: > \"$INTERPRETER_MARKER_DIR/fallback-used\"\ncat\nprintf '<done>COMPLETE</done>\\n'\n"
    )
    fallback_python.chmod(0o755)

    env = os.environ.copy()
    env.pop("TASK", None)
    env.pop("OMC_TASK_DESCRIPTION", None)
    env.pop("OMC_TASK_DESCRIPTION_FILE", None)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["INTERPRETER_MARKER_DIR"] = str(tmp_path)
    env.update(env_updates)

    command = ["bash", str(launch_script)]
    if executor_mode:
        employee_dir = tmp_path / "employee"
        employee_dir.mkdir()
        env["OMC_EMPLOYEE_ID"] = "00010"
        env["OMC_MAX_ITERATIONS"] = "1"
        env["OMC_PYTHON_EXECUTABLE"] = str(preferred_python)
        command.append(str(employee_dir))
    else:
        env.pop("OMC_EMPLOYEE_ID", None)
        env.pop("OMC_MAX_ITERATIONS", None)
        env.pop("OMC_PYTHON_EXECUTABLE", None)
        command.append("1")

    return subprocess.run(
        command,
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


@pytest.mark.parametrize("script", LAUNCH_SCRIPTS)
def test_launch_reads_subprocess_executor_task_file(tmp_path: Path, script: Path) -> None:
    prompt_file = tmp_path / "task-prompt.txt"
    prompt_file.write_text("task delivered through the executor file")

    result = _run_launch(tmp_path, script, {"OMC_TASK_DESCRIPTION_FILE": str(prompt_file)})

    assert result.returncode == 0, result.stderr
    assert "Task: task delivered through the executor file" in result.stdout


@pytest.mark.parametrize("script", LAUNCH_SCRIPTS)
def test_launch_falls_back_to_direct_task_description(tmp_path: Path, script: Path) -> None:
    result = _run_launch(
        tmp_path,
        script,
        {"OMC_TASK_DESCRIPTION": "task delivered directly in the environment"},
    )

    assert result.returncode == 0, result.stderr
    assert "Task: task delivered directly in the environment" in result.stdout


@pytest.mark.parametrize("script", LAUNCH_SCRIPTS)
def test_launch_uses_executor_python_interpreter(tmp_path: Path, script: Path) -> None:
    result = _run_launch(tmp_path, script, {"OMC_TASK_DESCRIPTION": "use the app interpreter"})

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "preferred-used").exists()
    assert not (tmp_path / "fallback-used").exists()


@pytest.mark.parametrize("script", LAUNCH_SCRIPTS)
def test_launch_preserves_legacy_numeric_iteration_argument(tmp_path: Path, script: Path) -> None:
    result = _run_launch(
        tmp_path,
        script,
        {"TASK": "legacy standalone task"},
        executor_mode=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Max iterations: 1" in result.stdout
    assert (tmp_path / "fallback-used").exists()
