import pytest


@pytest.mark.asyncio
@pytest.mark.timeout(120)
async def test_agent_dispatch_child_coordination():
    """E2E: Agent A dispatch_child to Agent B → both complete.

    Prerequisites: Running OMC server with at least 2 employees
    Steps:
    1. Register two test employees via ACP
    2. Assign task to Employee A that requires dispatch_child to Employee B
    3. Wait for both to complete
    4. Verify: both tasks COMPLETED, child result accessible to parent
    """
    pytest.skip("Requires running OMC server — run manually")
