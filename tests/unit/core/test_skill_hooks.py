"""Unit tests for core/skill_hooks.py — CC-style skill hooks system."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from onemancompany.core.skill_hooks import (
    HookConfig, HookEvent, HookResult,
    _build_env, _expand_command, _exec_callback_hook, _exec_command_hook,
    _matches, _registry, _resolve_event,
    clear_hooks, collect_context, get_hooks, get_updated_input,
    load_hooks_from_skills,
    register_callback_hook, register_skill_hooks,
    run_hooks, should_block,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    """Clear hook registry before each test."""
    _registry.clear()
    yield
    _registry.clear()


# ---------------------------------------------------------------------------
# Matcher
# ---------------------------------------------------------------------------

class TestMatches:
    def test_empty_matches_everything(self):
        assert _matches("", "anything") is True

    def test_exact_match(self):
        assert _matches("bash", "bash") is True
        assert _matches("bash", "write") is False

    def test_pipe_separated(self):
        assert _matches("bash|write|edit", "write") is True
        assert _matches("bash|write|edit", "read") is False

    def test_regex(self):
        assert _matches("^web_.*", "web_search") is True
        assert _matches("^web_.*", "bash") is False

    def test_regex_fullmatch(self):
        assert _matches("bash.*", "bash_exec") is True
        assert _matches("bash.*", "prebash") is False

    def test_invalid_regex_falls_back_to_exact(self):
        # Invalid regex pattern — falls back to exact string match
        assert _matches("[invalid", "[invalid") is True
        assert _matches("[invalid", "other") is False


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

class TestRegistration:
    def test_register_from_skill_metadata(self):
        hooks_meta = {
            "task_start": [
                {"command": "echo start", "mode": "auto"},
            ],
            "post_tool": [
                {"command": "echo post", "matcher": "bash", "mode": "auto"},
            ],
        }
        count = register_skill_hooks("00004", "test-skill", hooks_meta)
        assert count == 2
        assert len(get_hooks("00004", HookEvent.TASK_START)) == 1
        assert len(get_hooks("00004", HookEvent.POST_TOOL)) == 1

    def test_register_cc_nested_format(self):
        """CC settings.json format: {matcher: ..., hooks: [{type: command, command: ...}]}"""
        hooks_meta = {
            "PreToolUse": [
                {
                    "matcher": "Bash|Write",
                    "hooks": [
                        {"type": "command", "command": "echo pre-tool"},
                    ],
                },
            ],
            "Stop": [
                {
                    "matcher": "",
                    "hooks": [
                        {"type": "command", "command": "echo session-end"},
                    ],
                },
            ],
        }
        count = register_skill_hooks("00004", "cc-skill", hooks_meta)
        assert count == 2
        # PreToolUse → PRE_TOOL
        hooks = get_hooks("00004", HookEvent.PRE_TOOL)
        assert len(hooks) == 1
        assert hooks[0].matcher == "Bash|Write"
        assert hooks[0].command == "echo pre-tool"
        # Stop → TASK_COMPLETE
        assert len(get_hooks("00004", HookEvent.TASK_COMPLETE)) == 1

    def test_register_cc_frontmatter_format(self, tmp_path):
        """CC frontmatter format: before_start/after_complete/on_error with trigger."""
        # Create hook script files so trigger resolution works
        hooks_dir = tmp_path / "00004" / "skills" / "sia" / "hooks"
        hooks_dir.mkdir(parents=True)
        (hooks_dir / "session-logger.sh").write_text("#!/bin/bash\necho ok")

        hooks_meta = {
            "before_start": [
                {"trigger": "session-logger", "mode": "auto"},
            ],
            "after_complete": [
                {"trigger": "create-pr", "mode": "ask_first"},
                {"trigger": "session-logger", "mode": "auto"},
            ],
            "on_error": [
                {"trigger": "session-logger", "mode": "auto"},
            ],
        }
        with patch("onemancompany.core.skill_hooks.EMPLOYEES_DIR", tmp_path):
            count = register_skill_hooks("00004", "sia", hooks_meta)
        # ask_first skipped, 3 triggers resolved to session-logger.sh
        assert count == 3
        assert len(get_hooks("00004", HookEvent.TASK_START)) == 1
        assert len(get_hooks("00004", HookEvent.TASK_COMPLETE)) == 1
        assert len(get_hooks("00004", HookEvent.TASK_ERROR)) == 1
        # Verify the command points to the script
        hook = get_hooks("00004", HookEvent.TASK_START)[0]
        assert "session-logger.sh" in hook.command

    def test_skip_ask_first_mode(self):
        hooks_meta = {
            "task_complete": [
                {"command": "echo done", "mode": "ask_first"},
            ],
        }
        count = register_skill_hooks("00004", "test-skill", hooks_meta)
        assert count == 0

    def test_skip_unknown_event(self):
        hooks_meta = {"unknown_event": [{"command": "echo x"}]}
        count = register_skill_hooks("00004", "test-skill", hooks_meta)
        assert count == 0

    def test_clear_hooks(self):
        register_skill_hooks("00004", "s1", {"task_start": [{"command": "echo x"}]})
        assert len(get_hooks("00004", HookEvent.TASK_START)) == 1
        clear_hooks("00004")
        assert len(get_hooks("00004", HookEvent.TASK_START)) == 0

    def test_register_callback(self):
        async def my_hook(inp):
            return {}

        register_callback_hook("00004", HookEvent.TASK_START, my_hook)
        hooks = get_hooks("00004", HookEvent.TASK_START)
        assert len(hooks) == 1
        assert hooks[0].callback is my_hook

    def test_hook_list_not_list_wrapped(self):
        """Non-list hook_list value gets wrapped in a list."""
        hooks_meta = {
            "task_start": {"command": "echo single", "mode": "auto"},
        }
        count = register_skill_hooks("00004", "wrap-skill", hooks_meta)
        assert count == 1

    def test_non_dict_hook_entry_skipped(self):
        """Non-dict entries in hook list are skipped."""
        hooks_meta = {
            "task_start": ["not-a-dict", {"command": "echo yes"}],
        }
        count = register_skill_hooks("00004", "mix-skill", hooks_meta)
        assert count == 1

    def test_cc_nested_non_dict_inner_skipped(self):
        """Non-dict inner hooks in CC nested format are skipped."""
        hooks_meta = {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": ["not-a-dict", {"command": "echo ok"}],
                },
            ],
        }
        count = register_skill_hooks("00004", "nested-skip", hooks_meta)
        assert count == 1

    def test_empty_command_skipped(self):
        """Hook with no command (e.g. unresolved trigger) is skipped."""
        hooks_meta = {
            "task_start": [
                {"trigger": "nonexistent-trigger"},
            ],
        }
        with patch("onemancompany.core.skill_hooks.EMPLOYEES_DIR", __import__("pathlib").Path("/nonexistent")):
            count = register_skill_hooks("00004", "no-cmd", hooks_meta)
        assert count == 0


# ---------------------------------------------------------------------------
# should_block / get_updated_input / collect_context
# ---------------------------------------------------------------------------

class TestResultHelpers:
    def test_should_block_on_exit_2(self):
        results = [HookResult(exit_code=2, error="blocked")]
        blocked, reason = should_block(results)
        assert blocked is True
        assert "blocked" in reason

    def test_should_block_on_decision(self):
        results = [HookResult(decision="block", reason="no")]
        blocked, reason = should_block(results)
        assert blocked is True

    def test_no_block_on_success(self):
        results = [HookResult(exit_code=0)]
        blocked, _ = should_block(results)
        assert blocked is False

    def test_updated_input(self):
        results = [HookResult(updated_input={"key": "new"})]
        updated = get_updated_input(results, {"key": "old", "other": 1})
        assert updated == {"key": "new", "other": 1}

    def test_collect_context(self):
        results = [
            HookResult(additional_context="from hook1"),
            HookResult(additional_context=""),
            HookResult(additional_context="from hook3"),
        ]
        ctx = collect_context(results)
        assert "from hook1" in ctx
        assert "from hook3" in ctx


# ---------------------------------------------------------------------------
# Callback hook execution
# ---------------------------------------------------------------------------

class TestCallbackExecution:
    @pytest.mark.asyncio
    async def test_callback_hook_runs(self):
        captured = {}

        async def my_hook(hook_input):
            captured.update(hook_input)
            return {"decision": "allow", "additionalContext": "hello"}

        register_callback_hook("00004", HookEvent.TASK_START, my_hook)
        results = await run_hooks("00004", HookEvent.TASK_START, task_id="t1", task_description="do stuff")

        assert len(results) == 1
        assert results[0].additional_context == "hello"
        assert captured["employee_id"] == "00004"
        assert captured["task_id"] == "t1"

    @pytest.mark.asyncio
    async def test_callback_timeout(self):
        async def slow_hook(hook_input):
            await asyncio.sleep(10)
            return {}

        register_callback_hook("00004", HookEvent.TASK_START, slow_hook)
        # Override timeout to 0.1s
        get_hooks("00004", HookEvent.TASK_START)[0].timeout = 0.1

        results = await run_hooks("00004", HookEvent.TASK_START)
        assert len(results) == 1
        assert "timed out" in results[0].error.lower()

    @pytest.mark.asyncio
    async def test_no_hooks_returns_empty(self):
        results = await run_hooks("00004", HookEvent.TASK_START)
        assert results == []


# ---------------------------------------------------------------------------
# Tool event filtering
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# _resolve_event
# ---------------------------------------------------------------------------

class TestResolveEvent:
    def test_known_cc_event(self):
        assert _resolve_event("PreToolUse") == HookEvent.PRE_TOOL

    def test_our_event(self):
        assert _resolve_event("pre_tool") == HookEvent.PRE_TOOL

    def test_unknown_event(self):
        assert _resolve_event("totally_unknown") is None

    def test_valid_enum_value_fallback(self):
        # "task_start" is both in _CC_EVENT_MAP and a valid HookEvent value
        assert _resolve_event("task_start") == HookEvent.TASK_START


# ---------------------------------------------------------------------------
# _expand_command
# ---------------------------------------------------------------------------

class TestExpandCommand:
    def test_expands_braced_vars(self):
        env = {"FOO": "bar", "BAZ": "qux"}
        result = _expand_command("echo ${FOO} ${BAZ}", env)
        assert result == "echo bar qux"

    def test_no_expansion_needed(self):
        result = _expand_command("echo hello", {"FOO": "bar"})
        assert result == "echo hello"


# ---------------------------------------------------------------------------
# _exec_command_hook
# ---------------------------------------------------------------------------

class TestExecCommandHook:
    @pytest.mark.asyncio
    async def test_successful_json_output(self):
        config = HookConfig(
            event=HookEvent.PRE_TOOL,
            command='echo \'{"decision":"allow","reason":"ok","additionalContext":"ctx"}\'',
            timeout=5,
            skill_name="test",
        )
        env = dict(__import__("os").environ)
        result = await _exec_command_hook(config, {}, env, "00004")
        assert result.exit_code == 0
        assert result.decision == "allow"
        assert result.reason == "ok"
        assert result.additional_context == "ctx"

    @pytest.mark.asyncio
    async def test_non_json_stdout(self):
        config = HookConfig(
            event=HookEvent.PRE_TOOL,
            command="echo 'plain text'",
            timeout=5,
            skill_name="test",
        )
        env = dict(__import__("os").environ)
        result = await _exec_command_hook(config, {}, env, "00004")
        assert result.additional_context == "plain text"

    @pytest.mark.asyncio
    async def test_empty_stdout(self):
        config = HookConfig(
            event=HookEvent.PRE_TOOL,
            command="true",
            timeout=5,
            skill_name="test",
        )
        env = dict(__import__("os").environ)
        result = await _exec_command_hook(config, {}, env, "00004")
        assert result.exit_code == 0

    @pytest.mark.asyncio
    async def test_stderr_with_exit_block(self):
        config = HookConfig(
            event=HookEvent.PRE_TOOL,
            command="echo 'blocked' >&2; exit 2",
            timeout=5,
            skill_name="test",
        )
        env = dict(__import__("os").environ)
        result = await _exec_command_hook(config, {}, env, "00004")
        assert result.exit_code == 2
        assert result.error == "blocked"

    @pytest.mark.asyncio
    async def test_stderr_with_nonzero_exit(self):
        config = HookConfig(
            event=HookEvent.PRE_TOOL,
            command="echo 'warning' >&2; exit 1",
            timeout=5,
            skill_name="test",
        )
        env = dict(__import__("os").environ)
        result = await _exec_command_hook(config, {}, env, "00004")
        assert result.exit_code == 1
        assert result.error == "warning"

    @pytest.mark.asyncio
    async def test_timeout(self):
        config = HookConfig(
            event=HookEvent.PRE_TOOL,
            command="sleep 10",
            timeout=0.1,
            skill_name="test",
        )
        env = dict(__import__("os").environ)
        result = await _exec_command_hook(config, {}, env, "00004")
        assert result.exit_code == 1
        assert "timed out" in result.error.lower()

    @pytest.mark.asyncio
    async def test_general_exception(self):
        config = HookConfig(
            event=HookEvent.PRE_TOOL,
            command="echo ok",
            timeout=5,
            skill_name="test",
        )
        with patch("onemancompany.core.skill_hooks.asyncio.create_subprocess_shell",
                    side_effect=OSError("spawn failed")):
            result = await _exec_command_hook(config, {}, {}, "00004")
        assert result.exit_code == 1
        assert "spawn failed" in result.error

    @pytest.mark.asyncio
    async def test_json_with_updated_input(self):
        config = HookConfig(
            event=HookEvent.PRE_TOOL,
            command='echo \'{"updatedInput":{"key":"val"}}\'',
            timeout=5,
            skill_name="test",
        )
        env = dict(__import__("os").environ)
        result = await _exec_command_hook(config, {}, env, "00004")
        assert result.updated_input == {"key": "val"}


# ---------------------------------------------------------------------------
# _exec_callback_hook exception
# ---------------------------------------------------------------------------

class TestExecCallbackHookException:
    @pytest.mark.asyncio
    async def test_callback_raises_exception(self):
        async def bad_hook(inp):
            raise ValueError("oops")

        config = HookConfig(
            event=HookEvent.TASK_START,
            callback=bad_hook,
            timeout=5,
            skill_name="bad-skill",
        )
        result = await _exec_callback_hook(config, {})
        assert result.exit_code == 1
        assert "oops" in result.error


# ---------------------------------------------------------------------------
# run_hooks comprehensive branches
# ---------------------------------------------------------------------------

class TestRunHooksComprehensive:
    @pytest.mark.asyncio
    async def test_run_hooks_with_command_hook(self):
        """Command hooks are executed via run_hooks."""
        register_skill_hooks("00004", "cmd-skill", {
            "task_start": [{"command": "echo '{}'"}],
        })
        # Mock subprocess to avoid event loop cleanup issues in CI
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b'{}', b''))
        mock_proc.returncode = 0
        with patch("asyncio.create_subprocess_shell", new_callable=AsyncMock, return_value=mock_proc):
            results = await run_hooks("00004", HookEvent.TASK_START, task_id="t1")
        assert len(results) == 1
        assert results[0].exit_code == 0

    @pytest.mark.asyncio
    async def test_run_hooks_with_tool_output_and_error(self):
        """run_hooks sets TOOL_OUTPUT and EXIT_CODE env vars."""
        async def check_hook(inp):
            assert "tool_output" in inp
            assert "error_message" in inp
            return {}

        register_callback_hook("00004", HookEvent.POST_TOOL_FAILURE, check_hook)
        results = await run_hooks(
            "00004", HookEvent.POST_TOOL_FAILURE,
            tool_name="bash",
            tool_input={"cmd": "ls"},
            tool_output={"error": "failed"},
            error_message="command failed",
        )
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_run_hooks_filtered_to_empty(self):
        """Hooks filtered by matcher to zero should return empty."""
        register_skill_hooks("00004", "s", {
            "pre_tool": [{"command": "echo ok", "matcher": "specific_tool"}],
        })
        results = await run_hooks("00004", HookEvent.PRE_TOOL, tool_name="other_tool")
        assert results == []

    @pytest.mark.asyncio
    async def test_run_hooks_with_tool_input(self):
        async def cap_hook(inp):
            assert inp["tool_input"] == {"arg": "val"}
            return {}

        register_callback_hook("00004", HookEvent.PRE_TOOL, cap_hook)
        results = await run_hooks(
            "00004", HookEvent.PRE_TOOL,
            tool_name="bash",
            tool_input={"arg": "val"},
        )
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_run_hooks_no_command_no_callback(self):
        """Hook with neither command nor callback is skipped."""
        config = HookConfig(event=HookEvent.TASK_START, skill_name="empty")
        _registry[("00004", HookEvent.TASK_START)] = [config]
        results = await run_hooks("00004", HookEvent.TASK_START)
        assert results == []

    @pytest.mark.asyncio
    async def test_run_hooks_gather_exception_converted(self):
        """Raw exceptions from gather are converted to HookResults."""
        # Patch gather to return an exception object (simulating return_exceptions=True)
        real_gather = asyncio.gather

        async def fake_gather(*coros, **kwargs):
            # Run normally but replace first result with an exception
            results = await real_gather(*coros, **kwargs)
            return [RuntimeError("raw exception")] + list(results[1:])

        async def ok_hook(inp):
            return {"decision": "allow"}

        register_callback_hook("00004", HookEvent.TASK_START, ok_hook)

        with patch("onemancompany.core.skill_hooks.asyncio.gather", side_effect=fake_gather):
            results = await run_hooks("00004", HookEvent.TASK_START)
        assert len(results) == 1
        assert results[0].exit_code == 1
        assert "raw exception" in results[0].error

    @pytest.mark.asyncio
    async def test_run_hooks_tool_output_no_error(self):
        """tool_output without error_message sets EXIT_CODE=0."""
        async def hook(inp):
            assert "tool_output" in inp
            return {}

        register_callback_hook("00004", HookEvent.POST_TOOL, hook)
        results = await run_hooks(
            "00004", HookEvent.POST_TOOL,
            tool_name="bash",
            tool_output={"ok": True},
        )
        assert len(results) == 1


# ---------------------------------------------------------------------------
# load_hooks_from_skills
# ---------------------------------------------------------------------------

class TestLoadHooksFromSkills:
    def test_loads_and_registers(self):
        with patch("onemancompany.core.config.load_employee_skills", return_value={"my-skill": "content"}) as _, \
             patch("onemancompany.agents.base._parse_skill_frontmatter") as mock_parse:
            mock_parse.return_value = (
                {"metadata": {"hooks": {"task_start": [{"command": "echo hi"}]}}},
                "Body",
            )
            total = load_hooks_from_skills("00004")
        assert total == 1
        assert len(get_hooks("00004", HookEvent.TASK_START)) == 1

    def test_non_dict_metadata(self):
        """Non-dict metadata.hooks falls back to empty."""
        with patch("onemancompany.core.config.load_employee_skills", return_value={"s": "content"}) as _, \
             patch("onemancompany.agents.base._parse_skill_frontmatter") as mock_parse:
            mock_parse.return_value = (
                {"metadata": "not-a-dict"},
                "Body",
            )
            total = load_hooks_from_skills("00004")
        assert total == 0

    def test_no_hooks_in_metadata(self):
        with patch("onemancompany.core.config.load_employee_skills", return_value={"s": "content"}) as _, \
             patch("onemancompany.agents.base._parse_skill_frontmatter") as mock_parse:
            mock_parse.return_value = ({"metadata": {}}, "Body")
            total = load_hooks_from_skills("00004")
        assert total == 0


class TestToolEventFiltering:
    @pytest.mark.asyncio
    async def test_matcher_filters_tool_name(self):
        called = []

        async def hook_a(inp):
            called.append("a")
            return {}

        async def hook_b(inp):
            called.append("b")
            return {}

        register_callback_hook("00004", HookEvent.PRE_TOOL, hook_a, matcher="bash")
        register_callback_hook("00004", HookEvent.PRE_TOOL, hook_b, matcher="write")

        await run_hooks("00004", HookEvent.PRE_TOOL, tool_name="bash")
        assert called == ["a"]

    @pytest.mark.asyncio
    async def test_empty_matcher_matches_all(self):
        called = []

        async def hook(inp):
            called.append(inp["tool_name"])
            return {}

        register_callback_hook("00004", HookEvent.POST_TOOL, hook)
        await run_hooks("00004", HookEvent.POST_TOOL, tool_name="anything")
        assert called == ["anything"]
