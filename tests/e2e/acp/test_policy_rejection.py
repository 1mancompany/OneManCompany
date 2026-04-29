import pytest


@pytest.mark.asyncio
@pytest.mark.timeout(60)
async def test_destructive_op_policy_rejected():
    """E2E: Agent attempts destructive op → policy auto-rejects → agent adapts.

    Prerequisites: Running OMC server with permissions.yaml configured
    Steps:
    1. Register test employee via ACP
    2. Assign task that requires writing to another employee's file
    3. Verify: permission request auto-rejected by policy
    4. Verify: agent receives rejection and adapts (doesn't crash)
    """
    pytest.skip("Requires running OMC server — run manually")
