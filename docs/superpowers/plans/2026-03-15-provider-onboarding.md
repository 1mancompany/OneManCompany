# Provider Onboarding — Four-Step Auth Flow Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace hardcoded OpenRouter/Anthropic provider selection with a data-driven four-step onboarding flow supporting 12+ providers.

**Architecture:** Two independent data registries — `PROVIDER_REGISTRY` (connection params) and `AUTH_CHOICE_GROUPS` (UI/auth flow). Three new API endpoints (`/api/auth/providers`, `/api/auth/verify`, `/api/auth/apply`). Shared `_probe_chat()` function for verification and heartbeat health checks.

**Tech Stack:** Python/FastAPI backend, vanilla JS frontend, raw AsyncOpenAI/AsyncAnthropic clients for lightweight chat probing.

**Spec:** `docs/superpowers/specs/2026-03-15-provider-onboarding-design.md`

---

## File Structure

```
NEW files:
  src/onemancompany/core/auth_choices.py     — AUTH_CHOICE_GROUPS data + resolve functions + startup validator
  src/onemancompany/core/auth_verify.py      — _probe_chat() shared verifier
  src/onemancompany/core/auth_apply/__init__.py — dispatch: auth_method → handler
  src/onemancompany/core/auth_apply/api_key.py  — generic api_key apply (company + employee)
  tests/unit/core/test_auth_choices.py       — auth choices tests
  tests/unit/core/test_auth_verify.py        — probe_chat tests
  tests/unit/core/test_auth_apply.py         — apply handler tests
  tests/unit/api/test_auth_endpoints.py      — API endpoint tests

MODIFIED files:
  src/onemancompany/core/config.py:237-285   — add google/minimax to PROVIDER_REGISTRY
  src/onemancompany/core/config.py:325-344   — add google_api_key/minimax_api_key to Settings
  src/onemancompany/core/heartbeat.py:86-137  — replace _check_*_key() with _probe_chat()
  src/onemancompany/core/heartbeat.py:199-313 — update heartbeat cycle to use new verifier
  src/onemancompany/api/routes.py:1908-2009  — delete old provider/api-key endpoints
  src/onemancompany/api/routes.py:2159-2228  — replace old settings/api endpoints with new auth endpoints
  src/onemancompany/agents/onboarding.py:780-783 — salary calculation for all providers
  frontend/app.js:2419-2457                  — dynamic provider select for employee detail
  frontend/app.js:4074-4228                  — dynamic Settings API panel
  tests/unit/core/test_heartbeat.py          — update for _probe_chat()
```

---

## Chunk 1: Data Layer

### Task 1: AUTH_CHOICE_GROUPS data structure

**Files:**
- Create: `src/onemancompany/core/auth_choices.py`
- Create: `tests/unit/core/test_auth_choices.py`

- [ ] **Step 1: Write failing tests for auth_choices**

```python
# tests/unit/core/test_auth_choices.py
"""Tests for AUTH_CHOICE_GROUPS and resolve functions."""
import pytest


class TestResolveAuthChoice:
    def test_resolve_known_choice(self):
        from onemancompany.core.auth_choices import resolve_auth_choice

        option = resolve_auth_choice("openai-api-key")
        assert option is not None
        assert option.provider == "openai"
        assert option.auth_method == "api_key"
        assert option.available is True

    def test_resolve_oauth_choice(self):
        from onemancompany.core.auth_choices import resolve_auth_choice

        option = resolve_auth_choice("qwen-oauth")
        assert option is not None
        assert option.provider == "qwen"
        assert option.auth_method == "oauth"
        assert option.available is False  # Phase 2

    def test_resolve_unknown_returns_none(self):
        from onemancompany.core.auth_choices import resolve_auth_choice

        assert resolve_auth_choice("nonexistent-provider") is None

    def test_resolve_custom(self):
        from onemancompany.core.auth_choices import resolve_auth_choice

        option = resolve_auth_choice("custom-api-key")
        assert option is not None
        assert option.provider == "custom"
        assert option.auth_method == "api_key"


class TestAuthChoiceGroupsIntegrity:
    def test_all_groups_have_choices(self):
        from onemancompany.core.auth_choices import AUTH_CHOICE_GROUPS

        for group in AUTH_CHOICE_GROUPS:
            assert len(group.choices) > 0, f"Group {group.group_id} has no choices"

    def test_all_choice_values_unique(self):
        from onemancompany.core.auth_choices import AUTH_CHOICE_GROUPS

        values = []
        for group in AUTH_CHOICE_GROUPS:
            for choice in group.choices:
                values.append(choice.value)
        assert len(values) == len(set(values)), f"Duplicate choice values: {values}"

    def test_all_choices_have_explicit_provider(self):
        from onemancompany.core.auth_choices import AUTH_CHOICE_GROUPS

        for group in AUTH_CHOICE_GROUPS:
            for choice in group.choices:
                assert choice.provider, f"Choice {choice.value} missing provider"
                assert choice.auth_method, f"Choice {choice.value} missing auth_method"


class TestValidateRegistryConsistency:
    def test_all_group_ids_in_provider_registry(self):
        from onemancompany.core.auth_choices import AUTH_CHOICE_GROUPS
        from onemancompany.core.config import PROVIDER_REGISTRY

        for group in AUTH_CHOICE_GROUPS:
            if group.group_id == "custom":
                continue
            assert group.group_id in PROVIDER_REGISTRY, (
                f"AUTH_CHOICE_GROUPS group_id '{group.group_id}' "
                f"not found in PROVIDER_REGISTRY"
            )

    def test_choice_provider_matches_group_id(self):
        from onemancompany.core.auth_choices import AUTH_CHOICE_GROUPS

        for group in AUTH_CHOICE_GROUPS:
            for choice in group.choices:
                assert choice.provider == group.group_id, (
                    f"Choice {choice.value} provider '{choice.provider}' "
                    f"doesn't match group_id '{group.group_id}'"
                )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/core/test_auth_choices.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'onemancompany.core.auth_choices'`

