"""Unit tests for core/skill_hooks.py — CC-style skill hooks system."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from onemancompany.core.skill_hooks import (
    HookConfig, HookEvent, HookResult,
    _matches, _registry,
    clear_hooks, collect_context, get_hooks, get_updated_input,
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

    def test_register_cc_frontmatter_format(self):
        """CC frontmatter format: before_start/after_complete/on_error with trigger."""
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
        count = register_skill_hooks("00004", "sia", hooks_meta)
        # ask_first skipped, 3 registered (before_start + after_complete + on_error)
        assert count == 3
        assert len(get_hooks("00004", HookEvent.TASK_START)) == 1
        assert len(get_hooks("00004", HookEvent.TASK_COMPLETE)) == 1
        assert len(get_hooks("00004", HookEvent.TASK_ERROR)) == 1

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
