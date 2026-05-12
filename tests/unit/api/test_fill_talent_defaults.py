"""Tests for _fill_talent_defaults."""
import pytest
from unittest.mock import patch, MagicMock


class TestFillTalentDefaults:

    @patch("onemancompany.core.config.settings")
    def test_fills_all_missing_fields(self, mock_settings):
        from onemancompany.api.routes import _fill_talent_defaults
        mock_settings.default_llm_model = "test/model"
        mock_settings.default_api_provider = "openai"

        data = {"name": "Test Talent"}
        _fill_talent_defaults(data)
        assert data["llm_model"] == "test/model"
        assert data["api_provider"] == "openai"
        assert data["auth_method"] == "api_key"

    @patch("onemancompany.core.config.settings")
    def test_overrides_existing_fields_with_company_defaults(self, mock_settings):
        from onemancompany.api.routes import _fill_talent_defaults
        mock_settings.default_llm_model = "test/model"
        mock_settings.default_api_provider = "openai"

        data = {"llm_model": "custom/model", "api_provider": "anthropic"}
        _fill_talent_defaults(data)
        assert data["llm_model"] == "test/model"
        assert data["api_provider"] == "openai"
        assert data["auth_method"] == "api_key"

    @patch("onemancompany.core.config.settings")
    def test_preserves_existing_fields_when_talent_config_requested(self, mock_settings):
        from onemancompany.api.routes import _fill_talent_defaults
        mock_settings.default_llm_model = "test/model"
        mock_settings.default_api_provider = "openai"

        data = {"llm_model": "custom/model", "api_provider": "anthropic"}
        _fill_talent_defaults(data, use_talent_llm_config=True)
        assert data["llm_model"] == "custom/model"
        assert data["api_provider"] == "anthropic"
        assert data["auth_method"] == "api_key"

    def test_skips_self_hosted(self):
        from onemancompany.api.routes import _fill_talent_defaults

        data = {"hosting": "self"}
        _fill_talent_defaults(data)
        assert "llm_model" not in data
        assert "api_provider" not in data

    @patch("onemancompany.core.config.settings")
    def test_fills_empty_string_fields(self, mock_settings):
        from onemancompany.api.routes import _fill_talent_defaults
        mock_settings.default_llm_model = "test/model"
        mock_settings.default_api_provider = "openai"

        data = {"llm_model": "", "api_provider": ""}
        _fill_talent_defaults(data)
        assert data["llm_model"] == "test/model"
        assert data["api_provider"] == "openai"
