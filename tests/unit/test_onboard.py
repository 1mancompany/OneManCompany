"""Tests for onboard.py — TDD coverage for onboarding wizard logic."""
from __future__ import annotations

import json
import yaml
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestINQStyleType:
    """INQ_STYLE must be InquirerPyStyle, not a plain dict."""

    def test_inq_style_is_inquirerpy_style(self):
        from onemancompany.onboard import INQ_STYLE
        from InquirerPy.utils import InquirerPyStyle
        assert isinstance(INQ_STYLE, InquirerPyStyle)

    def test_inq_style_has_dict_attr(self):
        """InquirerPy internally calls style.dict — must not raise."""
        from onemancompany.onboard import INQ_STYLE
        assert hasattr(INQ_STYLE, "dict")

    def test_inq_style_can_merge_and_rewrap(self):
        """INQ_STYLE.dict can be unpacked and re-wrapped for overrides."""
        from onemancompany.onboard import INQ_STYLE
        from InquirerPy.utils import InquirerPyStyle
        merged = InquirerPyStyle({**INQ_STYLE.dict, "fuzzy_match": "#ff44cc"})
        assert isinstance(merged, InquirerPyStyle)


class TestProviderChoicesCompat:
    """Provider selector must use valid AuthChoiceGroup attributes."""

    def test_auth_choice_group_has_hint_not_auth_methods(self):
        from onemancompany.core.auth_choices import AUTH_CHOICE_GROUPS
        for group in AUTH_CHOICE_GROUPS:
            assert hasattr(group, "hint"), f"{group.group_id} missing hint"
            assert hasattr(group, "label"), f"{group.group_id} missing label"
            assert hasattr(group, "group_id"), f"{group.group_id} missing group_id"
            # auth_methods does NOT exist — this was the bug
            assert not hasattr(group, "auth_methods"), (
                f"{group.group_id} has auth_methods — should use hint instead"
            )

    def test_provider_choice_label_format(self):
        """The format string used in _step_llm must not crash."""
        from onemancompany.core.auth_choices import AUTH_CHOICE_GROUPS
        for g in AUTH_CHOICE_GROUPS:
            # This is what _step_llm does:
            label = f"{g.label}  ({g.hint})"
            assert isinstance(label, str)
            assert len(label) > 0


class TestApplyFounderFamilies:
    """_apply_founder_families writes hosting to profile.yaml correctly."""

    def test_writes_hosting_to_profile(self, tmp_path):
        from onemancompany.onboard import _apply_founder_families
        from rich.console import Console

        # Create a fake employee dir with profile.yaml
        emp_dir = tmp_path / "00004"
        emp_dir.mkdir()
        profile = emp_dir / "profile.yaml"
        profile.write_text(yaml.dump({"name": "Pat EA", "hosting": "company"}))

        console = Console(quiet=True)
        with patch("onemancompany.onboard.EMPLOYEES_DIR", tmp_path), \
             patch("subprocess.run", return_value=MagicMock(returncode=0)):
            _apply_founder_families(console, {"00004": "openclaw"})

        data = yaml.safe_load(profile.read_text())
        assert data["hosting"] == "openclaw"

    def test_skips_unchanged_hosting(self, tmp_path):
        from onemancompany.onboard import _apply_founder_families
        from rich.console import Console

        emp_dir = tmp_path / "00002"
        emp_dir.mkdir()
        profile = emp_dir / "profile.yaml"
        profile.write_text(yaml.dump({"name": "Sam HR", "hosting": "company"}))
        original_mtime = profile.stat().st_mtime

        console = Console(quiet=True)
        with patch("onemancompany.onboard.EMPLOYEES_DIR", tmp_path):
            _apply_founder_families(console, {"00002": "company"})

        # File should not have been rewritten
        assert profile.stat().st_mtime == original_mtime

    def test_installs_openclaw_on_need(self, tmp_path):
        from onemancompany.onboard import _apply_founder_families
        from rich.console import Console

        emp_dir = tmp_path / "00004"
        emp_dir.mkdir()
        (emp_dir / "profile.yaml").write_text(yaml.dump({"hosting": "company"}))

        console = Console(quiet=True)
        with patch("onemancompany.onboard.EMPLOYEES_DIR", tmp_path), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            _apply_founder_families(console, {"00004": "openclaw"})

        # npm install should have been called
        mock_run.assert_called_once()
        assert "openclaw" in str(mock_run.call_args)

    def test_no_npm_install_if_no_openclaw(self, tmp_path):
        from onemancompany.onboard import _apply_founder_families
        from rich.console import Console

        emp_dir = tmp_path / "00002"
        emp_dir.mkdir()
        (emp_dir / "profile.yaml").write_text(yaml.dump({"hosting": "company"}))

        console = Console(quiet=True)
        with patch("onemancompany.onboard.EMPLOYEES_DIR", tmp_path), \
             patch("subprocess.run") as mock_run:
            _apply_founder_families(console, {"00002": "self"})

        mock_run.assert_not_called()


