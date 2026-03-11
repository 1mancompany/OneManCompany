"""Heartbeat detection for employee API connections.

Zero-token-cost health checks:
- OpenRouter: GET /api/v1/auth/key (one request covers all OpenRouter employees)
- Anthropic: GET /v1/models (per employee, each has own key)
- Self-hosted: check worker.pid process liveness via os.kill(pid, 0)
- Script: execute employee's heartbeat.sh script
- Manifest-driven: heartbeat.method in manifest.json overrides auto-detection
"""

from __future__ import annotations

import asyncio
import os

import httpx
from loguru import logger

from onemancompany.core.config import (
    EMPLOYEES_DIR,
    employee_configs,
    load_manifest,
    settings,
    FOUNDING_LEVEL,
)
from onemancompany.core.state import company_state
from onemancompany.core import store as _store


def _get_heartbeat_method(emp_id: str, cfg) -> str:
    """Determine heartbeat method for an employee.

    Priority:
    1. manifest.json heartbeat.method (explicit config)
    2. Self-hosted employees → claude_cli
    3. Fallback: auto-detect from api_provider
    """
    manifest = load_manifest(emp_id)
    if manifest:
        hb = manifest.get("heartbeat")
        if isinstance(hb, dict):
            method = hb.get("method")
            if method:
                return method

    # Self-hosted employees use Claude CLI — check via `claude --version`
    if cfg.hosting == "self":
        return "claude_cli"

    # Fallback: auto-detect based on api_provider
    if cfg.api_provider == "anthropic":
        return "anthropic_key"
    return "openrouter_key"


def _resolve_anthropic_key(cfg) -> str:
    """Return per-employee Anthropic key, falling back to company-level key."""
    return cfg.api_key or settings.anthropic_api_key


def check_needs_setup(emp_id: str) -> bool:
    """Check whether an employee needs API key / OAuth configuration (pure local check)."""
    cfg = employee_configs.get(emp_id)
    if not cfg:
        return False  # founding employees without config don't need setup

    if cfg.hosting == "self":
        # Self-hosted employees use Claude CLI (run_claude_session) which manages
        # its own auth — no API key or launch.sh needed from our side.
        return False

    if cfg.api_provider == "anthropic":
        # Anthropic employees need their own API key or company-level key
        return not bool(_resolve_anthropic_key(cfg))

    # OpenRouter: uses company key — no per-employee setup needed
    return False


async def _check_openrouter_key() -> bool:
    """Validate the company OpenRouter API key (zero tokens)."""
    api_key = settings.openrouter_api_key
    if not api_key:
        return False
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                "https://openrouter.ai/api/v1/auth/key",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            return resp.status_code == 200
    except Exception:
        return False


async def _check_anthropic_key(api_key: str) -> bool:
    """Validate an Anthropic API key (zero tokens).

    Supports both permanent keys (x-api-key header) and OAuth access tokens
    (Authorization: Bearer header).  Tries x-api-key first; on 401 falls
    back to Bearer.
    """
    if not api_key:
        return False
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            # Try permanent key first
            resp = await client.get(
                "https://api.anthropic.com/v1/models",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                },
            )
            if resp.status_code == 200:
                return True
            # Fallback: OAuth access token
            resp = await client.get(
                "https://api.anthropic.com/v1/models",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "anthropic-version": "2023-06-01",
                },
            )
            return resp.status_code == 200
    except Exception:
        return False


def _check_self_hosted_pid(emp_id: str) -> bool:
    """Check if the self-hosted worker process is alive via PID file."""
    pid_file = EMPLOYEES_DIR / emp_id / "worker.pid"
    if not pid_file.exists():
        return False
    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, 0)  # signal 0: check existence without killing
        return True
    except (ValueError, ProcessLookupError, PermissionError, OSError):
        return False


