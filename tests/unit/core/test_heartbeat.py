"""Unit tests for core/heartbeat.py — heartbeat detection for employee APIs."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from onemancompany.core.heartbeat import (
    _check_anthropic_key,
    _check_openrouter_key,
    _check_script,
    _check_self_hosted_pid,
    _get_heartbeat_method,
    _update_online,
    check_needs_setup,
    run_heartbeat_cycle,
)


# ---------------------------------------------------------------------------
# Minimal stub types for test isolation
# ---------------------------------------------------------------------------

@dataclass
class _FakeCfg:
    api_provider: str = "openrouter"
    hosting: str = "company"
    api_key: str = ""


@dataclass
class _FakeEmployee:
    api_online: bool = True
    needs_setup: bool = False
    level: int = 1


# ---------------------------------------------------------------------------
# _get_heartbeat_method
# ---------------------------------------------------------------------------

class TestGetHeartbeatMethod:
    def test_default_openrouter(self):
        cfg = _FakeCfg(api_provider="openrouter")
        with patch("onemancompany.core.heartbeat.load_manifest", return_value=None):
            method = _get_heartbeat_method("emp1", cfg)
            assert method == "openrouter_key"

    def test_anthropic_provider(self):
        cfg = _FakeCfg(api_provider="anthropic")
        with patch("onemancompany.core.heartbeat.load_manifest", return_value=None):
            method = _get_heartbeat_method("emp1", cfg)
            assert method == "anthropic_key"

    def test_manifest_override(self):
        cfg = _FakeCfg(api_provider="openrouter")
        manifest = {"heartbeat": {"method": "always_online"}}
        with patch("onemancompany.core.heartbeat.load_manifest", return_value=manifest):
            method = _get_heartbeat_method("emp1", cfg)
            assert method == "always_online"

    def test_manifest_without_heartbeat(self):
        cfg = _FakeCfg(api_provider="openrouter")
        manifest = {"some_key": "value"}
        with patch("onemancompany.core.heartbeat.load_manifest", return_value=manifest):
            method = _get_heartbeat_method("emp1", cfg)
            assert method == "openrouter_key"

    def test_manifest_heartbeat_not_dict(self):
        cfg = _FakeCfg(api_provider="openrouter")
        manifest = {"heartbeat": "not_a_dict"}
        with patch("onemancompany.core.heartbeat.load_manifest", return_value=manifest):
            method = _get_heartbeat_method("emp1", cfg)
            assert method == "openrouter_key"

    def test_manifest_pid_method(self):
        cfg = _FakeCfg()
        manifest = {"heartbeat": {"method": "pid"}}
        with patch("onemancompany.core.heartbeat.load_manifest", return_value=manifest):
            method = _get_heartbeat_method("emp1", cfg)
            assert method == "pid"


# ---------------------------------------------------------------------------
# check_needs_setup
# ---------------------------------------------------------------------------

class TestCheckNeedsSetup:
    def test_no_config_returns_false(self):
        with patch("onemancompany.core.heartbeat.employee_configs", {}):
            assert check_needs_setup("missing") is False

    def test_openrouter_no_setup_needed(self):
        cfg = _FakeCfg(api_provider="openrouter")
        with patch("onemancompany.core.heartbeat.employee_configs", {"emp1": cfg}):
            assert check_needs_setup("emp1") is False

    def test_anthropic_without_key_needs_setup(self):
        cfg = _FakeCfg(api_provider="anthropic", api_key="")
        with patch("onemancompany.core.heartbeat.employee_configs", {"emp1": cfg}):
            assert check_needs_setup("emp1") is True

    def test_anthropic_with_key_no_setup(self):
        cfg = _FakeCfg(api_provider="anthropic", api_key="sk-test")
        with patch("onemancompany.core.heartbeat.employee_configs", {"emp1": cfg}):
            assert check_needs_setup("emp1") is False

    def test_self_hosted_without_launch_script(self, tmp_path):
        cfg = _FakeCfg(hosting="self", api_provider="openrouter")
        with patch("onemancompany.core.heartbeat.employee_configs", {"emp1": cfg}), \
             patch("onemancompany.core.heartbeat.EMPLOYEES_DIR", tmp_path):
            (tmp_path / "emp1").mkdir()
            assert check_needs_setup("emp1") is True

    def test_self_hosted_with_launch_script(self, tmp_path):
        cfg = _FakeCfg(hosting="self", api_provider="openrouter")
        with patch("onemancompany.core.heartbeat.employee_configs", {"emp1": cfg}), \
             patch("onemancompany.core.heartbeat.EMPLOYEES_DIR", tmp_path):
            emp_dir = tmp_path / "emp1"
            emp_dir.mkdir()
            (emp_dir / "launch.sh").write_text("#!/bin/bash")
            assert check_needs_setup("emp1") is False

    def test_self_hosted_anthropic_needs_key(self, tmp_path):
        cfg = _FakeCfg(hosting="self", api_provider="anthropic", api_key="")
        with patch("onemancompany.core.heartbeat.employee_configs", {"emp1": cfg}), \
             patch("onemancompany.core.heartbeat.EMPLOYEES_DIR", tmp_path):
            emp_dir = tmp_path / "emp1"
            emp_dir.mkdir()
            (emp_dir / "launch.sh").write_text("#!/bin/bash")
            assert check_needs_setup("emp1") is True


# ---------------------------------------------------------------------------
# _check_openrouter_key
# ---------------------------------------------------------------------------

class TestCheckOpenRouterKey:
    async def test_no_key_returns_false(self):
        with patch("onemancompany.core.heartbeat.settings") as mock_settings:
            mock_settings.openrouter_api_key = ""
            result = await _check_openrouter_key()
            assert result is False

    async def test_valid_key(self):
        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("onemancompany.core.heartbeat.settings") as mock_settings, \
             patch("onemancompany.core.heartbeat.httpx.AsyncClient", return_value=mock_client):
            mock_settings.openrouter_api_key = "sk-test"
            result = await _check_openrouter_key()
            assert result is True

    async def test_invalid_key(self):
        mock_response = MagicMock()
        mock_response.status_code = 401

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("onemancompany.core.heartbeat.settings") as mock_settings, \
             patch("onemancompany.core.heartbeat.httpx.AsyncClient", return_value=mock_client):
            mock_settings.openrouter_api_key = "sk-bad"
            result = await _check_openrouter_key()
            assert result is False

    async def test_network_error(self):
        with patch("onemancompany.core.heartbeat.settings") as mock_settings, \
             patch("onemancompany.core.heartbeat.httpx.AsyncClient", side_effect=Exception("network")):
            mock_settings.openrouter_api_key = "sk-test"
            result = await _check_openrouter_key()
            assert result is False


# ---------------------------------------------------------------------------
# _check_anthropic_key
# ---------------------------------------------------------------------------

class TestCheckAnthropicKey:
    async def test_no_key_returns_false(self):
        result = await _check_anthropic_key("")
        assert result is False

    async def test_permanent_key_success(self):
        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("onemancompany.core.heartbeat.httpx.AsyncClient", return_value=mock_client):
            result = await _check_anthropic_key("sk-ant-test")
            assert result is True

    async def test_oauth_fallback(self):
        resp_401 = MagicMock()
        resp_401.status_code = 401
        resp_200 = MagicMock()
        resp_200.status_code = 200

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        # First call (x-api-key) returns 401, second (Bearer) returns 200
        mock_client.get = AsyncMock(side_effect=[resp_401, resp_200])

        with patch("onemancompany.core.heartbeat.httpx.AsyncClient", return_value=mock_client):
            result = await _check_anthropic_key("oauth-token")
            assert result is True

    async def test_both_fail(self):
        resp_401 = MagicMock()
        resp_401.status_code = 401

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=resp_401)

        with patch("onemancompany.core.heartbeat.httpx.AsyncClient", return_value=mock_client):
            result = await _check_anthropic_key("bad-key")
            assert result is False

    async def test_network_error(self):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=Exception("timeout"))

        with patch("onemancompany.core.heartbeat.httpx.AsyncClient", return_value=mock_client):
            result = await _check_anthropic_key("sk-test")
            assert result is False


# ---------------------------------------------------------------------------
# _check_self_hosted_pid
# ---------------------------------------------------------------------------

class TestCheckSelfHostedPid:
    def test_no_pid_file(self, tmp_path):
        with patch("onemancompany.core.heartbeat.EMPLOYEES_DIR", tmp_path):
            (tmp_path / "emp1").mkdir()
            assert _check_self_hosted_pid("emp1") is False

    def test_valid_pid(self, tmp_path):
        import os
        with patch("onemancompany.core.heartbeat.EMPLOYEES_DIR", tmp_path):
            emp_dir = tmp_path / "emp1"
            emp_dir.mkdir()
            # Use current process PID (guaranteed to exist)
            (emp_dir / "worker.pid").write_text(str(os.getpid()))
            assert _check_self_hosted_pid("emp1") is True

    def test_dead_pid(self, tmp_path):
        with patch("onemancompany.core.heartbeat.EMPLOYEES_DIR", tmp_path):
            emp_dir = tmp_path / "emp1"
            emp_dir.mkdir()
            (emp_dir / "worker.pid").write_text("999999999")  # very unlikely to exist
            with patch("os.kill", side_effect=ProcessLookupError()):
                assert _check_self_hosted_pid("emp1") is False

    def test_invalid_pid_content(self, tmp_path):
        with patch("onemancompany.core.heartbeat.EMPLOYEES_DIR", tmp_path):
            emp_dir = tmp_path / "emp1"
            emp_dir.mkdir()
            (emp_dir / "worker.pid").write_text("not_a_number")
            assert _check_self_hosted_pid("emp1") is False


# ---------------------------------------------------------------------------
# _check_script
# ---------------------------------------------------------------------------

class TestCheckScript:
    async def test_no_script(self, tmp_path):
        with patch("onemancompany.core.heartbeat.EMPLOYEES_DIR", tmp_path):
            (tmp_path / "emp1").mkdir()
            assert await _check_script("emp1") is False

    async def test_script_success(self, tmp_path):
        with patch("onemancompany.core.heartbeat.EMPLOYEES_DIR", tmp_path):
            emp_dir = tmp_path / "emp1"
            emp_dir.mkdir()
            script = emp_dir / "heartbeat.sh"
            script.write_text("#!/bin/bash\nexit 0\n")
            script.chmod(0o755)
            result = await _check_script("emp1")
            assert result is True

    async def test_script_failure(self, tmp_path):
        with patch("onemancompany.core.heartbeat.EMPLOYEES_DIR", tmp_path):
            emp_dir = tmp_path / "emp1"
            emp_dir.mkdir()
            script = emp_dir / "heartbeat.sh"
            script.write_text("#!/bin/bash\nexit 1\n")
            script.chmod(0o755)
            result = await _check_script("emp1")
            assert result is False


# ---------------------------------------------------------------------------
# _update_online
# ---------------------------------------------------------------------------

class TestUpdateOnline:
    def test_change_from_offline_to_online(self):
        emp = _FakeEmployee(api_online=False)
        employees = {"emp1": emp}
        changed = []
        with patch("onemancompany.core.heartbeat.company_state") as mock_state:
            mock_state.employees = employees
            _update_online("emp1", True, changed)
            assert emp.api_online is True
            assert "emp1" in changed

    def test_no_change_no_append(self):
        emp = _FakeEmployee(api_online=True)
        employees = {"emp1": emp}
        changed = []
        with patch("onemancompany.core.heartbeat.company_state") as mock_state:
            mock_state.employees = employees
            _update_online("emp1", True, changed)
            assert changed == []

    def test_missing_employee_ignored(self):
        changed = []
        with patch("onemancompany.core.heartbeat.company_state") as mock_state:
            mock_state.employees = {}
            _update_online("missing", True, changed)
            assert changed == []

    def test_no_duplicate_in_changed(self):
        emp = _FakeEmployee(api_online=False)
        changed = ["emp1"]
        with patch("onemancompany.core.heartbeat.company_state") as mock_state:
            mock_state.employees = {"emp1": emp}
            _update_online("emp1", True, changed)
            assert changed.count("emp1") == 1


# ---------------------------------------------------------------------------
# run_heartbeat_cycle
# ---------------------------------------------------------------------------

class TestRunHeartbeatCycle:
    async def test_empty_employees(self):
        with patch("onemancompany.core.heartbeat.company_state") as mock_state, \
             patch("onemancompany.core.heartbeat.employee_configs", {}):
            mock_state.employees = {}
            changed = await run_heartbeat_cycle()
            assert changed == []

    async def test_founding_employees_skipped(self):
        emp = _FakeEmployee(api_online=True, level=4)
        cfg = _FakeCfg()
        with patch("onemancompany.core.heartbeat.company_state") as mock_state, \
             patch("onemancompany.core.heartbeat.employee_configs", {"emp1": cfg}), \
             patch("onemancompany.core.heartbeat.FOUNDING_LEVEL", 4), \
             patch("onemancompany.core.heartbeat.check_needs_setup", return_value=False):
            mock_state.employees = {"emp1": emp}
            changed = await run_heartbeat_cycle()
            # Founding employees are skipped entirely
            assert changed == []

    async def test_needs_setup_sets_offline(self):
        emp = _FakeEmployee(api_online=True, needs_setup=False)
        cfg = _FakeCfg()
        with patch("onemancompany.core.heartbeat.company_state") as mock_state, \
             patch("onemancompany.core.heartbeat.employee_configs", {"emp1": cfg}), \
             patch("onemancompany.core.heartbeat.check_needs_setup", return_value=True), \
             patch("onemancompany.core.heartbeat.FOUNDING_LEVEL", 4):
            mock_state.employees = {"emp1": emp}
            changed = await run_heartbeat_cycle()
            assert emp.needs_setup is True
            assert emp.api_online is False
            assert "emp1" in changed

    async def test_no_config_always_online(self):
        emp = _FakeEmployee(api_online=False)
        with patch("onemancompany.core.heartbeat.company_state") as mock_state, \
             patch("onemancompany.core.heartbeat.employee_configs", {}), \
             patch("onemancompany.core.heartbeat.check_needs_setup", return_value=False):
            mock_state.employees = {"emp1": emp}
            changed = await run_heartbeat_cycle()
            assert emp.api_online is True
            assert "emp1" in changed

    async def test_always_online_method(self):
        emp = _FakeEmployee(api_online=False, level=1)
        cfg = _FakeCfg()
        with patch("onemancompany.core.heartbeat.company_state") as mock_state, \
             patch("onemancompany.core.heartbeat.employee_configs", {"emp1": cfg}), \
             patch("onemancompany.core.heartbeat.check_needs_setup", return_value=False), \
             patch("onemancompany.core.heartbeat.FOUNDING_LEVEL", 4), \
             patch("onemancompany.core.heartbeat._get_heartbeat_method", return_value="always_online"):
            mock_state.employees = {"emp1": emp}
            changed = await run_heartbeat_cycle()
            assert emp.api_online is True
            assert "emp1" in changed