class TestGenerateMcpConfigs:
    """MCP config generation should honor configured server host/port."""

    def test_uses_configured_host_and_port(self, tmp_path):
        from onemancompany.onboard import _generate_mcp_configs

        emp_dir = tmp_path / "00002"
        emp_dir.mkdir()

        with patch("onemancompany.onboard.EMPLOYEES_DIR", tmp_path), \
             patch("onemancompany.onboard.TOOLS_DIR", tmp_path), \
             patch("onemancompany.core.config.EXEC_IDS", {"00002"}):
            _generate_mcp_configs("skills-key", "127.0.0.1", 8001)

        cfg = json.loads((emp_dir / "mcp_config.json").read_text())
        env = cfg["mcpServers"]["onemancompany"]["env"]
        assert env["OMC_SERVER_URL"] == "http://127.0.0.1:8001"
        assert cfg["mcpServers"]["fastskills"]["env"]["SKILLSMP_API_KEY"] == "skills-key"

    def test_maps_wildcard_host_to_localhost(self, tmp_path):
        from onemancompany.onboard import _generate_mcp_configs

        emp_dir = tmp_path / "00002"
        emp_dir.mkdir()

        with patch("onemancompany.onboard.EMPLOYEES_DIR", tmp_path), \
             patch("onemancompany.onboard.TOOLS_DIR", tmp_path), \
             patch("onemancompany.core.config.EXEC_IDS", {"00002"}):
            _generate_mcp_configs("", "0.0.0.0", 8001)

        cfg = json.loads((emp_dir / "mcp_config.json").read_text())
        env = cfg["mcpServers"]["onemancompany"]["env"]
        assert env["OMC_SERVER_URL"] == "http://localhost:8001"


class TestNoStaleRichUI:
    """Onboard must not show Rich Table/instructions alongside InquirerPy prompts."""

    def test_step_llm_no_rich_table_for_providers(self):
        """_step_llm should not render a Rich Table of providers — InquirerPy select replaces it."""
        import inspect
        from onemancompany.onboard import _step_llm
        source = inspect.getsource(_step_llm)
        assert "console.print(table)" not in source, "Rich Table still rendered before InquirerPy select"
        assert "Select your LLM provider" not in source, "Old Rich header text still present"

    def test_step_llm_no_old_model_picker_instructions(self):
        """Model picker instructions (n/p/c/number) are replaced by InquirerPy fuzzy search."""
        import inspect
        from onemancompany.onboard import _step_llm
        source = inspect.getsource(_step_llm)
        assert "Type a number" not in source
        assert "next/previous page" not in source
        assert "custom model ID" not in source


