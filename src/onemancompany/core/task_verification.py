"""Task verification — runtime evidence collection for task completion.

Scans execution logs to build verification evidence:
- Which tools were called and their results
- Unresolved errors (tool failures not followed by a success)
- Files created/modified during execution

This evidence is attached to the task node and injected into reviewer prompts,
so reviewers have concrete data instead of relying on the employee's claims.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

from onemancompany.core.config import ENCODING_UTF8


@dataclass
class VerificationEvidence:
    """Evidence collected from a task's execution log."""
    tools_called: list[str] = field(default_factory=list)
    tools_succeeded: list[str] = field(default_factory=list)
    tools_failed: list[dict] = field(default_factory=list)  # [{"tool": name, "error": msg}]
    unresolved_errors: list[dict] = field(default_factory=list)  # failures not followed by success
    files_written: list[str] = field(default_factory=list)
    commands_run: list[dict] = field(default_factory=list)  # [{"cmd": str, "exit_code": int}]

    @property
    def has_unresolved_errors(self) -> bool:
        return len(self.unresolved_errors) > 0

    @property
    def summary(self) -> str:
        """One-line summary for quick display."""
        parts = []
        if self.tools_called:
            parts.append(f"{len(self.tools_called)} tool calls")
        if self.tools_succeeded:
            parts.append(f"{len(self.tools_succeeded)} succeeded")
        if self.unresolved_errors:
            parts.append(f"⚠ {len(self.unresolved_errors)} unresolved error(s)")
        if self.files_written:
            parts.append(f"{len(self.files_written)} file(s) written")
        return ", ".join(parts) if parts else "no tool activity"

    def to_dict(self) -> dict:
        return {
            "tools_called": self.tools_called,
            "tools_succeeded": self.tools_succeeded,
            "tools_failed": self.tools_failed,
            "unresolved_errors": self.unresolved_errors,
            "files_written": self.files_written,
            "commands_run": self.commands_run,
        }

    def to_review_block(self) -> str:
        """Format as a text block for injection into reviewer prompts."""
        lines = ["[Verification Evidence]"]

        if not self.tools_called:
            lines.append("  No tools were called during execution.")
            return "\n".join(lines)

        lines.append(f"  Tools called: {', '.join(self.tools_called)}")

        if self.files_written:
            for f in self.files_written[:5]:
                lines.append(f"  ✓ File written: {f}")
            if len(self.files_written) > 5:
                lines.append(f"  ... and {len(self.files_written) - 5} more")

        if self.commands_run:
            for cmd in self.commands_run[:3]:
                icon = "✓" if cmd.get("exit_code", -1) == 0 else "✗"
                lines.append(f"  {icon} Command: {cmd.get('cmd', '?')[:80]} (exit {cmd.get('exit_code', '?')})")

        if self.unresolved_errors:
            lines.append("  ⚠ UNRESOLVED ERRORS:")
            for err in self.unresolved_errors:
                lines.append(f"    - {err.get('tool', '?')}: {err.get('error', '?')[:100]}")

        lines.append("[/Verification Evidence]")
        return "\n".join(lines)


def collect_evidence(project_dir: str, node_id: str) -> VerificationEvidence:
    """Scan a node's execution log and build verification evidence.

    Parses the JSONL execution log to extract:
    - tool_call entries → tools_called
    - tool_result entries → success/failure tracking
    - write tool calls → files_written
    - bash tool calls → commands_run with exit codes
    """
    evidence = VerificationEvidence()
    log_path = Path(project_dir) / "nodes" / node_id / "execution.log"

    if not log_path.exists():
        return evidence

    # Track pending tool calls to match with results
    pending_calls: dict[str, str] = {}  # tool_call_id → tool_name
    # Track tool errors: tool_name → error_msg (cleared on success)
    error_tracker: dict[str, str] = {}

    try:
        for line in log_path.read_text(encoding=ENCODING_UTF8).splitlines():
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                logger.debug("[verification] Skipping malformed JSONL line in {}", log_path)
                continue

            log_type = entry.get("type", "")
            content = entry.get("content", "")

            if log_type == "tool_call":
                _parse_tool_call(content, evidence, pending_calls)
            elif log_type == "tool_result":
                _parse_tool_result(content, evidence, error_tracker)
    except Exception as e:
        logger.debug("[verification] Failed to parse execution log {}: {}", log_path, e)

    # Remaining errors in tracker are unresolved
    for tool_name, error_msg in error_tracker.items():
        evidence.unresolved_errors.append({"tool": tool_name, "error": error_msg})

    return evidence


