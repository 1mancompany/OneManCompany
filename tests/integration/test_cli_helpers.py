"""Pytest wrapper for the node-based CLI smoke tests.

The CLI is plain JS (bin/cli.js); these helpers exercise readAppVersion
behavior + source-level invariants that guard against the "@dev stays
stale" regression. Runs via `node tests/cli/test_cli_helpers.js` and
asserts exit 0.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_SCRIPT = REPO_ROOT / "tests" / "cli" / "test_cli_helpers.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_cli_helpers_smoke() -> None:
    result = subprocess.run(
        ["node", str(TEST_SCRIPT)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"node CLI smoke test failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
