"""Test that heartbeat checks run in parallel via asyncio.gather(), not sequentially."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Import at top level — patches target module-level names in heartbeat,
# so the function reference resolves correctly regardless of import order.
from onemancompany.core.heartbeat import run_heartbeat_cycle


@pytest.mark.asyncio
async def test_per_employee_checks_run_in_parallel():
    """Per-employee provider checks should run concurrently, not sequentially.

    Asserts concurrency via start-time proximity (all checks start within 0.05s),
    which is more robust than wall-clock elapsed time on loaded CI machines.
    """
    call_times = []

    async def slow_probe(provider, key, model, timeout=15.0):
        call_times.append(time.monotonic())
        await asyncio.sleep(0.1)
        return True, None

    with patch("onemancompany.core.heartbeat._store") as mock_store, \
         patch("onemancompany.core.heartbeat.employee_configs", {
             "e1": MagicMock(api_provider="test", api_key="k1", llm_model="m1", hosting="company", auth_method="api_key"),
             "e2": MagicMock(api_provider="test", api_key="k2", llm_model="m2", hosting="company", auth_method="api_key"),
             "e3": MagicMock(api_provider="test", api_key="k3", llm_model="m3", hosting="company", auth_method="api_key"),
         }), \
         patch("onemancompany.core.heartbeat._get_heartbeat_method", return_value="provider_key"), \
         patch("onemancompany.core.heartbeat.check_needs_setup", return_value=False), \
         patch("onemancompany.core.heartbeat._update_online") as mock_update, \
         patch("onemancompany.core.auth_verify.probe_chat", side_effect=slow_probe):

        mock_store.load_all_employees.return_value = {
            "e1": {"level": 1, "runtime": {"needs_setup": False}},
            "e2": {"level": 1, "runtime": {"needs_setup": False}},
            "e3": {"level": 1, "runtime": {"needs_setup": False}},
        }
        mock_store.save_employee_runtime = AsyncMock()

        await run_heartbeat_cycle()

        # All 3 checks should have started concurrently (within 0.05s of each other)
        assert len(call_times) == 3
        spread = max(call_times) - min(call_times)
        assert spread < 0.05, f"Checks started {spread:.3f}s apart — not concurrent"

        # All 3 employees should have _update_online called
        assert mock_update.call_count == 3
        called_ids = {c.args[0] for c in mock_update.call_args_list}
        assert called_ids == {"e1", "e2", "e3"}


@pytest.mark.asyncio
async def test_script_checks_run_in_parallel():
    """Script-based heartbeat checks should run concurrently."""
    call_times = []

    async def slow_script(emp_id):
        call_times.append(time.monotonic())
        await asyncio.sleep(0.1)
        return True

    with patch("onemancompany.core.heartbeat._store") as mock_store, \
         patch("onemancompany.core.heartbeat.employee_configs", {
             "s1": MagicMock(api_provider="test", api_key="", llm_model="m1", hosting="company", auth_method="api_key"),
             "s2": MagicMock(api_provider="test", api_key="", llm_model="m2", hosting="company", auth_method="api_key"),
         }), \
         patch("onemancompany.core.heartbeat._get_heartbeat_method", return_value="script"), \
         patch("onemancompany.core.heartbeat.check_needs_setup", return_value=False), \
         patch("onemancompany.core.heartbeat._update_online") as mock_update, \
         patch("onemancompany.core.heartbeat._check_script", side_effect=slow_script):

        mock_store.load_all_employees.return_value = {
            "s1": {"level": 1, "runtime": {"needs_setup": False}},
            "s2": {"level": 1, "runtime": {"needs_setup": False}},
        }
        mock_store.save_employee_runtime = AsyncMock()

        await run_heartbeat_cycle()

        # Both started concurrently
        assert len(call_times) == 2
        spread = max(call_times) - min(call_times)
        assert spread < 0.05, f"Script checks started {spread:.3f}s apart — not concurrent"

        # Both employees updated
        assert mock_update.call_count == 2
        called_ids = {c.args[0] for c in mock_update.call_args_list}
        assert called_ids == {"s1", "s2"}


@pytest.mark.asyncio
async def test_failed_check_marks_offline_not_stale():
    """If a provider check throws, affected employees should be marked offline."""

    async def failing_probe(provider, key, model, timeout=15.0):
        if key == "k2":
            raise ConnectionError("network down")
        return True, None

    with patch("onemancompany.core.heartbeat._store") as mock_store, \
         patch("onemancompany.core.heartbeat.employee_configs", {
             "e1": MagicMock(api_provider="test", api_key="k1", llm_model="m1", hosting="company", auth_method="api_key"),
             "e2": MagicMock(api_provider="test", api_key="k2", llm_model="m2", hosting="company", auth_method="api_key"),
         }), \
         patch("onemancompany.core.heartbeat._get_heartbeat_method", return_value="provider_key"), \
         patch("onemancompany.core.heartbeat.check_needs_setup", return_value=False), \
         patch("onemancompany.core.heartbeat._update_online") as mock_update, \
         patch("onemancompany.core.auth_verify.probe_chat", side_effect=failing_probe):

        mock_store.load_all_employees.return_value = {
            "e1": {"level": 1, "runtime": {"needs_setup": False}},
            "e2": {"level": 1, "runtime": {"needs_setup": False}},
        }
        mock_store.save_employee_runtime = AsyncMock()

        await run_heartbeat_cycle()

        # e1 should be online (True), e2 should be offline (False) due to exception
        calls = {c.args[0]: c.args[1] for c in mock_update.call_args_list}
        assert calls["e1"] is True
        assert calls["e2"] is False