def _parse_tool_call(content: str, evidence: VerificationEvidence, pending: dict) -> None:
    """Parse a tool_call log entry."""
    # Format: "tool_name({args})" or "tool_name → ..."
    if not content:
        return

    # Extract tool name from "tool_name({...})" format
    paren_idx = content.find("(")
    if paren_idx > 0:
        tool_name = content[:paren_idx].strip()
        evidence.tools_called.append(tool_name)

        # Extract args for specific tools
        args_str = content[paren_idx + 1:].rstrip(")")
        args = {}
        try:
            # Try JSON first, then eval-style dict with single quotes
            if args_str.startswith("{"):
                try:
                    args = json.loads(args_str)
                except json.JSONDecodeError:
                    args = json.loads(args_str.replace("'", '"'))
        except (json.JSONDecodeError, ValueError):
            args = {}

        if tool_name == "write" and args.get("file_path"):
            evidence.files_written.append(args["file_path"])
        elif tool_name == "bash" and args.get("command"):
            evidence.commands_run.append({"cmd": args["command"], "exit_code": None})


def _parse_tool_result(content: str, evidence: VerificationEvidence, error_tracker: dict) -> None:
    """Parse a tool_result log entry and track errors."""
    if not content:
        return

    # Format: "tool_name → content='...'" or "tool_name → {'status': 'error', ...}"
    arrow_idx = content.find(" → ")
    if arrow_idx < 0:
        return

    tool_name = content[:arrow_idx].strip()

    result_str = content[arrow_idx + 3:]

    # Check for error indicators
    is_error = False
    error_msg = ""

    if '"status": "error"' in result_str or "'status': 'error'" in result_str:
        is_error = True
        # Extract error message
        for pattern in ['"message": "', "'message': '"]:
            idx = result_str.find(pattern)
            if idx >= 0:
                start = idx + len(pattern)
                end = result_str.find('"', start)
                if end < 0:
                    end = result_str.find("'", start)
                error_msg = result_str[start:end] if end > start else result_str[start:start + 100]
                break

    # bash: check exit code
    if tool_name == "bash":
        if '"returncode": 0' in result_str or "'returncode': 0" in result_str:
            is_error = False
            # Update last bash command with exit code
            for cmd in reversed(evidence.commands_run):
                if cmd.get("exit_code") is None:
                    cmd["exit_code"] = 0
                    break
        else:
            # Non-zero exit code
            for code_pattern in ['"returncode": ', "'returncode': "]:
                idx = result_str.find(code_pattern)
                if idx >= 0:
                    code_str = result_str[idx + len(code_pattern):idx + len(code_pattern) + 5]
                    try:
                        exit_code = int(code_str.split(",")[0].split("}")[0])
                        for cmd in reversed(evidence.commands_run):
                            if cmd.get("exit_code") is None:
                                cmd["exit_code"] = exit_code
                                break
                        if exit_code != 0:
                            is_error = True
                            error_msg = f"exit code {exit_code}"
                    except ValueError:
                        logger.debug("[verification] Non-numeric returncode in bash result")

    if is_error:
        evidence.tools_failed.append({"tool": tool_name, "error": error_msg})
        error_tracker[tool_name] = error_msg
    else:
        evidence.tools_succeeded.append(tool_name)
        # Clear error for this tool — it succeeded after a previous failure
        error_tracker.pop(tool_name, None)
