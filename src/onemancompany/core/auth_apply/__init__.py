"""Auth choice apply dispatch.

Routes auth_method to the appropriate handler.
"""
from __future__ import annotations

from loguru import logger

from onemancompany.core.auth_choices import resolve_auth_choice


async def apply_auth_choice(
    choice_value: str,
    scope: str,
    *,
    api_key: str = "",
    model: str = "",
    employee_id: str = "",
    base_url: str = "",
    chat_class: str = "",
) -> dict:
    """Dispatch an auth choice to the appropriate apply handler.

    Args:
        choice_value: e.g. "deepseek-api-key", "anthropic-setup-token"
        scope: "company" or "employee"
        api_key: The API key to apply
        model: Optional model override
        employee_id: Required when scope == "employee"
        base_url: Required for custom provider
        chat_class: Required for custom provider
    """
    option = resolve_auth_choice(choice_value)
    if option is None:
        return {"error": "Unknown auth choice", "code": "invalid_choice"}

    if not option.available:
        return {
            "error": f"{option.label} is not yet available (Coming Soon)",
            "code": "not_available",
        }

    if option.auth_method == "api_key":
        from onemancompany.core.auth_apply.api_key import (
            apply_api_key_company,
            apply_api_key_employee,
        )

        if scope == "company":
            return await apply_api_key_company(
                provider=option.provider,
                api_key=api_key,
                model=model,
                base_url=base_url,
                chat_class=chat_class,
            )
        elif scope == "employee":
            if not employee_id:
                return {"error": "employee_id required", "code": "missing_param"}
            return await apply_api_key_employee(
                provider=option.provider,
                employee_id=employee_id,
                api_key=api_key,
                model=model,
            )
        else:
            return {"error": f"Invalid scope: {scope}", "code": "invalid_scope"}

    # Phase 2 handlers
    logger.warning("Auth method '{}' not yet implemented", option.auth_method)
    return {
        "error": f"Auth method '{option.auth_method}' not yet implemented",
        "code": "not_available",
    }
