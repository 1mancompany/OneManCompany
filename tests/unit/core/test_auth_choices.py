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