- [ ] **Step 3: Implement auth_choices.py**

```python
# src/onemancompany/core/auth_choices.py
"""AUTH_CHOICE_GROUPS — UI/onboarding flow data for provider auth selection.

Separate from PROVIDER_REGISTRY (config.py) which handles connection parameters.
This module handles UI grouping, auth method options, and availability flags.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AuthChoiceOption:
    """A single auth method option within a provider group."""

    value: str                    # unique ID, e.g. "openai-api-key"
    label: str                    # display label, e.g. "API Key"
    hint: str = ""                # tooltip/hint text
    provider: str = ""            # provider key matching PROVIDER_REGISTRY
    auth_method: str = "api_key"  # "api_key" | "oauth" | "setup_token" | "codex"
    available: bool = True        # False = "Coming Soon" (Phase 2)


@dataclass
class AuthChoiceGroup:
    """A provider group containing one or more auth method options."""

    group_id: str                        # matches PROVIDER_REGISTRY key
    label: str                           # display name
    hint: str                            # summary of available auth methods
    choices: list[AuthChoiceOption] = field(default_factory=list)


AUTH_CHOICE_GROUPS: list[AuthChoiceGroup] = [
    AuthChoiceGroup("openai", "OpenAI", "Codex OAuth + API key", [
        AuthChoiceOption("openai-codex", "Codex OAuth", provider="openai", auth_method="codex", available=False),
        AuthChoiceOption("openai-api-key", "API Key", provider="openai", auth_method="api_key"),
    ]),
    AuthChoiceGroup("anthropic", "Anthropic", "Setup-token + API key", [
        AuthChoiceOption("anthropic-setup-token", "Setup Token", provider="anthropic", auth_method="setup_token"),
        AuthChoiceOption("anthropic-api-key", "API Key", provider="anthropic", auth_method="api_key"),
    ]),
    AuthChoiceGroup("kimi", "Moonshot AI (Kimi)", "API key", [
        AuthChoiceOption("kimi-api-key", "API Key", provider="kimi", auth_method="api_key"),
    ]),
    AuthChoiceGroup("deepseek", "DeepSeek", "API key", [
        AuthChoiceOption("deepseek-api-key", "API Key", provider="deepseek", auth_method="api_key"),
    ]),
    AuthChoiceGroup("qwen", "Qwen", "OAuth + API key", [
        AuthChoiceOption("qwen-oauth", "OAuth", provider="qwen", auth_method="oauth", available=False),
        AuthChoiceOption("qwen-api-key", "API Key", provider="qwen", auth_method="api_key"),
    ]),
    AuthChoiceGroup("zhipu", "ZhiPu (GLM)", "API key", [
        AuthChoiceOption("zhipu-api-key", "API Key", provider="zhipu", auth_method="api_key"),
    ]),
    AuthChoiceGroup("groq", "Groq", "API key", [
        AuthChoiceOption("groq-api-key", "API Key", provider="groq", auth_method="api_key"),
    ]),
    AuthChoiceGroup("together", "Together AI", "API key", [
        AuthChoiceOption("together-api-key", "API Key", provider="together", auth_method="api_key"),
    ]),
    AuthChoiceGroup("openrouter", "OpenRouter", "API key", [
        AuthChoiceOption("openrouter-api-key", "API Key", provider="openrouter", auth_method="api_key"),
    ]),
    AuthChoiceGroup("google", "Google Gemini", "OAuth + API key", [
        AuthChoiceOption("google-gemini-oauth", "Gemini CLI OAuth", provider="google", auth_method="oauth", available=False),
        AuthChoiceOption("google-gemini-api-key", "API Key", provider="google", auth_method="api_key"),
    ]),
    AuthChoiceGroup("minimax", "MiniMax", "OAuth + API key", [
        AuthChoiceOption("minimax-oauth", "OAuth", provider="minimax", auth_method="oauth", available=False),
        AuthChoiceOption("minimax-api-key", "API Key", provider="minimax", auth_method="api_key"),
    ]),
    AuthChoiceGroup("custom", "Custom Provider", "Any OpenAI/Anthropic compatible endpoint", [
        AuthChoiceOption("custom-api-key", "Custom API Key", provider="custom", auth_method="api_key"),
    ]),
]


def resolve_auth_choice(choice_value: str) -> AuthChoiceOption | None:
    """Look up an AuthChoiceOption by its value string.

    Uses the explicit provider and auth_method fields — no string parsing.
    """
    for group in AUTH_CHOICE_GROUPS:
        for option in group.choices:
            if option.value == choice_value:
                return option
    return None


def validate_registry_consistency() -> list[str]:
    """Check that all AUTH_CHOICE_GROUPS group_ids exist in PROVIDER_REGISTRY.

    Returns list of warning messages (empty = all good).
    Call at startup to catch configuration drift.
    """
    from onemancompany.core.config import PROVIDER_REGISTRY

    warnings = []
    for group in AUTH_CHOICE_GROUPS:
        if group.group_id == "custom":
            continue
        if group.group_id not in PROVIDER_REGISTRY:
            warnings.append(
                f"AUTH_CHOICE_GROUPS group_id '{group.group_id}' "
                f"not found in PROVIDER_REGISTRY"
            )
    return warnings


def get_auth_groups_json() -> list[dict]:
    """Serialize AUTH_CHOICE_GROUPS for the /api/auth/providers endpoint."""
    result = []
    for group in AUTH_CHOICE_GROUPS:
        result.append({
            "group_id": group.group_id,
            "label": group.label,
            "hint": group.hint,
            "choices": [
                {
                    "value": c.value,
                    "label": c.label,
                    "hint": c.hint,
                    "provider": c.provider,
                    "auth_method": c.auth_method,
                    "available": c.available,
                }
                for c in group.choices
            ],
        })
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/core/test_auth_choices.py -v`
Expected: PASS (all tests except `test_all_group_ids_in_provider_registry` — google/minimax not yet in PROVIDER_REGISTRY)

