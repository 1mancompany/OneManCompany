"""Apply handler for API key auth method.

Handles both company-level and employee-level key application.
"""
from __future__ import annotations

from loguru import logger

from onemancompany.core import store as _store


async def apply_api_key_company(
    provider: str,
    api_key: str,
    model: str = "",
    base_url: str = "",
    chat_class: str = "",
) -> dict:
    """Apply an API key at the company level (Settings/.env).

    Writes the key to the environment via update_env_var and reloads settings.
    """
    from onemancompany.core.config import (
        get_provider,
        update_env_var,
    )

    provider_cfg = get_provider(provider)
    if not provider_cfg and provider != "custom":
        return {"error": "Unknown provider", "code": "invalid_provider"}

    if provider == "custom":
        env_key = "custom_api_key"
    else:
        env_key = provider_cfg.env_key

    if not env_key:
        return {"error": f"No env_key configured for provider {provider}", "code": "config_error"}

    # Write key to .env (update_env_var already calls reload_settings)
    env_var_name = env_key.upper()
    update_env_var(env_var_name, api_key)

    logger.info("Company API key applied for provider: {}", provider)

    return {
        "status": "applied",
        "scope": "company",
        "provider": provider,
        "api_key_set": True,
    }


async def apply_api_key_employee(
    provider: str,
    employee_id: str,
    api_key: str,
    model: str = "",
) -> dict:
    """Apply an API key at the employee level (profile.yaml)."""
    from onemancompany.api.routes import _rebuild_employee_agent

    update_data: dict = {
        "api_provider": provider,
        "api_key": api_key,
        "auth_method": "api_key",
    }
    if model:
        update_data["llm_model"] = model

    await _store.save_employee(employee_id, update_data)
    _rebuild_employee_agent(employee_id)

    logger.info("Employee {} API key applied for provider: {}", employee_id, provider)

    return {
        "status": "applied",
        "scope": "employee",
        "employee_id": employee_id,
        "provider": provider,
        "api_key_set": True,
    }
