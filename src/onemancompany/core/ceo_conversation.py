"""CEO Inbox conversation sessions and message persistence.

Each conversation is tied to a task tree node (node_type="ceo_request").
Messages are stored as YAML lists in {project_dir}/conversations/{node_id}.yaml.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger


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
        encoding="utf-8",
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
    persona_path = EMPLOYEES_DIR / employee_id / "agent" / "system_prompt.md"
    if persona_path.exists():
        try:
            builder.add("persona", persona_path.read_text(encoding="utf-8"), priority=15)
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
        if m["sender"] == "ceo":
            messages.append(HumanMessage(content=m["text"]))
        else:
            messages.append(AIMessage(content=m["text"]))
    messages.append(HumanMessage(content=text))

    result = await tracked_ainvoke(
        llm, messages,
        category="ceo_conversation",
        employee_id=employee_id,
    )
    return _extract_text(result.content)


class ConversationSession:
    """Manages an async CEO<->employee conversation for one task node."""

    def __init__(self, node_id: str, employee_id: str, project_dir: str, broadcast_fn):
        self.node_id = node_id
        self.employee_id = employee_id
        self.project_dir = project_dir
        self._broadcast = broadcast_fn
        self._queue: asyncio.Queue = asyncio.Queue()
        self._conv_dir = Path(project_dir) / "conversations"

    async def send(self, text: str, attachments=None) -> dict:
        """Queue a CEO message for processing."""
        msg = append_message(
            self._conv_dir, self.node_id,
            sender="ceo", text=text, attachments=attachments,
        )
        await self._queue.put(msg)
        return msg

    async def complete(self):
        """Signal that the conversation is done; triggers summary generation."""
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
                "type": "ceo_conversation",
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