- [ ] **Step 5: Add google/minimax to PROVIDER_REGISTRY and Settings**

Modify: `src/onemancompany/core/config.py:283-285` — add before closing `}`:

```python
    "google": ProviderConfig(
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        env_key="google_api_key",
        health_url="https://generativelanguage.googleapis.com/v1beta/models",
    ),
    "minimax": ProviderConfig(
        base_url="https://api.minimax.chat/v1",
        env_key="minimax_api_key",
        health_url="https://api.minimax.chat/v1/models",
    ),
```

Modify: `src/onemancompany/core/config.py:344` — add after `together_api_key`:

```python
    google_api_key: str = ""
    minimax_api_key: str = ""
```

- [ ] **Step 6: Run all auth_choices tests**

Run: `.venv/bin/python -m pytest tests/unit/core/test_auth_choices.py -v`
Expected: ALL PASS

- [ ] **Step 7: Commit**

```bash
git add src/onemancompany/core/auth_choices.py tests/unit/core/test_auth_choices.py src/onemancompany/core/config.py
git commit -m "feat: add AUTH_CHOICE_GROUPS data layer + google/minimax providers"
```

---

### Task 2: Shared verification function (_probe_chat)

**Files:**
- Create: `src/onemancompany/core/auth_verify.py`
- Create: `tests/unit/core/test_auth_verify.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/core/test_auth_verify.py
"""Tests for _probe_chat() shared verifier."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock


class TestProbeChat:
    async def test_openai_compatible_success(self):
        from onemancompany.core.auth_verify import probe_chat

        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="hi"))]

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        with patch("onemancompany.core.auth_verify._make_openai_client", return_value=mock_client):
            ok, error = await probe_chat("deepseek", "sk-test", "deepseek-chat")

        assert ok is True
        assert error == ""

    async def test_openai_compatible_invalid_key(self):
        from onemancompany.core.auth_verify import probe_chat

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=Exception("Incorrect API key provided")
        )

        with patch("onemancompany.core.auth_verify._make_openai_client", return_value=mock_client):
            ok, error = await probe_chat("deepseek", "bad-key", "deepseek-chat")

        assert ok is False
        assert "Incorrect API key" in error

    async def test_anthropic_success(self):
        from onemancompany.core.auth_verify import probe_chat

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="hi")]

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with patch("onemancompany.core.auth_verify._make_anthropic_client", return_value=mock_client):
            ok, error = await probe_chat("anthropic", "sk-ant-test", "claude-3-haiku-20240307")

        assert ok is True
        assert error == ""

    async def test_timeout(self):
        import asyncio
        from onemancompany.core.auth_verify import probe_chat

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=asyncio.TimeoutError()
        )

        with patch("onemancompany.core.auth_verify._make_openai_client", return_value=mock_client):
            ok, error = await probe_chat("openai", "sk-test", "gpt-4o", timeout=1.0)

        assert ok is False
        assert "timeout" in error.lower() or "Timeout" in error

    async def test_custom_provider_with_base_url(self):
        from onemancompany.core.auth_verify import probe_chat

        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="hi"))]

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        with patch("onemancompany.core.auth_verify._make_openai_client", return_value=mock_client) as mock_make:
            ok, error = await probe_chat(
                "custom", "sk-test", "my-model",
                base_url="https://api.example.com/v1",
            )

        assert ok is True
        # Verify base_url was passed to client constructor
        mock_make.assert_called_once()
        call_kwargs = mock_make.call_args
        assert "https://api.example.com/v1" in str(call_kwargs)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/core/test_auth_verify.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement auth_verify.py**

```python
# src/onemancompany/core/auth_verify.py
"""Shared chat-probe verifier for provider connectivity.

Used by:
- POST /api/auth/verify (onboarding)
- heartbeat.py (periodic health checks)
"""
from __future__ import annotations

