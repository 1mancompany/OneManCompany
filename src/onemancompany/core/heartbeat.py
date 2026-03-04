"""Heartbeat detection for employee API connections.

Zero-token-cost health checks:
- OpenRouter: GET /api/v1/auth/key (one request covers all OpenRouter employees)
- Anthropic: GET /v1/models (per employee, each has own key)
- Self-hosted: check worker.pid process liveness via os.kill(pid, 0)
"""

from __future__ import annotations

import os

import httpx

from onemancompany.core.config import (
    EMPLOYEES_DIR,
    employee_configs,
    settings,
    FOUNDING_LEVEL,
)
from onemancompany.core.state import company_state


def check_needs_setup(emp_id: str) -> bool:
    """Check whether an employee needs API key / OAuth configuration (pure local check)."""
    cfg = employee_configs.get(emp_id)
    if not cfg:
        return False  # founding employees without config don't need setup

    if cfg.hosting == "self":
        # Self-hosted: needs launch.sh AND valid credentials
        if not (EMPLOYEES_DIR / emp_id / "launch.sh").exists():
            return True
        # Self-hosted with Anthropic provider still needs API key / OAuth token
        if cfg.api_provider == "anthropic" and not bool(cfg.api_key):
            return True
        return False

    if cfg.api_provider == "anthropic":
        # Anthropic employees need their own API key or OAuth token
        return not bool(cfg.api_key)

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
    """Validate an Anthropic API key (zero tokens)."""
    if not api_key:
        return False
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                "https://api.anthropic.com/v1/models",
                headers={
                    "x-api-key": api_key,
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


async def run_heartbeat_cycle() -> list[str]:
    """Run one heartbeat cycle for all employees. Returns list of IDs whose status changed."""
    changed: list[str] = []

    # 1. Update needs_setup for all employees
    for emp_id, emp in company_state.employees.items():
        new_needs_setup = check_needs_setup(emp_id)
        if emp.needs_setup != new_needs_setup:
            emp.needs_setup = new_needs_setup
            changed.append(emp_id)

    # 2. Determine which employees need heartbeat checks
    openrouter_employees: list[str] = []
    anthropic_checks: dict[str, str] = {}  # emp_id -> api_key
    self_hosted_employees: list[str] = []

    for emp_id, emp in company_state.employees.items():
        if emp.needs_setup:
            # Skip heartbeat for employees needing setup — needs_setup takes priority
            if emp.api_online:
                emp.api_online = False
                if emp_id not in changed:
                    changed.append(emp_id)
            continue

        cfg = employee_configs.get(emp_id)
        if not cfg:
            # Founding employees without config — assume always online
            if not emp.api_online:
                emp.api_online = True
                if emp_id not in changed:
                    changed.append(emp_id)
            continue

        if emp.level >= FOUNDING_LEVEL:
            continue

        if cfg.hosting == "self":
            self_hosted_employees.append(emp_id)
        elif cfg.api_provider == "anthropic":
            anthropic_checks[emp_id] = cfg.api_key
        else:
            openrouter_employees.append(emp_id)

    # 3. OpenRouter: one request covers all employees
    if openrouter_employees:
        or_online = await _check_openrouter_key()
        for emp_id in openrouter_employees:
            emp = company_state.employees.get(emp_id)
            if emp and emp.api_online != or_online:
                emp.api_online = or_online
                if emp_id not in changed:
                    changed.append(emp_id)

    # 4. Anthropic: per-employee check
    for emp_id, api_key in anthropic_checks.items():
        online = await _check_anthropic_key(api_key)
        emp = company_state.employees.get(emp_id)
        if emp and emp.api_online != online:
            emp.api_online = online
            if emp_id not in changed:
                changed.append(emp_id)

    # 5. Self-hosted: PID check
    for emp_id in self_hosted_employees:
        online = _check_self_hosted_pid(emp_id)
        emp = company_state.employees.get(emp_id)
        if emp and emp.api_online != online:
            emp.api_online = online
            if emp_id not in changed:
                changed.append(emp_id)

    return changed
