"""CEO Inbox conversation sessions and message persistence.

Each conversation is tied to a task tree node (node_type="ceo_request").
Messages are stored as YAML lists in {project_dir}/conversations/{node_id}.yaml.
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from onemancompany.core.config import AGENT_DIR_NAME, CONVERSATIONS_DIR_NAME, ENCODING_UTF8

CEO_SENDER = "ceo"
EA_SENDER = "ea"
CEO_CONVERSATION_CATEGORY = "ceo_conversation"
EA_AUTO_REPLY_DELAY_SECONDS = 2  # Near-instant — just enough for session.run() to start

# ---------------------------------------------------------------------------
# Message persistence (SSOT = disk)
# ---------------------------------------------------------------------------

def _conv_path(conv_dir: Path, node_id: str) -> Path:
    return conv_dir / f"{node_id}.yaml"


def load_messages(conv_dir: Path, node_id: str) -> list[dict]:
    """Load all messages for a conversation from disk."""
    path = _conv_path(conv_dir, node_id)
    if not path.exists():
        return []
    from onemancompany.core.store import _read_yaml_list
    return _read_yaml_list(path)


def append_message(
    conv_dir: Path,
    node_id: str,
    *,
    sender: str,
    text: str,
    attachments: list[dict] | None = None,
) -> dict:
    """Append a message to a conversation and return it."""
    import yaml as _yaml

    msg: dict[str, Any] = {
        "sender": sender,
        "text": text,
        "timestamp": datetime.now().isoformat(),
    }
    if attachments:
        msg["attachments"] = attachments

    path = _conv_path(conv_dir, node_id)
    path.parent.mkdir(parents=True, exist_ok=True)

    messages = load_messages(conv_dir, node_id)
    messages.append(msg)

    path.write_text(
        _yaml.dump(messages, allow_unicode=True, default_flow_style=False),
        encoding=ENCODING_UTF8,
    )
    return msg


# ---------------------------------------------------------------------------
# Conversation session
# ---------------------------------------------------------------------------

COMPLETE_SIGNAL = object()  # Sentinel pushed to queue to terminate loop


async def _build_agent_and_invoke(
    employee_id: str,
    text: str,
    chat_history: list[dict],
    *,
    node_id: str = "",
    project_dir: str = "",
) -> str:
    """Build a one-shot LLM call for the employee and return response text."""
    from onemancompany.agents.base import make_llm, tracked_ainvoke, _extract_text
    from onemancompany.agents.prompt_builder import PromptBuilder
    from onemancompany.core.config import employee_configs
    from langchain_core.messages import HumanMessage, SystemMessage, AIMessage

    cfg = employee_configs.get(employee_id)
    if not cfg:
        return f"[Error: employee {employee_id} not found in config]"

    llm = make_llm(employee_id)
    builder = PromptBuilder()

    # Add basic role context from employee config
    role_parts = [f"You are employee {employee_id}."]
    if cfg.name:
        role_parts[0] = f"You are {cfg.name} (employee {employee_id})."
    if cfg.role:
        role_parts.append(f"Role: {cfg.role}")
    builder.add("role", "\n".join(role_parts), priority=10)

    # Load talent persona if available
    from onemancompany.core.config import EMPLOYEES_DIR
    persona_path = EMPLOYEES_DIR / employee_id / AGENT_DIR_NAME / "system_prompt.md"
    if persona_path.exists():
        try:
            builder.add("persona", persona_path.read_text(encoding=ENCODING_UTF8), priority=15)
        except Exception as exc:
            logger.debug("Failed to load persona for {}: {}", employee_id, exc)

    # Inject task/project context so the employee knows what this conversation is about
    if node_id and project_dir:
        context_parts = []
        proj_path = Path(project_dir)
        # Load the task node description
        from onemancompany.core.config import TASK_TREE_FILENAME
        tree_path = proj_path / TASK_TREE_FILENAME
        if tree_path.exists():
            try:
                from onemancompany.core.task_tree import get_tree
                tree = get_tree(tree_path)
                node = tree.get_node(node_id)
                if node:
                    context_parts.append(f"You are responding to a CEO inbox request (node {node_id}).")
                    context_parts.append(f"Task description: {node.description}")
                    if node.acceptance_criteria:
                        context_parts.append(f"Acceptance criteria: {', '.join(node.acceptance_criteria)}")
                    if node.parent_id:
                        parent = tree.get_node(node.parent_id)
                        if parent:
                            context_parts.append(f"Parent task: {parent.description_preview}")
            except Exception as exc:
                logger.debug("Failed to load task context for node {}: {}", node_id, exc)
        # Add project directory for reference
        context_parts.append(f"Project workspace: {project_dir}")
        if context_parts:
            builder.add("task_context", "\n".join(context_parts), priority=20)

    system_prompt = builder.build()

    messages = [SystemMessage(content=system_prompt)]
    for m in chat_history:
        if m["sender"] == CEO_SENDER:
            messages.append(HumanMessage(content=m["text"]))
        else:
            messages.append(AIMessage(content=m["text"]))
    messages.append(HumanMessage(content=text))

    result = await tracked_ainvoke(
        llm, messages,
        category=CEO_CONVERSATION_CATEGORY,
        employee_id=employee_id,
    )
    return _extract_text(result.content)


async def _ea_auto_reply(node_id: str, description: str, broadcast_fn) -> str:
    """EA reads the request description and decides accept/reject on behalf of CEO."""
    from onemancompany.agents.base import make_llm, tracked_ainvoke, _extract_text
    from onemancompany.core.config import EA_ID

    llm = make_llm(EA_ID)
    prompt = (
        "You are the EA (Executive Assistant), making a decision on behalf of the CEO.\n\n"
        "An employee has sent the following request to the CEO inbox:\n"
        f"---\n{description}\n---\n\n"
        "The CEO has not responded within the timeout period. "
        "You need to make a decision: accept or reject this request, with a brief reason.\n\n"
        "Guidelines:\n"
        "- Accept requests that are reasonable, well-scoped, and align with business goals\n"
        "- Reject requests that are vague, out of scope, or need more information\n"
        "- Keep your response concise (2-3 sentences)\n\n"
        "Return your decision in JSON format:\n"
        '{"decision": "accept" or "reject", "reason": "your brief explanation"}\n'
        "Only return JSON, no other content."
    )

    resp = await tracked_ainvoke(llm, prompt, category="ea_auto_reply", employee_id=EA_ID)
    raw = _extract_text(resp.content)

    decision = "accept"
    reason = "EA auto-approved (no valid response parsed)"
    try:
        json_match = re.search(r'\{.*\}', raw, re.DOTALL)
        if json_match:
            parsed = json.loads(json_match.group())
            decision = parsed.get("decision", "accept")
            reason = parsed.get("reason", "")
    except (json.JSONDecodeError, AttributeError) as exc:
        logger.debug("[ea_auto_reply] failed to parse EA response: {}", exc)

    reply_text = f"[EA Auto-Reply] Decision: {decision.upper()}\n{reason}"
    logger.info("[ea_auto_reply] node={} decision={} reason={}", node_id, decision, reason)
    return reply_text


async def _ea_analyze_conversation(
    history: list[dict], description: str, node_id: str,
) -> dict:
    """EA analyzes a completed CEO inbox conversation to extract intent.

    Returns:
        {
            "decision": "accept" | "reject",
            "reason": "brief explanation",
            "follow_up_tasks": [{"description": "...", "assignee_hint": "..."}]
        }
    """
    from onemancompany.agents.base import make_llm, tracked_ainvoke, _extract_text
    from onemancompany.core.config import EA_ID

    # Format conversation for the prompt
    conv_lines = []
    for m in history:
        role = "CEO" if m["sender"] == CEO_SENDER else "Employee"
        conv_lines.append(f"[{role}]: {m['text']}")
    conv_text = "\n".join(conv_lines)

    try:
        llm = make_llm(EA_ID)
    except Exception as exc:
        logger.error("[ea_analyze] failed to create LLM for node {}: {}", node_id, exc)
        return {"decision": "accept", "reason": "EA analysis failed, defaulting to accept", "follow_up_tasks": []}

    prompt = (
        "You are the EA (Executive Assistant). Analyze this completed CEO inbox conversation "
        "and determine the CEO's intent.\n\n"
        f"Original request from employee:\n---\n{description}\n---\n\n"
        f"Conversation:\n---\n{conv_text}\n---\n\n"
        "Determine:\n"
        "1. Did the CEO accept or reject the employee's request?\n"
        "2. Did the CEO mention any follow-up tasks or additional instructions?\n\n"
        "Guidelines:\n"
        "- If CEO said things like 'ok', 'approved', 'go ahead', 'sounds good' → accept\n"
        "- If CEO said things like 'no', 'reject', 'don't do this', 'not now' → reject\n"
        "- If CEO gave instructions like 'also do X', 'add Y', 'change Z' → extract as follow_up_tasks\n"
        "- follow_up_tasks should only include NEW tasks the CEO requested, not the original request\n"
        "- assignee_hint can be a role like 'COO', 'HR', or empty string if unclear\n"
        "- If the conversation is ambiguous, default to accept\n\n"
        "Return JSON only:\n"
        '{"decision": "accept" or "reject", "reason": "brief explanation", '
        '"follow_up_tasks": [{"description": "task description", "assignee_hint": "role or employee_id"}]}\n'
        "Only return JSON, no other content."
    )

    try:
        resp = await tracked_ainvoke(llm, prompt, category="ea_analyze_conversation", employee_id=EA_ID)
        raw = _extract_text(resp.content)

        json_match = re.search(r'\{.*\}', raw, re.DOTALL)
        if json_match:
            parsed = json.loads(json_match.group())
            decision = parsed.get("decision", "accept")
            reason = parsed.get("reason", "")
            follow_ups = parsed.get("follow_up_tasks", [])
            # Validate follow_ups structure
            valid_follow_ups = []
            for ft in (follow_ups or []):
                if isinstance(ft, dict) and ft.get("description"):
                    valid_follow_ups.append({
                        "description": ft["description"],
                        "assignee_hint": ft.get("assignee_hint", ""),
                    })
            result = {
                "decision": decision if decision in ("accept", "reject") else "accept",
                "reason": reason,
                "follow_up_tasks": valid_follow_ups,
            }
            logger.info(
                "[ea_analyze] node={} decision={} reason={} follow_ups={}",
                node_id, result["decision"], reason[:80], len(valid_follow_ups),
            )
            return result
    except Exception as exc:
        logger.error("[ea_analyze] failed for node {}: {}", node_id, exc)

    return {"decision": "accept", "reason": "EA analysis failed, defaulting to accept", "follow_up_tasks": []}


class ConversationSession:
    """Manages an async CEO<->employee conversation for one task node."""

    def __init__(self, node_id: str, employee_id: str, project_dir: str, broadcast_fn):
        self.node_id = node_id
        self.employee_id = employee_id
        self.project_dir = project_dir
        self._broadcast = broadcast_fn
        self._queue: asyncio.Queue = asyncio.Queue()
        self._conv_dir = Path(project_dir) / CONVERSATIONS_DIR_NAME
        self._ea_auto_reply_enabled = False
        self._ea_timer_task: asyncio.Task | None = None
        self._ceo_replied = False
        self._description = ""

    def set_ea_auto_reply(self, enabled: bool, description: str = "") -> None:
        """Enable or disable EA auto-reply timer."""
        self._ea_auto_reply_enabled = enabled
        if description:
            self._description = description
        if enabled:
            self._ceo_replied = False
            self._start_ea_timer()
            logger.info("[ea_auto_reply] enabled for node={}", self.node_id)
        else:
            self._cancel_ea_timer()
            logger.info("[ea_auto_reply] disabled for node={}", self.node_id)

    def _start_ea_timer(self) -> None:
        """Start the EA auto-reply countdown."""
        self._cancel_ea_timer()
        self._ea_timer_task = asyncio.ensure_future(self._ea_timer_loop())

    def _cancel_ea_timer(self) -> None:
        """Cancel any pending EA auto-reply timer."""
        if self._ea_timer_task and not self._ea_timer_task.done():
            self._ea_timer_task.cancel()
            self._ea_timer_task = None

    async def _ea_timer_loop(self) -> None:
        """Auto-reply immediately when EA auto-reply is enabled (no countdown)."""
        try:
            # Small yield to let session.run() start listening first
            await asyncio.sleep(EA_AUTO_REPLY_DELAY_SECONDS)
            if self._ceo_replied or not self._ea_auto_reply_enabled:
                return
            logger.info("[ea_auto_reply] timeout reached, auto-replying for node={}", self.node_id)
            reply_text = await _ea_auto_reply(self.node_id, self._description, self._broadcast)
            # Persist as CEO_SENDER (EA acts on behalf of CEO — employee
            # must see it as HumanMessage). Broadcast with origin=ea so
            # the frontend can display it differently. Disk = source of truth.
            ea_msg = append_message(
                self._conv_dir, self.node_id,
                sender=CEO_SENDER, text=reply_text,
            )
            await self._queue.put(ea_msg)
            await self._broadcast({
                "type": CEO_CONVERSATION_CATEGORY,
                "node_id": self.node_id,
                "sender": CEO_SENDER,
                "origin": EA_SENDER,
                "text": reply_text,
                "timestamp": ea_msg["timestamp"],
            })
            # Auto-complete after EA reply (guard against late CEO reply)
            await asyncio.sleep(2)
            if not self._ceo_replied:
                await self.complete()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("[ea_auto_reply] timer error for node {}: {}", self.node_id, e)

    async def send(self, text: str, attachments=None) -> dict:
        """Queue a CEO message for processing."""
        self._ceo_replied = True
        self._cancel_ea_timer()
        msg = append_message(
            self._conv_dir, self.node_id,
            sender=CEO_SENDER, text=text, attachments=attachments,
        )
        await self._queue.put(msg)
        return msg

    async def complete(self):
        """Signal that the conversation is done; triggers summary generation."""
        self._cancel_ea_timer()
        await self._queue.put(COMPLETE_SIGNAL)

    async def run(self) -> str:
        """Main async loop: process messages until COMPLETE_SIGNAL."""
        logger.info("CEO conversation started: node={}, employee={}", self.node_id, self.employee_id)
        while True:
            msg = await self._queue.get()
            if msg is COMPLETE_SIGNAL:
                history = load_messages(self._conv_dir, self.node_id)
                try:
                    summary = await _build_agent_and_invoke(
                        self.employee_id,
                        "Please summarize the result of this conversation in one paragraph as a task completion report.",
                        history,
                        node_id=self.node_id,
                        project_dir=self.project_dir,
                    )
                except Exception as e:
                    logger.error("Summary generation failed: {}", e)
                    summary = ""
                logger.info("CEO conversation completed: node={}", self.node_id)
                return summary

            try:
                history = load_messages(self._conv_dir, self.node_id)
                response_text = await _build_agent_and_invoke(
                    self.employee_id, msg["text"], history[:-1],
                    node_id=self.node_id, project_dir=self.project_dir,
                )
            except Exception as e:
                logger.error("Agent invoke failed for node {}: {}", self.node_id, e)
                response_text = f"[Error: {e}]"

            agent_msg = append_message(
                self._conv_dir, self.node_id,
                sender=self.employee_id, text=response_text,
            )
            await self._broadcast({
                "type": CEO_CONVERSATION_CATEGORY,
                "node_id": self.node_id,
                "sender": self.employee_id,
                "text": response_text,
                "timestamp": agent_msg["timestamp"],
            })


# ---------------------------------------------------------------------------
# Session registry (in-memory, rebuilt on reopen)
# ---------------------------------------------------------------------------

_active_sessions: dict[str, ConversationSession] = {}


def register_session(session: ConversationSession) -> None:
    _active_sessions[session.node_id] = session


def unregister_session(node_id: str) -> None:
    _active_sessions.pop(node_id, None)


def get_session(node_id: str) -> ConversationSession | None:
    return _active_sessions.get(node_id)