import asyncio

from loguru import logger


def _make_openai_client(api_key: str, base_url: str):
    """Create an async OpenAI client."""
    from openai import AsyncOpenAI

    return AsyncOpenAI(api_key=api_key, base_url=base_url)


def _make_anthropic_client(api_key: str):
    """Create an async Anthropic client."""
    from anthropic import AsyncAnthropic

    return AsyncAnthropic(api_key=api_key)


async def probe_chat(
    provider: str,
    api_key: str,
    model: str,
    *,
    timeout: float = 30.0,
    base_url: str = "",
    chat_class: str = "",
) -> tuple[bool, str]:
    """Send a minimal chat request to verify provider connectivity.

    Args:
        provider: Provider name (key in PROVIDER_REGISTRY, or "custom").
        api_key: API key to test.
        model: Model ID to use for the probe.
        timeout: Request timeout in seconds.
        base_url: Override base URL (required for "custom" provider).
        chat_class: Override chat class ("openai" or "anthropic").

    Returns:
        (ok, error_message) — ok=True means connectivity verified.
    """
    from onemancompany.core.config import get_provider

    # Resolve connection params
    provider_cfg = get_provider(provider)
    resolved_base_url = base_url or (provider_cfg.base_url if provider_cfg else "")
    resolved_chat_class = chat_class or (provider_cfg.chat_class if provider_cfg else "openai")

    try:
        if resolved_chat_class == "anthropic":
            client = _make_anthropic_client(api_key)
            response = await asyncio.wait_for(
                client.messages.create(
                    model=model,
                    max_tokens=1,
                    messages=[{"role": "user", "content": "hi"}],
                ),
                timeout=timeout,
            )
        else:
            client = _make_openai_client(api_key, resolved_base_url)
            response = await asyncio.wait_for(
                client.chat.completions.create(
                    model=model,
                    max_tokens=1,
                    messages=[{"role": "user", "content": "hi"}],
                ),
                timeout=timeout,
            )
        return True, ""
    except asyncio.TimeoutError:
        return False, f"Connection timeout ({timeout}s)"
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        error_msg = str(exc)
        # Truncate long error messages
        if len(error_msg) > 200:
            error_msg = error_msg[:200] + "..."
        logger.debug("probe_chat failed for {}/{}: {}", provider, model, error_msg)
        return False, error_msg
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/core/test_auth_verify.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/onemancompany/core/auth_verify.py tests/unit/core/test_auth_verify.py
git commit -m "feat: add probe_chat() shared verifier for provider connectivity"
```

---

### Task 3: Auth apply handlers

**Files:**
- Create: `src/onemancompany/core/auth_apply/__init__.py`
- Create: `src/onemancompany/core/auth_apply/api_key.py`
- Create: `tests/unit/core/test_auth_apply.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/core/test_auth_apply.py
"""Tests for auth apply handlers."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock


class TestApplyApiKeyCompany:
    async def test_apply_company_level(self):
        from onemancompany.core.auth_apply.api_key import apply_api_key_company

        mock_update_env = MagicMock()
        mock_reload = MagicMock()

        with patch("onemancompany.core.auth_apply.api_key.update_env_var", mock_update_env), \
             patch("onemancompany.core.auth_apply.api_key.reload_settings", mock_reload):
            result = await apply_api_key_company(
                provider="deepseek",
                api_key="sk-test-key",
                model="deepseek-chat",
            )

        assert result["status"] == "applied"
        assert result["scope"] == "company"
        mock_update_env.assert_called_once()
        mock_reload.assert_called_once()


class TestApplyApiKeyEmployee:
    async def test_apply_employee_level(self):
        from onemancompany.core.auth_apply.api_key import apply_api_key_employee

        mock_store = AsyncMock()

        with patch("onemancompany.core.auth_apply.api_key._store", mock_store), \
             patch("onemancompany.core.auth_apply.api_key._rebuild_employee_agent"):
            result = await apply_api_key_employee(
                provider="deepseek",
                employee_id="00010",
                api_key="sk-test-key",
                model="deepseek-chat",
            )

        assert result["status"] == "applied"
        assert result["scope"] == "employee"
        mock_store.save_employee.assert_called_once()

    async def test_apply_employee_no_model_keeps_existing(self):
        from onemancompany.core.auth_apply.api_key import apply_api_key_employee

        mock_store = AsyncMock()

        with patch("onemancompany.core.auth_apply.api_key._store", mock_store), \
             patch("onemancompany.core.auth_apply.api_key._rebuild_employee_agent"):
            result = await apply_api_key_employee(
                provider="deepseek",
                employee_id="00010",
                api_key="sk-test-key",
            )

        # save_employee should NOT include llm_model when not provided
        call_args = mock_store.save_employee.call_args
        saved_data = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("data", {})
        assert "llm_model" not in saved_data


class TestApplyDispatch:
    async def test_dispatch_api_key(self):
        from onemancompany.core.auth_apply import apply_auth_choice

        with patch("onemancompany.core.auth_apply.api_key.apply_api_key_company", new_callable=AsyncMock) as mock_apply:
            mock_apply.return_value = {"status": "applied"}
            result = await apply_auth_choice(
                choice_value="deepseek-api-key",
                scope="company",
                api_key="sk-test",
            )

        assert result["status"] == "applied"
        mock_apply.assert_called_once()

    async def test_dispatch_unavailable_choice(self):
        from onemancompany.core.auth_apply import apply_auth_choice

        result = await apply_auth_choice(
            choice_value="qwen-oauth",
            scope="company",
            api_key="",
        )

        assert result.get("error")
        assert "not_available" in result.get("code", "")

    async def test_dispatch_unknown_choice(self):
        from onemancompany.core.auth_apply import apply_auth_choice

        result = await apply_auth_choice(
            choice_value="nonexistent",
            scope="company",
            api_key="",
        )

        assert result.get("error")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/core/test_auth_apply.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement auth_apply package**

```python
# src/onemancompany/core/auth_apply/__init__.py
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
```

```python
# src/onemancompany/core/auth_apply/api_key.py
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
        PROVIDER_REGISTRY,
        get_provider,
        reload_settings,
        update_env_var,
    )

    provider_cfg = get_provider(provider)
    if not provider_cfg and provider != "custom":
        return {"error": "Unknown provider", "code": "invalid_provider"}

    if provider == "custom":
        # Custom providers: store base_url and chat_class in settings
        # For now, store under a generic custom key
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/core/test_auth_apply.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/onemancompany/core/auth_apply/ tests/unit/core/test_auth_apply.py
git commit -m "feat: add auth apply dispatch + api_key handler"
```

---

## Chunk 2: API Endpoints

### Task 4: New auth API endpoints

**Files:**
- Modify: `src/onemancompany/api/routes.py`
- Create: `tests/unit/api/test_auth_endpoints.py`

- [ ] **Step 1: Write failing tests for endpoints**

```python
# tests/unit/api/test_auth_endpoints.py
"""Tests for /api/auth/* endpoints."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from httpx import AsyncClient, ASGITransport


def _make_test_app():
    """Create a minimal FastAPI app with auth routes."""
    from fastapi import FastAPI
    from onemancompany.api.routes import router

    app = FastAPI()
    app.include_router(router)
    return app


class TestGetProviders:
    async def test_returns_all_groups(self):
        app = _make_test_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/auth/providers")

        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 12  # 12 provider groups

        # Check structure
        first = data[0]
        assert "group_id" in first
        assert "label" in first
        assert "choices" in first
        assert isinstance(first["choices"], list)

    async def test_choices_have_available_field(self):
        app = _make_test_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/auth/providers")

        data = resp.json()
        for group in data:
            for choice in group["choices"]:
                assert "available" in choice
                assert "provider" in choice
                assert "auth_method" in choice


class TestVerifyEndpoint:
    async def test_verify_success(self):
        app = _make_test_app()
        transport = ASGITransport(app=app)

        with patch("onemancompany.api.routes.probe_chat", new_callable=AsyncMock) as mock_probe:
            mock_probe.return_value = (True, "")
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/api/auth/verify", json={
                    "provider": "deepseek",
                    "auth_method": "api_key",
                    "api_key": "sk-test",
                    "model": "deepseek-chat",
                })

        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    async def test_verify_failure(self):
        app = _make_test_app()
        transport = ASGITransport(app=app)

        with patch("onemancompany.api.routes.probe_chat", new_callable=AsyncMock) as mock_probe:
            mock_probe.return_value = (False, "Invalid API key")
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/api/auth/verify", json={
                    "provider": "deepseek",
                    "auth_method": "api_key",
                    "api_key": "bad-key",
                    "model": "deepseek-chat",
                })

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert "Invalid API key" in data["error"]


class TestApplyEndpoint:
    async def test_apply_company(self):
        app = _make_test_app()
        transport = ASGITransport(app=app)

        with patch("onemancompany.core.auth_apply.apply_auth_choice", new_callable=AsyncMock) as mock_apply:
            mock_apply.return_value = {"status": "applied", "scope": "company"}
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/api/auth/apply", json={
                    "scope": "company",
                    "choice": "deepseek-api-key",
                    "api_key": "sk-test",
                })

        assert resp.status_code == 200
        assert resp.json()["status"] == "applied"

    async def test_apply_employee(self):
        app = _make_test_app()
        transport = ASGITransport(app=app)

        with patch("onemancompany.core.auth_apply.apply_auth_choice", new_callable=AsyncMock) as mock_apply:
            mock_apply.return_value = {"status": "applied", "scope": "employee"}
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/api/auth/apply", json={
                    "scope": "employee",
                    "employee_id": "00010",
                    "choice": "deepseek-api-key",
                    "api_key": "sk-test",
                    "model": "deepseek-chat",
                })

        assert resp.status_code == 200
        assert resp.json()["status"] == "applied"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/api/test_auth_endpoints.py -v`
Expected: FAIL (endpoints don't exist yet)

- [ ] **Step 3: Add new auth endpoints to routes.py**

Add to `src/onemancompany/api/routes.py` (after imports, before existing endpoints):

```python
# --- Auth Onboarding Endpoints ---

@router.get("/api/auth/providers")
async def get_auth_providers() -> list[dict]:
    """Return AUTH_CHOICE_GROUPS for the provider selection UI."""
    from onemancompany.core.auth_choices import get_auth_groups_json

    return get_auth_groups_json()


@router.post("/api/auth/verify")
async def verify_auth(body: dict) -> dict:
    """Verify provider connectivity with a minimal chat request."""
    from onemancompany.core.auth_verify import probe_chat

    provider = body.get("provider", "")
    api_key = body.get("api_key", "")
    model = body.get("model", "")
    base_url = body.get("base_url", "")
    chat_class = body.get("chat_class", "")

    if not provider or not api_key or not model:
        return {"ok": False, "error": "provider, api_key, and model are required"}

    ok, error = await probe_chat(
        provider, api_key, model,
        base_url=base_url,
        chat_class=chat_class,
    )
    return {"ok": ok, "error": error} if not ok else {"ok": True}


@router.post("/api/auth/apply")
async def apply_auth(body: dict) -> dict:
    """Apply an auth choice (persist key/config)."""
    from onemancompany.core.auth_apply import apply_auth_choice

    return await apply_auth_choice(
        choice_value=body.get("choice", ""),
        scope=body.get("scope", ""),
        api_key=body.get("api_key", ""),
        model=body.get("model", ""),
        employee_id=body.get("employee_id", ""),
        base_url=body.get("base_url", ""),
        chat_class=body.get("chat_class", ""),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/api/test_auth_endpoints.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/onemancompany/api/routes.py tests/unit/api/test_auth_endpoints.py
git commit -m "feat: add /api/auth/providers, /api/auth/verify, /api/auth/apply endpoints"
```

---

### Task 5: Delete old provider endpoints

**Files:**
- Modify: `src/onemancompany/api/routes.py:1908-2009`

- [ ] **Step 1: Delete old endpoints**

Remove the following functions from `routes.py`:
- `update_employee_provider()` (lines 1908–1959) — `PUT /api/employee/{id}/provider`
- `update_employee_api_key()` (lines 1962–2009) — `PUT /api/employee/{id}/api-key`

Also remove the old settings API test endpoint:
- `test_api_connection()` (lines 2181–2194) — `POST /api/settings/api/test`

Replace `test_api_connection` with a redirect to `/api/auth/verify`.

- [ ] **Step 2: Run full test suite to find broken tests**

Run: `.venv/bin/python -m pytest tests/ -x -q`
Expected: Some tests may reference old endpoints — fix them.

- [ ] **Step 3: Fix any broken tests**

Update any tests that reference the old endpoints to use the new `/api/auth/*` endpoints instead.

- [ ] **Step 4: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -x -q`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/onemancompany/api/routes.py tests/
git commit -m "refactor: delete old provider/api-key endpoints, replaced by /api/auth/*"
```

---

## Chunk 3: Heartbeat Migration

### Task 6: Replace heartbeat health checks with _probe_chat

**Files:**
- Modify: `src/onemancompany/core/heartbeat.py:86-137`
- Modify: `src/onemancompany/core/heartbeat.py:199-313`
- Modify: `tests/unit/core/test_heartbeat.py`

- [ ] **Step 1: Write failing tests for new heartbeat flow**

Add to `tests/unit/core/test_heartbeat.py`:

```python
class TestCheckProviderOnline:
    """Tests for _check_provider_online using probe_chat."""

    async def test_batched_check_calls_probe(self):
        """Batched company-key check should call probe_chat once per provider."""
        from onemancompany.core.heartbeat import _check_provider_online

        with patch("onemancompany.core.heartbeat.probe_chat", new_callable=AsyncMock) as mock_probe:
            mock_probe.return_value = (True, "")
            result = await _check_provider_online("deepseek", "sk-company-key", "deepseek-chat")

        assert result is True
        mock_probe.assert_called_once_with("deepseek", "sk-company-key", "deepseek-chat", timeout=15.0)

    async def test_batched_check_failure(self):
        from onemancompany.core.heartbeat import _check_provider_online

        with patch("onemancompany.core.heartbeat.probe_chat", new_callable=AsyncMock) as mock_probe:
            mock_probe.return_value = (False, "Invalid API key")
            result = await _check_provider_online("deepseek", "sk-bad", "deepseek-chat")

        assert result is False

    async def test_missing_key_returns_false(self):
        from onemancompany.core.heartbeat import _check_provider_online

        result = await _check_provider_online("deepseek", "", "deepseek-chat")
        assert result is False

    async def test_missing_model_returns_false(self):
        from onemancompany.core.heartbeat import _check_provider_online

        result = await _check_provider_online("deepseek", "sk-test", "")
        assert result is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/core/test_heartbeat.py::TestCheckProviderOnline -v`
Expected: FAIL

- [ ] **Step 3: Refactor heartbeat.py**

In `src/onemancompany/core/heartbeat.py`:

1. Delete `_check_provider_key()` (lines 86–126)
2. Delete `_check_openrouter_key()` (lines 130–132)
3. Delete `_check_anthropic_key()` (lines 135–137)
4. Add import: `from onemancompany.core.auth_verify import probe_chat`
5. Add `_check_provider_online()` — a thin wrapper preserving the batching pattern:

```python
async def _check_provider_online(provider: str, api_key: str, model: str) -> bool:
    """Check provider connectivity via probe_chat.

    Used by heartbeat cycle. Preserves the batching pattern: called once per
    company-level key, result shared across all employees using that key.
    """
    if not api_key or not model:
        return False
    ok, _error = await probe_chat(provider, api_key, model, timeout=15.0)
    return ok
```

6. In `run_heartbeat_cycle()`, replace calls at lines 288 and 294:

```python
# Line 288 (batched company-key check):
# OLD: online = await _check_provider_key(provider, company_key)
# NEW: need a default model for the probe — use first employee's model in the group
first_emp_cfg = employee_configs.get(emp_ids[0])
default_model = first_emp_cfg.llm_model if first_emp_cfg else ""
online = await _check_provider_online(provider, company_key, default_model)

# Line 294 (per-employee check):
# OLD: online = await _check_provider_key(provider, key)
# NEW:
emp_cfg = employee_configs.get(emp_id)
emp_model = emp_cfg.llm_model if emp_cfg else ""
online = await _check_provider_online(provider, key, emp_model)
```

**Key design point**: The batching structure is preserved — one `probe_chat()` call per company-level key, shared across all employees on that key. Per-employee keys still get individual checks. The only change is the check mechanism: `/models` GET → `max_tokens=1` chat completion.

- [ ] **Step 4: Update existing heartbeat tests**

Update tests that reference `_check_provider_key`, `_check_openrouter_key`, `_check_anthropic_key`:
- `TestCheckProviderKey` class → rename to `TestCheckProviderOnline`, mock `probe_chat` instead of `httpx`
- Any tests mocking `_check_provider_key` in `run_heartbeat_cycle` tests → mock `_check_provider_online` instead
- Remove references to `_check_openrouter_key` and `_check_anthropic_key` legacy aliases

- [ ] **Step 5: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -x -q`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add src/onemancompany/core/heartbeat.py tests/unit/core/test_heartbeat.py
git commit -m "refactor: heartbeat uses probe_chat() instead of /models endpoint checks"
```

---

## Chunk 4: Frontend

### Task 7: Dynamic provider select in employee detail panel

**Files:**
- Modify: `frontend/app.js:2419-2457`

- [ ] **Step 1: Replace hardcoded select with dynamic provider list**

Replace the hardcoded `<select>` in `_renderFallbackModelSection()` (lines 2419–2422):

```javascript
// OLD:
// <select id="emp-detail-provider" class="emp-model-select" style="flex:1;">
//   <option value="openrouter"${...}>OpenRouter</option>
//   <option value="anthropic"${...}>Anthropic</option>
// </select>

// NEW: fetch from /api/auth/providers and build dynamically
const providerSelect = document.getElementById('emp-detail-provider');
fetch('/api/auth/providers')
  .then(r => r.json())
  .then(groups => {
    providerSelect.innerHTML = groups.map(g =>
      `<option value="${g.group_id}"${g.group_id === currentProvider ? ' selected' : ''}>${g.label}</option>`
    ).join('');
  });
```

- [ ] **Step 2: Update provider change handler (lines 2434–2457)**

Replace the PUT to `/api/employee/{id}/provider` with the four-step flow:
1. Show auth method selection (if multiple choices)
2. Show API key input
3. Call `POST /api/auth/verify`
4. Call `POST /api/auth/apply` with `scope: "employee"`

- [ ] **Step 3: Manual test in browser**

Start the server and verify:
- Employee detail shows all providers in dropdown
- Selecting a provider triggers the four-step flow
- Verification spinner works
- Key is saved on success

- [ ] **Step 4: Commit**

```bash
git add frontend/app.js
git commit -m "feat: dynamic provider selection in employee detail panel"
```

---

### Task 8: Dynamic Settings API panel

**Files:**
- Modify: `frontend/app.js:4074-4228`

- [ ] **Step 1: Replace hardcoded provider cards**

Replace `_renderApiSettings()` (lines 4074–4157) to dynamically render provider cards from `/api/auth/providers`:

```javascript
async _renderApiSettings() {
  const groups = await fetch('/api/auth/providers').then(r => r.json());
  let html = '<div class="api-settings-grid">';
  for (const group of groups) {
    const availableChoices = group.choices.filter(c => c.available);
    const isConfigured = false; // TODO: check from /api/settings/api
    html += `
      <div class="api-provider-card" data-group="${group.group_id}">
        <div class="provider-header">
          <span class="provider-name">${group.label}</span>
          <span class="provider-hint">${group.hint}</span>
          ${isConfigured ? '<span class="provider-status">✓</span>' : ''}
        </div>
        <button class="pixel-btn small" onclick="app._startProviderSetup('${group.group_id}')">
          ${isConfigured ? 'Update' : 'Configure'}
        </button>
      </div>
    `;
  }
  html += '</div>';
  return html;
}
```

- [ ] **Step 2: Implement _startProviderSetup() four-step flow**

```javascript
async _startProviderSetup(groupId) {
  const groups = await fetch('/api/auth/providers').then(r => r.json());
  const group = groups.find(g => g.group_id === groupId);
  if (!group) return;

  const availableChoices = group.choices.filter(c => c.available);

  // Step 1 already done (group selected)
  // Step 2: auto-select if only one choice, otherwise prompt
  let choice;
  if (availableChoices.length === 1) {
    choice = availableChoices[0];
  } else {
    // Show choice selection UI
    choice = await this._promptAuthMethod(availableChoices);
    if (!choice) return;
  }

  // Step 3: prompt for API key
  const apiKey = await this._promptApiKey(group.label);
  if (!apiKey) return;

  // Step 4: verify
  const model = await this._promptModel(groupId);
  const verifyResult = await fetch('/api/auth/verify', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      provider: choice.provider,
      auth_method: choice.auth_method,
      api_key: apiKey,
      model: model,
    }),
  }).then(r => r.json());

  if (!verifyResult.ok) {
    this.logEntry('SYSTEM', `Verification failed: ${verifyResult.error}`, 'system');
    return;
  }

  // Apply
  await fetch('/api/auth/apply', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      scope: 'company',
      choice: choice.value,
      api_key: apiKey,
      model: model,
    }),
  });

  this.logEntry('CEO', `${group.label} configured successfully`, 'ceo');
}
```

- [ ] **Step 3: Replace _saveApiSettings and _testApiConnection**

Delete `_saveApiSettings()` (lines 4173–4204) and `_testApiConnection()` (lines 4206–4228) — replaced by `_startProviderSetup()`.

- [ ] **Step 4: Manual test in browser**

Start the server and verify:
- Settings panel shows all providers as cards
- Clicking Configure triggers four-step flow
- Verification works
- Key is persisted

- [ ] **Step 5: Commit**

```bash
git add frontend/app.js
git commit -m "feat: dynamic Settings API panel with four-step provider setup"
```

---

## Chunk 5: Cleanup and Polish

### Task 9: Update onboarding salary calculation

**Files:**
- Modify: `src/onemancompany/agents/onboarding.py:780-783`

- [ ] **Step 1: Fix salary calculation to handle all providers**

Replace lines 780–783:

```python
# OLD:
# if api_provider == "openrouter":
#     salary = compute_salary(llm_model) if llm_model else 0.0
# else:
#     salary = 0.0

# NEW: compute salary for any provider that has pricing data
salary = compute_salary(llm_model) if llm_model else 0.0
```

The `compute_salary()` function in `model_costs.py` already handles unknown models by returning 0.0, so no guard needed.

- [ ] **Step 2: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/agents/test_onboarding.py -v`
Expected: ALL PASS

- [ ] **Step 3: Commit**

```bash
git add src/onemancompany/agents/onboarding.py
git commit -m "fix: salary calculation works for all providers, not just openrouter"
```

---

### Task 10: Startup validation + integration test

**Files:**
- Modify: `src/onemancompany/api/routes.py` (startup event)

- [ ] **Step 1: Add startup validation**

In routes.py startup event (or app initialization), add:

```python
from onemancompany.core.auth_choices import validate_registry_consistency
warnings = validate_registry_consistency()
for w in warnings:
    logger.warning("Auth config: {}", w)
```

- [ ] **Step 2: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -x -q`
Expected: ALL PASS

- [ ] **Step 3: Final commit**

```bash
git add src/onemancompany/api/routes.py
git commit -m "feat: startup validation for AUTH_CHOICE_GROUPS ↔ PROVIDER_REGISTRY consistency"
```

---

### Task 11: End-to-end verification

- [ ] **Step 1: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: ALL PASS, no regressions

- [ ] **Step 2: Start server and manual test**

```bash
.venv/bin/python -m onemancompany
```

Verify:
1. `GET /api/auth/providers` returns all 12 groups
2. Settings panel shows dynamic provider cards
3. Employee detail shows dynamic provider dropdown
4. Four-step flow works end-to-end for at least one provider
5. Heartbeat continues to work (check server.log)

- [ ] **Step 3: Commit any final fixes**

```bash
git add src/onemancompany/ frontend/ tests/
git commit -m "chore: final polish for provider onboarding four-step flow"
```