async def _check_claude_cli() -> bool:
    """Check if Claude CLI is available and authenticated via `claude --version`."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "claude", "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        return proc.returncode == 0 and bool(stdout.strip())
    except (asyncio.TimeoutError, FileNotFoundError, OSError):
        return False


async def _check_script(emp_id: str) -> bool:
    """Run {employee_dir}/heartbeat.sh and return True if exit 0."""
    script = EMPLOYEES_DIR / emp_id / "heartbeat.sh"
    if not script.exists():
        return False
    try:
        proc = await asyncio.create_subprocess_exec(
            str(script), str(EMPLOYEES_DIR / emp_id),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=5)
        return proc.returncode == 0
    except (asyncio.TimeoutError, OSError):
        return False


def _update_online(emp_id: str, online: bool, changed: list[str]) -> None:
    """Helper: set api_online and track change."""
    emp = company_state.employees.get(emp_id)
    if emp and emp.api_online != online:
        emp.api_online = online
        try:
            asyncio.create_task(_store.save_employee_runtime(emp_id, api_online=online))
        except RuntimeError:
            logger.debug("No event loop for runtime persist of {}", emp_id)
        if emp_id not in changed:
            changed.append(emp_id)


async def run_heartbeat_cycle() -> list[str]:
    """Run one heartbeat cycle for all employees. Returns list of IDs whose status changed."""
    changed: list[str] = []

    # 1. Update needs_setup for all employees
    for emp_id, emp in company_state.employees.items():
        new_needs_setup = check_needs_setup(emp_id)
        if emp.needs_setup != new_needs_setup:
            emp.needs_setup = new_needs_setup
            await _store.save_employee_runtime(emp_id, needs_setup=new_needs_setup)
            changed.append(emp_id)

    # 2. Route employees to heartbeat methods
    openrouter_employees: list[str] = []
    anthropic_checks: dict[str, str] = {}  # emp_id -> api_key
    claude_cli_employees: list[str] = []
    pid_employees: list[str] = []
    script_employees: list[str] = []

    for emp_id, emp in company_state.employees.items():
        if emp.needs_setup:
            # Skip heartbeat for employees needing setup — needs_setup takes priority
            if emp.api_online:
                emp.api_online = False
                await _store.save_employee_runtime(emp_id, api_online=False)
                if emp_id not in changed:
                    changed.append(emp_id)
            continue

        cfg = employee_configs.get(emp_id)
        if not cfg:
            # Founding employees without config — assume always online
            if not emp.api_online:
                emp.api_online = True
                await _store.save_employee_runtime(emp_id, api_online=True)
                if emp_id not in changed:
                    changed.append(emp_id)
            continue

        method = _get_heartbeat_method(emp_id, cfg)

        # Founding employees skip heartbeat unless self-hosted (need CLI check)
        if emp.level >= FOUNDING_LEVEL and method != "claude_cli":
            continue

        if method == "always_online":
            if not emp.api_online:
                emp.api_online = True
                await _store.save_employee_runtime(emp_id, api_online=True)
                if emp_id not in changed:
                    changed.append(emp_id)
            continue
        elif method == "claude_cli":
            claude_cli_employees.append(emp_id)
        elif method == "pid":
            pid_employees.append(emp_id)
        elif method == "script":
            script_employees.append(emp_id)
        elif method == "anthropic_key":
            key = _resolve_anthropic_key(cfg)
            # OAuth tokens can't be validated via /v1/models — just check existence
            auth_method = cfg.auth_method if cfg.auth_method == "oauth" else settings.anthropic_auth_method
            if auth_method == "oauth":
                _update_online(emp_id, bool(key), changed)
            else:
                anthropic_checks[emp_id] = key
        else:  # openrouter_key
            openrouter_employees.append(emp_id)

    # 3. OpenRouter: one request covers all employees
    if openrouter_employees:
        or_online = await _check_openrouter_key()
        for emp_id in openrouter_employees:
            _update_online(emp_id, or_online, changed)

    # 4. Anthropic: per-employee check
    for emp_id, api_key in anthropic_checks.items():
        online = await _check_anthropic_key(api_key)
        _update_online(emp_id, online, changed)

    # 5. Claude CLI: one check covers all self-hosted employees
    if claude_cli_employees:
        cli_online = await _check_claude_cli()
        for emp_id in claude_cli_employees:
            _update_online(emp_id, cli_online, changed)

    # 7. PID check
    for emp_id in pid_employees:
        online = _check_self_hosted_pid(emp_id)
        _update_online(emp_id, online, changed)

    # 8. Script check
    for emp_id in script_employees:
        online = await _check_script(emp_id)
        _update_online(emp_id, online, changed)

    return changed
