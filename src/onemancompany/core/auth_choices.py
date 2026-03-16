"""AUTH_CHOICE_GROUPS — UI/onboarding flow data for provider auth selection.

Separate from PROVIDER_REGISTRY (config.py) which handles connection parameters.
This module handles UI grouping, auth method options, and availability flags.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AuthChoiceOption:
    """A single auth method option within a provider group."""
    value: str
    label: str
    hint: str = ""
    provider: str = ""
    auth_method: str = "api_key"
    available: bool = True


@dataclass
class AuthChoiceGroup:
    """A provider group containing one or more auth method options."""
    group_id: str
    label: str
    hint: str
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
    """Look up an AuthChoiceOption by its value string."""
    for group in AUTH_CHOICE_GROUPS:
        for option in group.choices:
            if option.value == choice_value:
                return option
    return None


def validate_registry_consistency() -> list[str]:
    """Check that all AUTH_CHOICE_GROUPS group_ids exist in PROVIDER_REGISTRY."""
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
