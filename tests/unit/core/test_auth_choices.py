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
                    value="fake-key", label="Fake Key", hint="",
                    provider="nonexistent_provider", auth_method="api_key",
                )],
            ),
        ]
        monkeypatch.setattr(ac_mod, "AUTH_CHOICE_GROUPS", fake_groups)
        warnings = validate_registry_consistency()
        assert len(warnings) == 1
        assert "nonexistent_provider" in warnings[0]
