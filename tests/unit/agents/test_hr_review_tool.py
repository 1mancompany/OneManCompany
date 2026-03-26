"""Tests for HR performance_review tool."""
import pytest
from unittest.mock import patch, AsyncMock, MagicMock


class TestPerformanceReview:

    @pytest.mark.asyncio
    @patch("onemancompany.agents.hr_agent._store")
    @patch("onemancompany.agents.hr_agent.run_performance_meeting", new_callable=AsyncMock)
    async def test_review_success(self, mock_meeting, mock_store):
        from onemancompany.agents.hr_agent import performance_review
        mock_store.load_employee.return_value = {
            "current_quarter_tasks": 3,
            "performance_history": [],
            "level": 1,
        }
        mock_store.save_employee = AsyncMock()

        result = await performance_review.ainvoke({
            "employee_id": "00006",
            "score": 3.75,
            "feedback": "Great work",
        })
        assert result["status"] == "ok"
        assert result["score"] == 3.75
        mock_store.save_employee.assert_awaited()

    @pytest.mark.asyncio
    @patch("onemancompany.agents.hr_agent._store")
    async def test_review_employee_not_found(self, mock_store):
        from onemancompany.agents.hr_agent import performance_review
        mock_store.load_employee.return_value = {}

        result = await performance_review.ainvoke({
            "employee_id": "99999",
            "score": 3.5,
        })
        assert result["status"] == "error"
        assert "not found" in result["message"]

    @pytest.mark.asyncio
    @patch("onemancompany.agents.hr_agent._store")
    async def test_review_insufficient_tasks(self, mock_store):
        from onemancompany.agents.hr_agent import performance_review
        mock_store.load_employee.return_value = {
            "current_quarter_tasks": 1,
        }

        result = await performance_review.ainvoke({
            "employee_id": "00006",
            "score": 3.5,
        })
        assert result["status"] == "error"
        assert "not completed" in result["message"]

    @pytest.mark.asyncio
    @patch("onemancompany.agents.hr_agent._store")
    @patch("onemancompany.agents.hr_agent.run_performance_meeting", new_callable=AsyncMock)
    async def test_score_snaps_to_nearest(self, mock_meeting, mock_store):
        from onemancompany.agents.hr_agent import performance_review
        mock_store.load_employee.return_value = {
            "current_quarter_tasks": 3,
            "performance_history": [],
            "level": 1,
        }
        mock_store.save_employee = AsyncMock()

        result = await performance_review.ainvoke({
            "employee_id": "00006",
            "score": 3.6,  # Should snap to 3.5
        })
        assert result["score"] == 3.5

    @pytest.mark.asyncio
    @patch("onemancompany.agents.hr_agent._store")
    @patch("onemancompany.agents.hr_agent.run_performance_meeting", new_callable=AsyncMock)
    async def test_pip_started_on_low_score(self, mock_meeting, mock_store):
        from onemancompany.agents.hr_agent import performance_review
        mock_store.load_employee.return_value = {
            "current_quarter_tasks": 3,
            "performance_history": [],
            "level": 1,
            "pip": None,
        }
        mock_store.save_employee = AsyncMock()

        result = await performance_review.ainvoke({
            "employee_id": "00006",
            "score": 3.25,
        })
        assert result["status"] == "ok"
        assert result.get("action") == "pip_started"

    @pytest.mark.asyncio
    @patch("onemancompany.agents.hr_agent._store")
    @patch("onemancompany.agents.hr_agent.run_performance_meeting", new_callable=AsyncMock)
    async def test_history_trimmed(self, mock_meeting, mock_store):
        from onemancompany.agents.hr_agent import performance_review
        from onemancompany.core.config import MAX_PERFORMANCE_HISTORY
        mock_store.load_employee.return_value = {
            "current_quarter_tasks": 3,
            "performance_history": [{"score": 3.5, "tasks": 3}] * MAX_PERFORMANCE_HISTORY,
            "level": 1,
        }
        mock_store.save_employee = AsyncMock()

        await performance_review.ainvoke({
            "employee_id": "00006",
            "score": 3.5,
        })
        # Check the saved history length
        call_args = mock_store.save_employee.call_args
        saved_history = call_args[0][1]["performance_history"]
        assert len(saved_history) == MAX_PERFORMANCE_HISTORY
