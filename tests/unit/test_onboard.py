"""Tests for onboard.py — TDD coverage for onboarding wizard logic."""
from __future__ import annotations

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
        with patch("onemancompany.onboard.EMPLOYEES_DIR", tmp_path):
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
        """launch.sh should capture stderr to a temp file for error reporting."""
        launch_sh = Path(__file__).parent.parent.parent / "src/onemancompany/talent_market/talents/openclaw/launch.sh"
        content = launch_sh.read_text()
        assert "STDERR_FILE" in content, "launch.sh should capture stderr to a temp file"
        assert "STDERR_CONTENT" in content, "launch.sh should read stderr content for error reporting"


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