class TestStepLlmBaseUrlPrompt:
    """Provider-specific Base URL prompt behavior."""

    @pytest.mark.parametrize("selected_provider", [
        "openai",
        "anthropic",
        "kimi",
        "deepseek",
        "qwen",
        "zhipu",
        "groq",
        "together",
        "openrouter",
        "google",
        "minimax",
    ])
    def test_known_providers_do_not_prompt_for_base_url(self, selected_provider):
        from onemancompany.onboard import _step_llm

        mock_console = MagicMock()
        mock_select = MagicMock()
        mock_select.execute.return_value = selected_provider
        mock_secret = MagicMock()
        mock_secret.execute.return_value = "sk-test"
        mock_text = MagicMock()
        mock_text.execute.return_value = "test-model"

        with patch("InquirerPy.inquirer.select", return_value=mock_select), \
             patch("InquirerPy.inquirer.secret", return_value=mock_secret), \
             patch("InquirerPy.inquirer.text", return_value=mock_text) as mock_text_fn, \
             patch("onemancompany.onboard._fetch_provider_models", return_value=[]):
            provider, api_key, model, base_url, custom_chat_class = _step_llm(mock_console)

        assert provider == selected_provider
        assert api_key == "sk-test"
        assert model == "test-model"
        assert base_url == ""
        assert custom_chat_class == ""
        assert [call.kwargs["message"] for call in mock_text_fn.call_args_list] == ["Model ID:"]

    def test_azure_provider_builds_endpoint_and_uses_deployment_name(self):
        from onemancompany.onboard import _step_llm

        mock_console = MagicMock()
        provider_select = MagicMock()
        provider_select.execute.return_value = "azure"
        mock_secret = MagicMock()
        mock_secret.execute.return_value = "az-key"
        resource_text = MagicMock()
        resource_text.execute.return_value = "myres"
        deployment_text = MagicMock()
        deployment_text.execute.return_value = "gpt-5.5"

        with patch("InquirerPy.inquirer.select", return_value=provider_select), \
             patch("InquirerPy.inquirer.secret", return_value=mock_secret), \
             patch("InquirerPy.inquirer.text", side_effect=[resource_text, deployment_text]) as mock_text_fn:
            provider, api_key, model, base_url, custom_chat_class = _step_llm(mock_console)

        assert provider == "azure"
        assert api_key == "az-key"
        # Azure uses the *deployment name* as the model id — no /models fetch.
        assert model == "gpt-5.5"
        assert base_url == "https://myres.services.ai.azure.com/openai/v1/"
        # chat_class stays the registry default (openai); no CUSTOM_CHAT_CLASS needed.
        assert custom_chat_class == ""
        prompts = [call.kwargs["message"] for call in mock_text_fn.call_args_list]
        assert prompts == ["Azure resource name or endpoint URL:", "Deployment name:"]

    def test_custom_provider_prompts_for_base_url(self):
        from onemancompany.onboard import _step_llm

        mock_console = MagicMock()
        provider_select = MagicMock()
        provider_select.execute.return_value = "custom"
        compatibility_select = MagicMock()
        compatibility_select.execute.return_value = "openai"
        mock_secret = MagicMock()
        mock_secret.execute.return_value = "sk-custom"
        base_url_text = MagicMock()
        base_url_text.execute.return_value = "https://llm.example.com/v1"
        model_text = MagicMock()
        model_text.execute.return_value = "custom-model"

        with patch("InquirerPy.inquirer.select", side_effect=[provider_select, compatibility_select]), \
             patch("InquirerPy.inquirer.secret", return_value=mock_secret), \
             patch("InquirerPy.inquirer.text", side_effect=[base_url_text, model_text]) as mock_text_fn, \
             patch("onemancompany.onboard._fetch_provider_models", return_value=[]):
            provider, api_key, model, base_url, custom_chat_class = _step_llm(mock_console)

        assert provider == "custom"
        assert api_key == "sk-custom"
        assert model == "custom-model"
        assert base_url == "https://llm.example.com/v1"
        assert custom_chat_class == "openai"
        assert [call.kwargs["message"] for call in mock_text_fn.call_args_list] == ["Base URL:", "Model ID:"]


