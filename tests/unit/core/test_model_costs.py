"""Unit tests for core/model_costs.py — salary & cost estimation."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# compute_salary
# ---------------------------------------------------------------------------

class TestComputeSalary:
    def test_average_of_input_output(self, monkeypatch):
        import onemancompany.core.model_costs as mc

        monkeypatch.setattr(mc, "_cost_cache", {
            "test-model": {"input": 2.0, "output": 6.0},
        })
        monkeypatch.setattr(mc, "_cache_ts", 9999999999.0)  # prevent refresh

        salary = mc.compute_salary("test-model")
        assert salary == 4.0  # (2 + 6) / 2

    def test_unknown_model_uses_default(self, monkeypatch):
        import onemancompany.core.model_costs as mc

        monkeypatch.setattr(mc, "_cost_cache", {})
        monkeypatch.setattr(mc, "_cache_ts", 9999999999.0)

        salary = mc.compute_salary("unknown-model")
        # DEFAULT_COST = {"input": 1.00, "output": 3.00}
        assert salary == 2.0  # (1 + 3) / 2


# ---------------------------------------------------------------------------
# estimate_task_cost
# ---------------------------------------------------------------------------

class TestEstimateTaskCost:
    def test_cost_calculation(self, monkeypatch):
        import onemancompany.core.model_costs as mc

        monkeypatch.setattr(mc, "_cost_cache", {
            "test-model": {"input": 2.0, "output": 6.0},
        })
        monkeypatch.setattr(mc, "_cache_ts", 9999999999.0)

        # 1M tokens, 40% input, 60% output
        cost = mc.estimate_task_cost("test-model", 1_000_000)
        # input_cost = 400_000 * 2.0 / 1_000_000 = 0.8
        # output_cost = 600_000 * 6.0 / 1_000_000 = 3.6
        # total = 4.4
        assert abs(cost - 4.4) < 0.001

    def test_zero_tokens(self, monkeypatch):
        import onemancompany.core.model_costs as mc

        monkeypatch.setattr(mc, "_cost_cache", {
            "test-model": {"input": 2.0, "output": 6.0},
        })
        monkeypatch.setattr(mc, "_cache_ts", 9999999999.0)

        cost = mc.estimate_task_cost("test-model", 0)
        assert cost == 0.0


# ---------------------------------------------------------------------------
# get_model_cost
# ---------------------------------------------------------------------------

class TestGetModelCost:
    def test_returns_cached_cost(self, monkeypatch):
        import onemancompany.core.model_costs as mc

        monkeypatch.setattr(mc, "_cost_cache", {
            "cached-model": {"input": 1.5, "output": 4.5},
        })
        monkeypatch.setattr(mc, "_cache_ts", 9999999999.0)

        cost = mc.get_model_cost("cached-model")
        assert cost["input"] == 1.5
        assert cost["output"] == 4.5

    def test_missing_model_returns_default(self, monkeypatch):
        import onemancompany.core.model_costs as mc

        monkeypatch.setattr(mc, "_cost_cache", {})
        monkeypatch.setattr(mc, "_cache_ts", 9999999999.0)

        cost = mc.get_model_cost("missing")
        assert cost == mc.DEFAULT_COST


# ---------------------------------------------------------------------------
# refresh_cache
# ---------------------------------------------------------------------------

class TestRefreshCache:
    def test_populates_cache_on_success(self, monkeypatch):
        import onemancompany.core.model_costs as mc

        monkeypatch.setattr(mc, "_cost_cache", {})
        monkeypatch.setattr(mc, "_cache_ts", 0.0)
        monkeypatch.setattr(
            mc, "_fetch_openrouter_pricing",
            lambda: {"model-a": {"input": 1.0, "output": 2.0}},
        )

        mc.refresh_cache()
        assert "model-a" in mc._cost_cache

    def test_no_update_on_empty_fetch(self, monkeypatch):
        import onemancompany.core.model_costs as mc

        monkeypatch.setattr(mc, "_cost_cache", {"existing": {"input": 1.0, "output": 1.0}})
        monkeypatch.setattr(mc, "_cache_ts", 0.0)
        monkeypatch.setattr(mc, "_fetch_openrouter_pricing", lambda: {})

        mc.refresh_cache()
        # Should preserve existing cache when fetch returns empty
        assert "existing" in mc._cost_cache
