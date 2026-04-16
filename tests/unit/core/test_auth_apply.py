"""Tests for auth apply handlers."""
from unittest.mock import AsyncMock, patch, MagicMock


class TestApplyApiKeyCompany:
    async def test_apply_company_level(self):
        from onemancompany.core.auth_apply.api_key import apply_api_key_company

        mock_update_env = MagicMock()

        with patch("onemancompany.core.config.update_env_var", mock_update_env):
            result = await apply_api_key_company(
                provider="deepseek",
                api_key="sk-test-key",
                model="deepseek-chat",
            )

        assert result["status"] == "applied"
        assert result["scope"] == "company"
        mock_update_env.assert_called_once()


class TestApplyApiKeyEmployee:
    async def test_apply_employee_level(self):
        from onemancompany.core.auth_apply.api_key import apply_api_key_employee

        mock_store = AsyncMock()

        with patch("onemancompany.core.auth_apply.api_key._store", mock_store), \
             patch("onemancompany.api.routes._rebuild_employee_agent"):
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
             patch("onemancompany.api.routes._rebuild_employee_agent"):
            result = await apply_api_key_employee(
                provider="deepseek",
                employee_id="00010",
                api_key="sk-test-key",
            )

        # save_employee should NOT include llm_model when not provided
        call_args = mock_store.save_employee.call_args
        saved_data = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("data", {})
        assert "llm_model" not in saved_data


class TestApplyApiKeyCompanyEdgeCases:
    async def test_unknown_provider_returns_error(self):
        """Line 30: unknown provider returns error."""
        from onemancompany.core.auth_apply.api_key import apply_api_key_company
        with patch("onemancompany.core.config.get_provider", return_value=None):
            result = await apply_api_key_company(provider="nonexistent", api_key="sk-test")
        assert result["code"] == "invalid_provider"

    async def test_no_env_key_returns_error(self):
        """Line 35: provider with no env_key returns config error."""
        from onemancompany.core.auth_apply.api_key import apply_api_key_company
        mock_provider = MagicMock()
        mock_provider.env_key = ""
        with patch("onemancompany.core.config.get_provider", return_value=mock_provider):
            result = await apply_api_key_company(provider="broken", api_key="sk-test")
        assert result["code"] == "config_error"


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

    async def test_dispatch_employee_scope(self):
        """Lines 57-65: employee scope dispatches to apply_api_key_employee."""
        from onemancompany.core.auth_apply import apply_auth_choice

        with patch("onemancompany.core.auth_apply.api_key.apply_api_key_employee", new_callable=AsyncMock) as mock_apply:
            mock_apply.return_value = {"status": "applied", "scope": "employee"}
            result = await apply_auth_choice(
                choice_value="deepseek-api-key",
                scope="employee",
                api_key="sk-test",
                employee_id="00010",
            )

        assert result["scope"] == "employee"
        mock_apply.assert_called_once()

    async def test_dispatch_employee_scope_missing_id(self):
        """Lines 58-59: employee scope without employee_id returns error."""
        from onemancompany.core.auth_apply import apply_auth_choice

        result = await apply_auth_choice(
            choice_value="deepseek-api-key",
            scope="employee",
            api_key="sk-test",
        )

        assert result["code"] == "missing_param"

    async def test_dispatch_invalid_scope(self):
        """Lines 66-67: invalid scope returns error."""
        from onemancompany.core.auth_apply import apply_auth_choice

        result = await apply_auth_choice(
            choice_value="deepseek-api-key",
            scope="invalid_scope",
            api_key="sk-test",
        )

        assert result["code"] == "invalid_scope"

    async def test_dispatch_non_api_key_method(self):
        """Lines 69-74: non-api_key auth method returns not_available."""
        from onemancompany.core.auth_apply import apply_auth_choice
        from onemancompany.core.auth_choices import AuthChoiceOption

        # Mock resolve_auth_choice to return an available oauth option
        fake_option = AuthChoiceOption(
            value="fake-oauth", label="Fake OAuth", hint="",
            provider="fake", auth_method="oauth", available=True,
        )
        with patch("onemancompany.core.auth_apply.resolve_auth_choice", return_value=fake_option):
            result = await apply_auth_choice(
                choice_value="fake-oauth",
                scope="company",
                api_key="",
            )

        assert result["code"] == "not_available"
        assert "oauth" in result["error"]