class TestModelPricingDisplay:
    """Model list pricing display must distinguish free from unknown."""

    def test_format_price_missing_is_na_zero_is_free(self):
        from onemancompany.onboard import PRICE_FREE, PRICE_NA, _format_price

        assert _format_price(None) == PRICE_NA
        assert _format_price("") == PRICE_NA
        assert _format_price("0") == PRICE_FREE

    def test_select_model_uses_existing_price_labels_without_reformatting(self):
        from onemancompany.onboard import (
            MODEL_KEY_COMPLETION_PRICE,
            MODEL_KEY_CONTEXT,
            MODEL_KEY_ID,
            MODEL_KEY_NAME,
            MODEL_KEY_PROMPT_PRICE,
            PRICE_FREE,
            PRICE_NA,
            _select_model_interactive,
        )

        mock_console = MagicMock()
        fuzzy_prompt = MagicMock()
        fuzzy_prompt.execute.return_value = "claude-haiku-4-5-20251001"
        models = [
            {
                MODEL_KEY_ID: "claude-haiku-4-5-20251001",
                MODEL_KEY_NAME: "Claude Haiku 4.5",
                MODEL_KEY_PROMPT_PRICE: "",
                MODEL_KEY_COMPLETION_PRICE: "",
                MODEL_KEY_CONTEXT: 0,
            },
            {
                MODEL_KEY_ID: "openrouter/free-model",
                MODEL_KEY_NAME: "Free Model",
                MODEL_KEY_PROMPT_PRICE: PRICE_FREE,
                MODEL_KEY_COMPLETION_PRICE: PRICE_FREE,
                MODEL_KEY_CONTEXT: 0,
            },
            {
                MODEL_KEY_ID: "priced-model",
                MODEL_KEY_NAME: "Priced Model",
                MODEL_KEY_PROMPT_PRICE: "$0.25/M",
                MODEL_KEY_COMPLETION_PRICE: "$1.25/M",
                MODEL_KEY_CONTEXT: 0,
            },
        ]

        with patch("InquirerPy.inquirer.fuzzy", return_value=fuzzy_prompt) as mock_fuzzy:
            selected = _select_model_interactive(mock_console, models)

        choices = mock_fuzzy.call_args.kwargs["choices"]
        assert selected == "claude-haiku-4-5-20251001"
        assert choices[0]["name"] == f"claude-haiku-4-5-20251001  [{PRICE_NA} / {PRICE_NA}]"
        assert choices[1]["name"] == "openrouter/free-model  [free / free]"
        assert choices[2]["name"] == "priced-model  [$0.25/M / $1.25/M]"

    def test_fetch_provider_models_missing_pricing_is_na(self):
        from onemancompany.onboard import (
            MODEL_KEY_COMPLETION_PRICE,
            MODEL_KEY_PROMPT_PRICE,
            PRICE_NA,
            _fetch_provider_models,
        )

        mock_console = MagicMock()
        mock_console.status.return_value.__enter__.return_value = None
        mock_console.status.return_value.__exit__.return_value = None
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "data": [
                {"id": "claude-haiku-4-5-20251001", "display_name": "Claude Haiku 4.5"}
            ]
        }

        with patch("httpx.get", return_value=mock_resp):
            models = _fetch_provider_models(mock_console, "anthropic", "sk-ant-test")

        assert models[0][MODEL_KEY_PROMPT_PRICE] == PRICE_NA
        assert models[0][MODEL_KEY_COMPLETION_PRICE] == PRICE_NA


class TestOpenclawLaunchShErrorHandling:
    """launch.sh must surface errors instead of silently returning 'No output returned'."""

    def test_launch_sh_does_not_blindly_discard_stderr(self):
        """The openclaw agent call must NOT use bare '2>/dev/null' without capturing stderr."""
        launch_sh = Path(__file__).parent.parent.parent / "src/onemancompany/talent_market/talents/openclaw/launch.sh"
        content = launch_sh.read_text()
        # Old pattern: 2>/dev/null throws away all error info
        assert '2>/dev/null || echo ""' not in content, (
            "launch.sh blindly discards stderr — errors like '403 Key limit exceeded' "
            "are silently swallowed, showing only 'No output returned'"
        )

    def test_launch_sh_captures_stderr_to_file(self):
        """launch.sh should capture stderr to a temp file for JSON extraction."""
        launch_sh = Path(__file__).parent.parent.parent / "src/onemancompany/talent_market/talents/openclaw/launch.sh"
        content = launch_sh.read_text()
        assert "STDERR_FILE" in content, "launch.sh should capture stderr to a temp file"
        assert "raw_decode" in content, "launch.sh should use raw_decode for robust JSON extraction"


class TestHostingLabels:
    """HOSTING_LABELS constant covers all valid hosting values."""

    def test_all_hosting_modes_have_labels(self):
        from onemancompany.onboard import HOSTING_LABELS
        assert "company" in HOSTING_LABELS
        assert "self" in HOSTING_LABELS
        assert "openclaw" in HOSTING_LABELS

    def test_labels_are_human_readable(self):
        from onemancompany.onboard import HOSTING_LABELS
        assert HOSTING_LABELS["company"] == "LangChain"
        assert HOSTING_LABELS["self"] == "Claude Code"
        assert HOSTING_LABELS["openclaw"] == "OpenClaw"


