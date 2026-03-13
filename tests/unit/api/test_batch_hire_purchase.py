"""Tests for batch hire with Talent Market purchase+clone flow."""
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path
import pytest


class TestBatchHirePurchase:
    @pytest.mark.asyncio
    async def test_purchase_called_when_connected(self):
        """When talent_market is connected, hire() and onboard() are called."""
        from onemancompany.agents import recruitment

        # Setup connected state
        recruitment.talent_market._session = MagicMock()
        recruitment.talent_market.hire = AsyncMock(return_value={"status": "ok"})
        recruitment.talent_market.onboard = AsyncMock(return_value={"repo_url": "https://git/t1.git"})

        recruitment.pending_candidates["bp1"] = [
            {"id": "t1", "talent_id": "t1", "name": "Dev", "role": "Engineer",
             "skill_set": [], "sprite": "employee_blue"}
        ]
        recruitment._pending_project_ctx["bp1"] = {"session_id": "ses_x"}

        try:
            with patch("onemancompany.agents.onboarding.clone_talent_repo", new_callable=AsyncMock, return_value=Path("/tmp/t1")) as mock_clone, \
                 patch("onemancompany.agents.onboarding.execute_hire", new_callable=AsyncMock) as mock_hire, \
                 patch("onemancompany.agents.onboarding.generate_nickname", new_callable=AsyncMock, return_value="测试"), \
                 patch("onemancompany.core.config.load_talent_profile", return_value={}), \
                 patch("onemancompany.core.events.event_bus", MagicMock(publish=AsyncMock())):

                mock_hire.return_value = MagicMock(id="00099")

                from onemancompany.api.routes import batch_hire_candidates
                result = await batch_hire_candidates({
                    "batch_id": "bp1",
                    "selections": [{"candidate_id": "t1", "role": "Engineer"}],
                })

                recruitment.talent_market.hire.assert_awaited_once()
                recruitment.talent_market.onboard.assert_awaited_once_with("t1")
                mock_clone.assert_awaited_once()
        finally:
            recruitment.talent_market._session = None
            recruitment.pending_candidates.pop("bp1", None)
            recruitment._pending_project_ctx.pop("bp1", None)

    @pytest.mark.asyncio
    async def test_purchase_error_returns_error(self):
        """When hire() returns an error, batch hire returns it."""
        from onemancompany.agents import recruitment

        recruitment.talent_market._session = MagicMock()
        recruitment.talent_market.hire = AsyncMock(return_value={
            "error": "Insufficient balance", "balance": 10, "required": 50, "shortfall": 40
        })

        recruitment.pending_candidates["bp2"] = [
            {"id": "t1", "talent_id": "t1", "name": "Dev", "role": "Engineer",
             "skill_set": [], "sprite": "employee_blue"}
        ]
        recruitment._pending_project_ctx["bp2"] = {"session_id": "ses_y"}

        try:
            from onemancompany.api.routes import batch_hire_candidates
            result = await batch_hire_candidates({
                "batch_id": "bp2",
                "selections": [{"candidate_id": "t1", "role": "Engineer"}],
            })

            assert "error" in result
            assert result["balance"] == 10
            assert result["shortfall"] == 40
        finally:
            recruitment.talent_market._session = None
            recruitment.pending_candidates.pop("bp2", None)
            recruitment._pending_project_ctx.pop("bp2", None)

    @pytest.mark.asyncio
    async def test_no_purchase_when_disconnected(self):
        """When talent_market is not connected, hire() is not called."""
        from onemancompany.agents import recruitment

        recruitment.talent_market._session = None  # disconnected
        recruitment.talent_market.hire = AsyncMock()

        recruitment.pending_candidates["bp3"] = [
            {"id": "t1", "talent_id": "t1", "name": "Dev", "role": "Engineer",
             "skill_set": [], "sprite": "employee_blue"}
        ]

        try:
            with patch("onemancompany.agents.onboarding.execute_hire", new_callable=AsyncMock) as mock_hire, \
                 patch("onemancompany.agents.onboarding.generate_nickname", new_callable=AsyncMock, return_value="测试"), \
                 patch("onemancompany.core.config.load_talent_profile", return_value={}), \
                 patch("onemancompany.core.events.event_bus", MagicMock(publish=AsyncMock())):

                mock_hire.return_value = MagicMock(id="00099")

                from onemancompany.api.routes import batch_hire_candidates
                result = await batch_hire_candidates({
                    "batch_id": "bp3",
                    "selections": [{"candidate_id": "t1", "role": "Engineer"}],
                })

                recruitment.talent_market.hire.assert_not_awaited()
        finally:
            recruitment.pending_candidates.pop("bp3", None)
