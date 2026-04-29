import pytest


@pytest.mark.asyncio
@pytest.mark.timeout(120)
async def test_ceo_assigns_task_full_flow():
    """E2E: CEO assigns task → ACP dispatch → agent executes → task COMPLETED.

    Prerequisites: Running OMC server at localhost:8000
    Steps:
    1. Register a test employee via register_acp()
    2. Push a simple task
    3. Wait for task completion
    4. Verify: task status is COMPLETED, result stored on TaskNode
    """
    pytest.skip("Requires running OMC server — run manually with: pytest tests/e2e/acp/ -v --no-header -k full_flow")
