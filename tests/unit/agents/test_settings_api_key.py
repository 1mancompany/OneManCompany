"""Regression tests for API key settings — save, test, and status display.

Bug: API keys saved through settings UI don't take effect because base.py
imports `settings` at module level, getting a stale reference after reload.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Bug 1: Stale settings import — _resolve_provider_key uses old settings
# ---------------------------------------------------------------------------


def test_resolve_provider_key_sees_reloaded_settings():
    """After reload_settings(), _resolve_provider_key must see the new key."""
    import onemancompany.core.config as cfg_mod
    from onemancompany.agents.base import _resolve_provider_key

    # Simulate: settings originally has no openrouter key
    original_settings = cfg_mod.Settings()
    original_settings.openrouter_api_key = ""
    cfg_mod.settings = original_settings

    # Re-import to pick up the module-level binding
    import importlib
    import onemancompany.agents.base as base_mod
    importlib.reload(base_mod)

    assert base_mod._resolve_provider_key("openrouter", "") == ""

    # Now simulate saving a key via update_env_var → reload_settings
    new_settings = cfg_mod.Settings()
    new_settings.openrouter_api_key = "sk-new-key-12345"
    cfg_mod.settings = new_settings

    # BUG: base_mod._resolve_provider_key still uses old settings (stale import)
    result = base_mod._resolve_provider_key("openrouter", "")
    assert result == "sk-new-key-12345", (
        f"Expected new key after reload, got '{result}' — stale settings import"
    )


# ---------------------------------------------------------------------------
# Bug 2: Test button sends model='test' which always fails
# ---------------------------------------------------------------------------


def test_test_provider_key_uses_valid_model():
    """The test/verify endpoint should use a model that the provider can handle,
    or use a health check endpoint instead of a chat completion."""
    from onemancompany.core.config import PROVIDER_REGISTRY

    # The frontend sends model='test' for probe_chat.
    # probe_chat tries to create a chat completion with model='test'.
    # No provider has a model called 'test', so it always fails.
    # The fix should either:
    # a) Use the provider's health_url for verification, or
    # b) Use a sensible default model name

    # Verify all providers have health_url configured
    for name, prov in PROVIDER_REGISTRY.items():
        assert prov.health_url, f"Provider '{name}' missing health_url for key verification"


def _mock_httpx_client(status_code, text=""):
    """Create a mock httpx.AsyncClient context manager."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.text = text

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    mock_httpx = MagicMock()
    mock_httpx.AsyncClient = MagicMock(return_value=mock_client)
    return mock_httpx


@pytest.mark.asyncio
async def test_probe_health_returns_ok_on_200():
    """probe_health returns (True, '') when health endpoint returns 200."""
    import onemancompany.core.auth_verify as verify_mod

    mock_httpx = _mock_httpx_client(200)
    with patch.dict("sys.modules", {"httpx": mock_httpx}):
        ok, error = await verify_mod.probe_health("openrouter", "sk-test-key")

    assert ok is True
    assert error == ""


@pytest.mark.asyncio
async def test_probe_health_returns_fail_on_401():
    """probe_health returns (False, ...) when health endpoint returns 401."""
    import onemancompany.core.auth_verify as verify_mod

    mock_httpx = _mock_httpx_client(401, text="Unauthorized")
    with patch.dict("sys.modules", {"httpx": mock_httpx}):
        ok, error = await verify_mod.probe_health("openrouter", "sk-bad-key")

    assert ok is False
    assert "401" in error


# ---------------------------------------------------------------------------
# Bug 3: GET /api/settings/api only returns openrouter/anthropic
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_api_settings_returns_all_providers():
    """GET /api/settings/api should return status for all registered providers,
    not just hardcoded openrouter and anthropic."""
    from onemancompany.core.config import PROVIDER_REGISTRY

    with patch("onemancompany.api.routes._get_talent_market_connected", return_value=False), \
         patch("onemancompany.api.routes._get_local_talent_count", return_value=0):
        from onemancompany.api.routes import get_api_settings
        result = await get_api_settings()

    # Should have an entry for every provider in PROVIDER_REGISTRY
    for provider_name in PROVIDER_REGISTRY:
        assert provider_name in result, (
            f"Provider '{provider_name}' missing from GET /api/settings/api response. "
            f"Only hardcoded providers are returned."
        )