class TestStepExecuteSignature:
    """_step_execute accepts founder_families parameter."""

    def test_accepts_founder_families_none(self):
        """Calling with founder_families=None should not crash."""
        import inspect
        from onemancompany.onboard import _step_execute
        sig = inspect.signature(_step_execute)
        assert "founder_families" in sig.parameters
        assert sig.parameters["founder_families"].default is None

    def test_passes_host_and_port_to_mcp_config_generation(self, tmp_path):
        from rich.console import Console
        from onemancompany.onboard import _step_execute

        source_root = tmp_path / "src"
        source_root.mkdir()

        with patch("onemancompany.onboard.DATA_ROOT", tmp_path / "data"), \
             patch("onemancompany.onboard.SOURCE_ROOT", source_root), \
             patch("onemancompany.onboard._assign_default_avatars"), \
             patch("onemancompany.onboard._generate_mcp_configs") as mock_generate, \
             patch("onemancompany.core.config.sync_founding_defaults", return_value=False):
            _step_execute(
                Console(quiet=True),
                provider="anthropic",
                api_key="sk-test",
                model="claude-test",
                host="127.0.0.1",
                port=8001,
                extras={},
            )

        mock_generate.assert_called_once_with("", "127.0.0.1", 8001)


class TestCreateExecutorForHosting:
    """Executor factory returns correct types for each hosting value."""

    def test_company_returns_langchain(self):
        from onemancompany.core.vessel import _create_executor_for_hosting, LangChainExecutor
        executor = _create_executor_for_hosting("company", "00002", MagicMock, Path("/tmp"))
        assert isinstance(executor, LangChainExecutor)

    def test_self_returns_claude_session(self):
        from onemancompany.core.vessel import _create_executor_for_hosting, ClaudeSessionExecutor
        executor = _create_executor_for_hosting("self", "00002", MagicMock, Path("/tmp"))
        assert isinstance(executor, ClaudeSessionExecutor)

    def test_openclaw_returns_subprocess(self):
        from onemancompany.core.vessel import _create_executor_for_hosting
        from onemancompany.core.subprocess_executor import SubprocessExecutor
        executor = _create_executor_for_hosting("openclaw", "00002", MagicMock, Path("/tmp"))
        assert isinstance(executor, SubprocessExecutor)


class TestAiSearchPrompt:
    """AI Search Talent prompt appears only when TM API key is provided."""

    def test_ai_search_prompt_shown_when_api_key_provided(self):
        """When user enters a TM API key, the AI search confirm is shown."""
        from onemancompany.onboard import _step_optional, ENV_KEY_TALENT_MARKET

        mock_console = MagicMock()

        # Mock inquirer: Anthropic=skip, SkillMarket=skip, TM=key, AI search=True
        mock_secret = MagicMock()
        mock_secret.execute = MagicMock(side_effect=["", "", "tm-key-123"])
        mock_confirm = MagicMock()
        mock_confirm.execute = MagicMock(return_value=True)

        with patch("InquirerPy.inquirer.secret", return_value=mock_secret), \
             patch("InquirerPy.inquirer.confirm", return_value=mock_confirm) as mock_confirm_fn:
            extras = _step_optional(mock_console)

        assert extras[ENV_KEY_TALENT_MARKET] == "tm-key-123"
        assert extras.get("USE_AI_SEARCH") == "true"
        mock_confirm_fn.assert_called_once()

    def test_ai_search_prompt_not_shown_when_no_api_key(self):
        """When user skips TM API key, no AI search prompt is shown."""
        from onemancompany.onboard import _step_optional, ENV_KEY_TALENT_MARKET

        mock_console = MagicMock()

        # Mock inquirer: all keys skipped
        mock_secret = MagicMock()
        mock_secret.execute = MagicMock(return_value="")

        with patch("InquirerPy.inquirer.secret", return_value=mock_secret), \
             patch("InquirerPy.inquirer.confirm") as mock_confirm_fn:
            extras = _step_optional(mock_console)

        assert ENV_KEY_TALENT_MARKET not in extras
        assert "USE_AI_SEARCH" not in extras
        mock_confirm_fn.assert_not_called()

    def test_ai_search_false_when_user_declines(self):
        """When user declines AI search, extras has USE_AI_SEARCH=false."""
        from onemancompany.onboard import _step_optional, ENV_KEY_TALENT_MARKET

        mock_console = MagicMock()

        mock_secret = MagicMock()
        mock_secret.execute = MagicMock(side_effect=["", "", "tm-key-456"])
        mock_confirm = MagicMock()
        mock_confirm.execute = MagicMock(return_value=False)

        with patch("InquirerPy.inquirer.secret", return_value=mock_secret), \
             patch("InquirerPy.inquirer.confirm", return_value=mock_confirm):
            extras = _step_optional(mock_console)

        assert extras[ENV_KEY_TALENT_MARKET] == "tm-key-456"
        assert extras.get("USE_AI_SEARCH") == "false"


