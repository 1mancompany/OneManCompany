"""Test that heartbeat checks run in parallel via asyncio.gather(), not sequentially."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_per_employee_checks_run_in_parallel():
    """Per-employee provider checks should run concurrently, not sequentially.

    If 3 checks each take 0.1s, sequential = 0.3s, parallel < 0.2s.
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
         patch("onemancompany.core.heartbeat._update_online"), \
         patch("onemancompany.core.auth_verify.probe_chat", side_effect=slow_probe):

        mock_store.load_all_employees.return_value = {
            "e1": {"level": 1, "runtime": {"needs_setup": False}},
            "e2": {"level": 1, "runtime": {"needs_setup": False}},
            "e3": {"level": 1, "runtime": {"needs_setup": False}},
        }
        mock_store.save_employee_runtime = AsyncMock()

        from onemancompany.core.heartbeat import run_heartbeat_cycle

        start = time.monotonic()
        await run_heartbeat_cycle()
        elapsed = time.monotonic() - start

        # 3 checks × 0.1s each: sequential = ~0.3s, parallel < 0.2s
        assert elapsed < 0.25, f"Heartbeat took {elapsed:.2f}s — checks are still sequential"
        assert len(call_times) == 3


@pytest.mark.asyncio
async def test_script_checks_run_in_parallel():
    """Script-based heartbeat checks should run concurrently."""
    call_count = 0

    async def slow_script(emp_id):
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.1)
        return True

    with patch("onemancompany.core.heartbeat._store") as mock_store, \
         patch("onemancompany.core.heartbeat.employee_configs", {
             "s1": MagicMock(api_provider="test", api_key="", llm_model="m1", hosting="company", auth_method="api_key"),
             "s2": MagicMock(api_provider="test", api_key="", llm_model="m2", hosting="company", auth_method="api_key"),
         }), \
         patch("onemancompany.core.heartbeat._get_heartbeat_method", return_value="script"), \
         patch("onemancompany.core.heartbeat.check_needs_setup", return_value=False), \
         patch("onemancompany.core.heartbeat._update_online"), \
         patch("onemancompany.core.heartbeat._check_script", side_effect=slow_script):

        mock_store.load_all_employees.return_value = {
            "s1": {"level": 1, "runtime": {"needs_setup": False}},
            "s2": {"level": 1, "runtime": {"needs_setup": False}},
        }
        mock_store.save_employee_runtime = AsyncMock()

        from onemancompany.core.heartbeat import run_heartbeat_cycle

        start = time.monotonic()
        await run_heartbeat_cycle()
        elapsed = time.monotonic() - start

        assert elapsed < 0.2, f"Script checks took {elapsed:.2f}s — still sequential"
        assert call_count == 2