# ---------------------------------------------------------------------------
# Founding employee sync — single source of truth
# ---------------------------------------------------------------------------


def test_sync_founding_defaults_updates_profiles(tmp_path):
    """sync_founding_defaults writes provider/model to founding employee profiles."""
    import yaml
    from unittest.mock import patch as _patch

    # Create a fake founding employee profile
    emp_dir = tmp_path / "00001"
    emp_dir.mkdir()
    profile = emp_dir / "profile.yaml"
    profile.write_text(yaml.dump({
        "name": "HR",
        "api_provider": "openrouter",
        "llm_model": "old-model",
    }))

    with _patch("onemancompany.core.config.EMPLOYEES_DIR", tmp_path), \
         _patch("onemancompany.core.config.FOUNDING_IDS", frozenset({"00001"})):
        from onemancompany.core.config import sync_founding_defaults
        count = sync_founding_defaults(provider="deepseek", model="deepseek-chat")

    assert count == 1
    updated = yaml.safe_load(profile.read_text())
    assert updated["api_provider"] == "deepseek"
    assert updated["llm_model"] == "deepseek-chat"


def test_sync_founding_defaults_skips_unchanged(tmp_path):
    """sync_founding_defaults doesn't rewrite profiles that already match."""
    import yaml
    from unittest.mock import patch as _patch

    emp_dir = tmp_path / "00001"
    emp_dir.mkdir()
    profile = emp_dir / "profile.yaml"
    profile.write_text(yaml.dump({
        "name": "HR",
        "api_provider": "openrouter",
        "llm_model": "gpt-4",
    }))

    with _patch("onemancompany.core.config.EMPLOYEES_DIR", tmp_path), \
         _patch("onemancompany.core.config.FOUNDING_IDS", frozenset({"00001"})):
        from onemancompany.core.config import sync_founding_defaults
        count = sync_founding_defaults(provider="openrouter", model="gpt-4")

    assert count == 0


# ---------------------------------------------------------------------------
# Provider model list endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_provider_models_returns_normalized_list():
    """GET /api/models/{provider} returns normalized model list for any provider."""
    from onemancompany.api.routes import _fetch_provider_models as list_provider_models

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "data": [
            {"id": "deepseek-chat", "object": "model"},
            {"id": "deepseek-coder", "object": "model"},
        ]
    }

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_resp)

    mock_settings = MagicMock()
    mock_settings.deepseek_api_key = "sk-test"

    mock_httpx = MagicMock()
    mock_httpx.AsyncClient = MagicMock(return_value=mock_client)

    with patch.dict("sys.modules", {"httpx": mock_httpx}), \
         patch("onemancompany.core.config.settings", mock_settings):
        result = await list_provider_models("deepseek")

    assert len(result["models"]) == 2
    assert result["models"][0]["id"] == "deepseek-chat"


@pytest.mark.asyncio
async def test_list_provider_models_google_format():
    """Google returns {models: [{name: 'models/gemini-...', displayName: ...}]}."""
    from onemancompany.api.routes import _fetch_provider_models as list_provider_models

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "models": [
            {"name": "models/gemini-2.0-flash", "displayName": "Gemini 2.0 Flash"},
        ]
    }

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_resp)

    mock_settings = MagicMock()
    mock_settings.google_api_key = "test-key"

    mock_httpx = MagicMock()
    mock_httpx.AsyncClient = MagicMock(return_value=mock_client)

    with patch.dict("sys.modules", {"httpx": mock_httpx}), \
         patch("onemancompany.core.config.settings", mock_settings):
        result = await list_provider_models("google")

    assert len(result["models"]) == 1
    assert result["models"][0]["id"] == "gemini-2.0-flash"  # models/ prefix stripped
    assert result["models"][0]["name"] == "Gemini 2.0 Flash"


@pytest.mark.asyncio
async def test_list_provider_models_unknown_provider():
    """Unknown provider returns empty list with error."""
    from onemancompany.api.routes import _fetch_provider_models as list_provider_models
    result = await list_provider_models("nonexistent")
    assert result["models"] == []
    assert "error" in result
