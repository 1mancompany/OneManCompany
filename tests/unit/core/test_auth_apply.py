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