class TestSandboxPromptRemoved:
    """Interactive onboarding should not offer sandbox installation."""

    def test_wizard_does_not_call_sandbox_step(self):
        import inspect
        from onemancompany.onboard import run_wizard

        source = inspect.getsource(run_wizard)

        assert "_step_sandbox(" not in source

    def test_total_steps_excludes_sandbox(self):
        from onemancompany.onboard import TOTAL_STEPS

        assert TOTAL_STEPS == 5


class TestAzureBaseUrl:
    """_azure_base_url builds the OpenAI-compatible v1 endpoint from a resource."""

    def test_bare_resource_name(self):
        from onemancompany.onboard import _azure_base_url
        assert _azure_base_url("myres") == "https://myres.services.ai.azure.com/openai/v1/"

    def test_full_services_endpoint_normalized(self):
        from onemancompany.onboard import _azure_base_url
        url = _azure_base_url("https://myres.services.ai.azure.com")
        assert url == "https://myres.services.ai.azure.com/openai/v1/"

    def test_openai_azure_com_endpoint(self):
        from onemancompany.onboard import _azure_base_url
        url = _azure_base_url("https://myres.openai.azure.com/")
        assert url == "https://myres.openai.azure.com/openai/v1/"

    def test_already_has_openai_v1_suffix_is_idempotent(self):
        from onemancompany.onboard import _azure_base_url
        url = _azure_base_url("https://myres.services.ai.azure.com/openai/v1/")
        assert url == "https://myres.services.ai.azure.com/openai/v1/"

    def test_empty_resource_returns_empty(self):
        from onemancompany.onboard import _azure_base_url
        assert _azure_base_url("") == ""
        assert _azure_base_url("   ") == ""


class TestRunAutoPreservesEndpointConfig:
    """run_auto must not drop DEFAULT_API_BASE_URL / CUSTOM_CHAT_CLASS (issue #402)."""

    def _write_env(self, tmp_path, body):
        (tmp_path / ".onemancompany.env").write_text(body, encoding="utf-8")

    def test_run_auto_passes_base_url_and_chat_class(self, tmp_path, monkeypatch):
        import onemancompany.onboard as onboard

        env_body = (
            "DEFAULT_API_PROVIDER=azure\n"
            "AZURE_API_KEY=az-secret\n"
            "DEFAULT_LLM_MODEL=gpt-5.5\n"
            "DEFAULT_API_BASE_URL=https://myres.services.ai.azure.com/openai/v1/\n"
            "CUSTOM_CHAT_CLASS=openai\n"
        )
        self._write_env(tmp_path, env_body)
        monkeypatch.setattr(onboard, "DOT_ENV_FILENAME", ".onemancompany.env")
        monkeypatch.chdir(tmp_path)

        captured = {}

        def fake_execute(console, provider, api_key, model, host, port, extras, **kwargs):
            captured.update(kwargs)
            captured["provider"] = provider
            captured["api_key"] = api_key

        with patch.object(onboard, "_step_execute", side_effect=fake_execute), \
             patch.object(onboard, "_step_done"):
            onboard.run_auto(skip_confirm=True)

        assert captured["provider"] == "azure"
        assert captured["api_key"] == "az-secret"
        assert captured["base_url"] == "https://myres.services.ai.azure.com/openai/v1/"
        assert captured["custom_chat_class"] == "openai"
