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
        assert option.available is False

    def test_resolve_unknown_returns_none(self):
        from onemancompany.core.auth_choices import resolve_auth_choice

        assert resolve_auth_choice("nonexistent-provider") is None

    def test_resolve_custom(self):
        from onemancompany.core.auth_choices import resolve_auth_choice

        option = resolve_auth_choice("custom-api-key")
        assert option is not None
        assert option.provider == "custom"
        assert option.auth_method == "api_key"

    def test_resolve_azure(self):
        from onemancompany.core.auth_choices import resolve_auth_choice

        option = resolve_auth_choice("azure-api-key")
        assert option is not None
        assert option.provider == "azure"
        assert option.auth_method == "api_key"
        assert option.available is True

    def test_azure_in_provider_registry(self):
        """Unlike 'custom', 'azure' is a first-class registry provider."""
        from onemancompany.core.config import PROVIDER_REGISTRY, get_provider

        assert "azure" in PROVIDER_REGISTRY
        prov = get_provider("azure")
        assert prov.chat_class == "openai"
        assert prov.env_key == "azure_api_key"


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

    def test_api_key_choice_follows_group_id_convention(self):
        """Regression #405: the Settings UI saves a key by POSTing
        choice=f'{group_id}-api-key' (frontend _saveProviderKey). Every group that
        has an api_key option MUST expose it under exactly that value, or Save fails
        with 'Unknown auth choice'. Google Gemini used 'google-gemini-api-key'."""
        from onemancompany.core.auth_choices import AUTH_CHOICE_GROUPS, resolve_auth_choice

        for group in AUTH_CHOICE_GROUPS:
            api_key_opts = [c for c in group.choices if c.auth_method == "api_key"]
            if not api_key_opts:
                continue
            expected = f"{group.group_id}-api-key"
            resolved = resolve_auth_choice(expected)
            assert resolved is not None and resolved.auth_method == "api_key", (
                f"Group '{group.group_id}' has an api_key option but "
                f"resolve_auth_choice('{expected}') did not resolve it — the Settings "
                f"UI cannot save this provider's key."
            )


class TestGoogleGeminiAuthChoice:
    def test_resolve_google_api_key(self):
        """The exact value the frontend constructs for Google Gemini must resolve."""
        from onemancompany.core.auth_choices import resolve_auth_choice

        option = resolve_auth_choice("google-api-key")
        assert option is not None
        assert option.provider == "google"
        assert option.auth_method == "api_key"
        assert option.available is True


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


class TestValidateRegistryConsistencyFunction:
    def test_validate_returns_no_warnings(self):
        """Lines 87-95: validate_registry_consistency returns no warnings for valid setup."""
        from onemancompany.core.auth_choices import validate_registry_consistency
        warnings = validate_registry_consistency()
        # 'custom' group_id is intentionally not in PROVIDER_REGISTRY
        assert all("custom" in w for w in warnings) or len(warnings) == 0

    def test_validate_detects_missing_group(self, monkeypatch):
        """Lines 90-94: detects group_id not in PROVIDER_REGISTRY."""
        from onemancompany.core import auth_choices as ac_mod
        from onemancompany.core.auth_choices import (
            AuthChoiceGroup, AuthChoiceOption, validate_registry_consistency,
        )
        fake_groups = [
            AuthChoiceGroup(
                group_id="nonexistent_provider",
                label="Fake",
                hint="",
                choices=[AuthChoiceOption(
                    # Value follows the {group_id}-api-key convention so only the
                    # registry-missing warning fires (see the dedicated convention test).
                    value="nonexistent_provider-api-key", label="Fake Key", hint="",
                    provider="nonexistent_provider", auth_method="api_key",
                )],
            ),
        ]
        monkeypatch.setattr(ac_mod, "AUTH_CHOICE_GROUPS", fake_groups)
        warnings = validate_registry_consistency()
        assert len(warnings) == 1
        assert "nonexistent_provider" in warnings[0]
        assert "not found in PROVIDER_REGISTRY" in warnings[0]
