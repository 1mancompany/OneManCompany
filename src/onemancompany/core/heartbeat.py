"""Heartbeat detection for employee API connections.

Health checks via probe_chat (minimal-token probe):
- Company-hosted: probe_chat() against provider (batched per company-level key)
- Self-hosted: check worker.pid process liveness via os.kill(pid, 0)
- Script: execute employee's heartbeat.sh script
- Manifest-driven: heartbeat.method in manifest.json overrides auto-detection
"""

from __future__ import annotations

import asyncio

from onemancompany.core.async_utils import spawn_background
import os

from loguru import logger

from onemancompany.core.config import (
    EMPLOYEES_DIR,
    PROVIDER_REGISTRY,
    employee_configs,
    get_provider,
    load_manifest,
    settings,
    FOUNDING_LEVEL,
)
from onemancompany.core import store as _store


def _get_heartbeat_method(emp_id: str, cfg) -> str:
    """Determine heartbeat method for an employee.

    Priority:
    1. manifest.json heartbeat.method (explicit config)
    2. Self-hosted employees → claude_cli
    3. Fallback: auto-detect from api_provider via PROVIDER_REGISTRY
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

    # Fallback: use provider registry to determine health check method
    return "provider_key"


def _resolve_provider_key(cfg) -> str:
    """Return per-employee API key, falling back to company-level key from PROVIDER_REGISTRY."""
    if cfg.api_key:
        return cfg.api_key
    prov = get_provider(cfg.api_provider)
    if prov and prov.env_key:
        return getattr(settings, prov.env_key, "")
    return ""


def check_needs_setup(emp_id: str) -> bool:
    """Check whether an employee needs API key / OAuth configuration (pure local check)."""
    cfg = employee_configs.get(emp_id)
    if not cfg:
        return False  # founding employees without config don't need setup

    if cfg.hosting == "self":
        return False

    prov = get_provider(cfg.api_provider)
    if not prov:
        return True  # unknown provider → needs setup

    # OpenRouter uses company key — no per-employee setup needed
    if cfg.api_provider == "openrouter":
        return False

    # Other providers need either employee key or company-level key
    return not bool(_resolve_provider_key(cfg))


async def _check_provider_online(provider: str, api_key: str, model: str) -> bool:
    """Check provider connectivity via probe_chat.

    Used by heartbeat cycle. Preserves the batching pattern: called once per
    company-level key, result shared across all employees using that key.
    """
    if not api_key or not model:
        return False
    from onemancompany.core.auth_verify import probe_chat
    ok, _error = await probe_chat(provider, api_key, model, timeout=15.0)
    return ok


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
    """Helper: persist api_online and track change."""
    emp_data = _store.load_employee(emp_id)
    if not emp_data:
        return
    current = emp_data.get("runtime", {}).get("api_online", False)
    if current != online:
        try:
            spawn_background(_store.save_employee_runtime(emp_id, api_online=online))
        except RuntimeError:
            logger.debug("No event loop for runtime persist of {}", emp_id)
        if emp_id not in changed:
            changed.append(emp_id)


async def run_heartbeat_cycle() -> list[str]:
    """Run one heartbeat cycle for all employees. Returns list of IDs whose status changed."""
    changed: list[str] = []
    all_employees = _store.load_all_employees()

    # 1. Update needs_setup for all employees
    for emp_id, emp_data in all_employees.items():
        runtime = emp_data.get("runtime", {})
        new_needs_setup = check_needs_setup(emp_id)
        if runtime.get("needs_setup", False) != new_needs_setup:
            await _store.save_employee_runtime(emp_id, needs_setup=new_needs_setup)
            changed.append(emp_id)

    # 2. Route employees to heartbeat methods
    # Group by provider for batching (one health check per company-level key)
    provider_groups: dict[str, list[str]] = {}  # provider_name -> [emp_ids]
    per_employee_checks: list[tuple[str, str, str]] = []  # (emp_id, provider, key)
    claude_cli_employees: list[str] = []
    pid_employees: list[str] = []
    script_employees: list[str] = []

    # Re-read after needs_setup updates
    all_employees = _store.load_all_employees()
    for emp_id, emp_data in all_employees.items():
        runtime = emp_data.get("runtime", {})
        if runtime.get("needs_setup", False):
            # Skip heartbeat for employees needing setup — needs_setup takes priority
            if runtime.get("api_online", False):
                await _store.save_employee_runtime(emp_id, api_online=False)
                if emp_id not in changed:
                    changed.append(emp_id)
            continue

        cfg = employee_configs.get(emp_id)
        if not cfg:
            # Founding employees without config — assume always online
            if not runtime.get("api_online", False):
                await _store.save_employee_runtime(emp_id, api_online=True)
                if emp_id not in changed:
                    changed.append(emp_id)
            continue

        method = _get_heartbeat_method(emp_id, cfg)

        # Founding employees skip heartbeat unless self-hosted (need CLI check)
        level = emp_data.get("level", 0)
        if level >= FOUNDING_LEVEL and method != "claude_cli":
            continue

        if method == "always_online":
            if not runtime.get("api_online", False):
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
        elif method == "provider_key":
            provider = cfg.api_provider
            key = _resolve_provider_key(cfg)

            # OAuth tokens can't be validated via health endpoint — just check existence
            if provider == "anthropic":
                auth_method = cfg.auth_method if cfg.auth_method == "oauth" else settings.anthropic_auth_method
                if auth_method == "oauth":
                    _update_online(emp_id, bool(key), changed)
                    continue

            # Employee has own key → per-employee check
            if cfg.api_key:
                per_employee_checks.append((emp_id, provider, key))
            else:
                # Company-level key → batch by provider
                provider_groups.setdefault(provider, []).append(emp_id)
        # Legacy method names from manifest.json
        elif method == "anthropic_key":
            key = _resolve_provider_key(cfg)
            per_employee_checks.append((emp_id, "anthropic", key))
        else:  # openrouter_key or unknown
            provider_groups.setdefault("openrouter", []).append(emp_id)

    # 3. Batched provider checks — one request per company-level key
    for provider, emp_ids in provider_groups.items():
        prov = get_provider(provider)
        company_key = getattr(settings, prov.env_key, "") if prov and prov.env_key else ""
        first_emp_cfg = employee_configs.get(emp_ids[0])
        default_model = first_emp_cfg.llm_model if first_emp_cfg else ""
        online = await _check_provider_online(provider, company_key, default_model)
        for emp_id in emp_ids:
            _update_online(emp_id, online, changed)

    # 4. Per-employee provider checks (employees with their own API keys)
    for emp_id, provider, key in per_employee_checks:
        emp_cfg = employee_configs.get(emp_id)
        emp_model = emp_cfg.llm_model if emp_cfg else ""
        online = await _check_provider_online(provider, key, emp_model)
        _update_online(emp_id, online, changed)

    # 5. Claude CLI: one check covers all self-hosted employees
    if claude_cli_employees:
        cli_online = await _check_claude_cli()
        for emp_id in claude_cli_employees:
            _update_online(emp_id, cli_online, changed)

    # 6. PID check
    for emp_id in pid_employees:
        online = _check_self_hosted_pid(emp_id)
        _update_online(emp_id, online, changed)

    # 7. Script check
    for emp_id in script_employees:
        online = await _check_script(emp_id)
        _update_online(emp_id, online, changed)

    return changed
