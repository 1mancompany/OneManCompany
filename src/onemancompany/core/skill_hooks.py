"""Skill Hooks — CC-style lifecycle hooks for company-hosted agents.

Skills can define hooks in SKILL.md frontmatter that fire at key
lifecycle points: tool calls (pre/post) and task lifecycle (start/
complete/error). Hooks execute shell commands with JSON I/O.

Hook events:
  pre_tool, post_tool, post_tool_failure  — tool-level
  task_start, task_complete, task_error    — task-level

Exit codes (CC-compatible):
  0  — success, continue
  2  — block (pre_tool only: abort tool execution)
  other — warning, continue
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Awaitable

from loguru import logger

from onemancompany.core.config import EMPLOYEES_DIR


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

class HookEvent(str, Enum):
    PRE_TOOL = "pre_tool"
    POST_TOOL = "post_tool"
    POST_TOOL_FAILURE = "post_tool_failure"
    TASK_START = "task_start"
    TASK_COMPLETE = "task_complete"
    TASK_ERROR = "task_error"


TOOL_EVENTS = frozenset({HookEvent.PRE_TOOL, HookEvent.POST_TOOL, HookEvent.POST_TOOL_FAILURE})
TASK_EVENTS = frozenset({HookEvent.TASK_START, HookEvent.TASK_COMPLETE, HookEvent.TASK_ERROR})

# CC event names → our event names (CC compat layer)
_CC_EVENT_MAP: dict[str, HookEvent] = {
    # CC names
    "PreToolUse": HookEvent.PRE_TOOL,
    "PostToolUse": HookEvent.POST_TOOL,
    "PostToolUseFailure": HookEvent.POST_TOOL_FAILURE,
    "SessionStart": HookEvent.TASK_START,
    "Stop": HookEvent.TASK_COMPLETE,
    "StopFailure": HookEvent.TASK_ERROR,
    # frontmatter names
    "before_start": HookEvent.TASK_START,
    "after_complete": HookEvent.TASK_COMPLETE,
    "on_error": HookEvent.TASK_ERROR,
    # our names (passthrough)
    "pre_tool": HookEvent.PRE_TOOL,
    "post_tool": HookEvent.POST_TOOL,
    "post_tool_failure": HookEvent.POST_TOOL_FAILURE,
    "task_start": HookEvent.TASK_START,
    "task_complete": HookEvent.TASK_COMPLETE,
    "task_error": HookEvent.TASK_ERROR,
}


def _resolve_event(name: str) -> HookEvent | None:
    """Resolve any event name format (CC, frontmatter, or ours) to HookEvent."""
    if name in _CC_EVENT_MAP:
        return _CC_EVENT_MAP[name]
    try:
        return HookEvent(name)
    except ValueError:
        return None

EXIT_BLOCK = 2
DEFAULT_TIMEOUT = 30


@dataclass
class HookConfig:
    """Single hook definition parsed from SKILL.md metadata."""
    event: HookEvent
    command: str = ""
    callback: Callable[..., Awaitable[dict]] | None = None
    matcher: str = ""       # tool name filter (exact, pipe-separated, regex)
    mode: str = "auto"      # auto | ask_first
    timeout: int = DEFAULT_TIMEOUT
    skill_name: str = ""    # source skill for logging


@dataclass
class HookResult:
    """Result from a single hook execution."""
    exit_code: int = 0
    decision: str = "allow"     # allow | block
    reason: str = ""
    updated_input: dict | None = None
    additional_context: str = ""
    error: str = ""


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

# (employee_id, event) → list of HookConfig
_registry: dict[tuple[str, HookEvent], list[HookConfig]] = {}


def register_skill_hooks(employee_id: str, skill_name: str, hooks_meta: dict) -> int:
    """Parse hooks from SKILL.md and register them.

    Supports three configuration formats:

    1. Our format (flat):
       hooks:
         pre_tool:
           - command: "bash script.sh"
             matcher: "bash"

    2. CC settings.json format (nested):
       hooks:
         PreToolUse:
           - matcher: "Bash|Write"
             hooks:
               - type: command
                 command: "bash script.sh"

    3. CC frontmatter format (trigger-based):
       hooks:
         before_start:
           - trigger: session-logger
             mode: auto

    Returns number of hooks registered.
    """
    count = 0
    for event_name, hook_list in hooks_meta.items():
        event = _resolve_event(event_name)
        if event is None:
            logger.warning("[hooks] Unknown event '{}' in skill {} for {}", event_name, skill_name, employee_id)
            continue

        if not isinstance(hook_list, list):
            hook_list = [hook_list]

        for h in hook_list:
            if not isinstance(h, dict):
                continue

            # CC nested format: {matcher: "...", hooks: [{type: command, command: "..."}]}
            if "hooks" in h:
                outer_matcher = h.get("matcher", "")
                for inner in h["hooks"]:
                    if not isinstance(inner, dict):
                        continue
                    count += _register_single_hook(
                        employee_id, event, skill_name,
                        command=inner.get("command", ""),
                        matcher=outer_matcher,
                        mode=inner.get("mode", "auto"),
                        timeout=inner.get("timeout", DEFAULT_TIMEOUT),
                    )
            else:
                # Flat format or frontmatter trigger format
                count += _register_single_hook(
                    employee_id, event, skill_name,
                    command=h.get("command", h.get("trigger", "")),
                    matcher=h.get("matcher", ""),
                    mode=h.get("mode", "auto"),
                    timeout=h.get("timeout", DEFAULT_TIMEOUT),
                )

    if count:
        logger.debug("[hooks] Registered {} hooks from skill '{}' for {}", count, skill_name, employee_id)
    return count


def _register_single_hook(
    employee_id: str,
    event: HookEvent,
    skill_name: str,
    command: str,
    matcher: str = "",
    mode: str = "auto",
    timeout: int = DEFAULT_TIMEOUT,
) -> int:
    """Register one hook. Returns 1 if registered, 0 if skipped."""
    if mode == "ask_first":
        return 0  # company-hosted agents can't ask for confirmation
    if not command:
        return 0

    config = HookConfig(
        event=event,
        command=command,
        matcher=matcher,
        mode=mode,
        timeout=timeout,
        skill_name=skill_name,
    )
    key = (employee_id, event)
    _registry.setdefault(key, []).append(config)
    return 1


def register_callback_hook(
    employee_id: str,
    event: HookEvent,
    callback: Callable[..., Awaitable[dict]],
    matcher: str = "",
    skill_name: str = "_internal",
) -> None:
    """Register a Python callback hook (internal use)."""
    config = HookConfig(
        event=event,
        callback=callback,
        matcher=matcher,
        skill_name=skill_name,
    )
    key = (employee_id, event)
    _registry.setdefault(key, []).append(config)


def clear_hooks(employee_id: str) -> None:
    """Remove all hooks for an employee (used on re-registration)."""
    keys = [k for k in _registry if k[0] == employee_id]
    for k in keys:
        del _registry[k]


def get_hooks(employee_id: str, event: HookEvent) -> list[HookConfig]:
    """Get all hooks registered for (employee_id, event)."""
    return _registry.get((employee_id, event), [])


# ---------------------------------------------------------------------------
# Matcher
# ---------------------------------------------------------------------------

def _matches(matcher: str, tool_name: str) -> bool:
    """Check if a hook matcher matches a tool name.

    Supports: exact, pipe-separated, regex.
    Empty matcher matches everything.
    """
    if not matcher:
        return True
    # Pipe-separated list (no regex chars other than pipe)
    if "|" in matcher and not any(c in matcher for c in "^$.*+?[]()\\"):
        return tool_name in matcher.split("|")
    # Try regex
    try:
        return bool(re.fullmatch(matcher, tool_name))
    except re.error:
        return matcher == tool_name


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

def _build_env(
    employee_id: str,
    event: HookEvent,
    tool_name: str = "",
    tool_input: dict | None = None,
    task_id: str = "",
) -> dict[str, str]:
    """Build environment variables for hook subprocess.

    Sets both OMC_* vars (our convention) and CC-compatible vars
    ($TOOL_NAME, $TOOL_INPUT, $SKILLS_DIR, etc.) so hook scripts
    from either ecosystem work without modification.
    """
    skills_dir = str(EMPLOYEES_DIR / employee_id / "skills")
    tool_input_json = json.dumps(tool_input, ensure_ascii=False, default=str)[:10000] if tool_input else ""
    env = {
        **os.environ,
        # Our vars
        "OMC_EMPLOYEE_ID": employee_id,
        "OMC_TASK_ID": task_id,
        "OMC_HOOK_EVENT": event.value,
        "OMC_SKILLS_DIR": skills_dir,
        "OMC_TOOL_NAME": tool_name or "",
        "OMC_TOOL_INPUT": tool_input_json,
        # CC-compatible vars
        "SKILLS_DIR": skills_dir,
        "TOOL_NAME": tool_name or "",
        "TOOL_INPUT": tool_input_json,
    }
    return env


def _expand_command(command: str, env: dict[str, str]) -> str:
    """Expand ${VAR} and $VAR references in command string using env dict."""
    result = command
    # Expand ${VAR} first, then $VAR (longer match first to avoid partial replace)
    for key, val in env.items():
        result = result.replace(f"${{{key}}}", val)
    for key, val in env.items():
        # Only replace $VAR if not already expanded as ${VAR}
        # and followed by word boundary (space, quote, end)
        result = result.replace(f"${key}", val)
    return result


async def _exec_command_hook(
    config: HookConfig,
    hook_input: dict,
    env: dict[str, str],
    employee_id: str,
) -> HookResult:
    """Execute a single command hook."""
    command = _expand_command(config.command, env)
    input_json = json.dumps(hook_input, ensure_ascii=False, default=str)

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=input_json.encode()),
            timeout=config.timeout,
        )
        exit_code = proc.returncode or 0

        # Parse JSON output from stdout
        result = HookResult(exit_code=exit_code)
        stdout_str = stdout.decode().strip()
        if stdout_str:
            try:
                data = json.loads(stdout_str)
                result.decision = data.get("decision", "allow")
                result.reason = data.get("reason", "")
                result.updated_input = data.get("updatedInput")
                result.additional_context = data.get("additionalContext", "")
            except json.JSONDecodeError:
                result.additional_context = stdout_str

        if stderr:
            result.error = stderr.decode().strip()
            if exit_code == EXIT_BLOCK:
                logger.warning("[hooks] {} blocked by '{}': {}", config.event.value, config.skill_name, result.error)
            elif exit_code != 0:
                logger.warning("[hooks] {} warning from '{}': {}", config.event.value, config.skill_name, result.error)

        return result

    except asyncio.TimeoutError:
        logger.warning("[hooks] {} timed out after {}s (skill: {})", config.event.value, config.timeout, config.skill_name)
        return HookResult(exit_code=1, error=f"Hook timed out after {config.timeout}s")
    except Exception as e:
        logger.warning("[hooks] {} error (skill: {}): {}", config.event.value, config.skill_name, e)
        return HookResult(exit_code=1, error=str(e))


async def _exec_callback_hook(
    config: HookConfig,
    hook_input: dict,
) -> HookResult:
    """Execute a single callback hook."""
    try:
        data = await asyncio.wait_for(config.callback(hook_input), timeout=config.timeout)
        return HookResult(
            decision=data.get("decision", "allow"),
            reason=data.get("reason", ""),
            updated_input=data.get("updatedInput"),
            additional_context=data.get("additionalContext", ""),
        )
    except asyncio.TimeoutError:
        return HookResult(exit_code=1, error=f"Callback timed out after {config.timeout}s")
    except Exception as e:
        logger.warning("[hooks] callback error (skill: {}): {}", config.skill_name, e)
        return HookResult(exit_code=1, error=str(e))


async def run_hooks(
    employee_id: str,
    event: HookEvent,
    tool_name: str = "",
    tool_input: dict | None = None,
    tool_output: dict | None = None,
    task_id: str = "",
    task_description: str = "",
    error_message: str = "",
) -> list[HookResult]:
    """Run all matching hooks for an event. Returns list of results.

    For pre_tool: if any result has exit_code==2 or decision=="block",
    the caller should abort the tool execution.
    """
    hooks = get_hooks(employee_id, event)
    if not hooks:
        return []

    # Filter by matcher for tool events
    if event in TOOL_EVENTS and tool_name:
        hooks = [h for h in hooks if _matches(h.matcher, tool_name)]
    if not hooks:
        return []

    # Build hook input
    hook_input: dict[str, Any] = {
        "employee_id": employee_id,
        "event": event.value,
        "task_id": task_id,
    }
    if tool_name:
        hook_input["tool_name"] = tool_name
    if tool_input is not None:
        hook_input["tool_input"] = tool_input
    if tool_output is not None:
        hook_input["tool_output"] = tool_output
    if task_description:
        hook_input["task_description"] = task_description
    if error_message:
        hook_input["error_message"] = error_message

    env = _build_env(employee_id, event, tool_name, tool_input, task_id)
    # CC-compat: add TOOL_OUTPUT and EXIT_CODE for post-tool hooks
    if tool_output is not None:
        env["TOOL_OUTPUT"] = json.dumps(tool_output, ensure_ascii=False, default=str)[:10000]
        env["OMC_TOOL_OUTPUT"] = env["TOOL_OUTPUT"]
    if error_message:
        env["EXIT_CODE"] = "1"
        env["OMC_ERROR_MESSAGE"] = error_message
    else:
        env["EXIT_CODE"] = "0"

    # Execute all hooks in parallel
    tasks = []
    for h in hooks:
        if h.callback:
            tasks.append(_exec_callback_hook(h, hook_input))
        elif h.command:
            tasks.append(_exec_command_hook(h, hook_input, env, employee_id))

    if not tasks:
        return []

    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Convert exceptions to HookResults
    final = []
    for r in results:
        if isinstance(r, Exception):
            final.append(HookResult(exit_code=1, error=str(r)))
        else:
            final.append(r)

    return final


def should_block(results: list[HookResult]) -> tuple[bool, str]:
    """Check if any hook result indicates blocking.

    Returns (should_block, reason).
    """
    for r in results:
        if r.exit_code == EXIT_BLOCK or r.decision == "block":
            return True, r.reason or r.error or "Blocked by hook"
    return False, ""


def get_updated_input(results: list[HookResult], original: dict) -> dict:
    """Apply updatedInput from hook results. Last writer wins."""
    result = dict(original)
    for r in results:
        if r.updated_input:
            result.update(r.updated_input)
    return result


def collect_context(results: list[HookResult]) -> str:
    """Collect additionalContext from all hook results."""
    parts = [r.additional_context for r in results if r.additional_context]
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Skill hooks loading
# ---------------------------------------------------------------------------

def load_hooks_from_skills(employee_id: str) -> int:
    """Load hooks from all of an employee's skills. Called on startup.

    Returns total number of hooks registered.
    """
    from onemancompany.core.config import load_employee_skills
    from onemancompany.agents.base import _parse_skill_frontmatter

    clear_hooks(employee_id)
    skills = load_employee_skills(employee_id)
    total = 0
    for skill_name, content in skills.items():
        meta, _body = _parse_skill_frontmatter(content)
        hooks_meta = meta.get("metadata", {})
        if isinstance(hooks_meta, dict):
            hooks_meta = hooks_meta.get("hooks", {})
        else:
            hooks_meta = {}
        if hooks_meta:
            total += register_skill_hooks(employee_id, skill_name, hooks_meta)

    return total
