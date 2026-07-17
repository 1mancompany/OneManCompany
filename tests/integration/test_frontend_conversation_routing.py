"""Pytest wrapper for the node-based frontend conversation-routing test.

Regression guard: PRODUCT planning conversations were not routed to the
CEO terminal because the conversation_message handler only matched
'oneonone' / 'ea_chat'. EA replies arrived via WebSocket but never
rendered. The accompanying node test asserts both the source-level
filter and a behavioral mirror of the routing predicate.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_SCRIPT = REPO_ROOT / "tests" / "frontend" / "test_conversation_routing.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_frontend_conversation_routing() -> None:
    result = subprocess.run(
        ["node", str(TEST_SCRIPT)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"frontend routing test failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
