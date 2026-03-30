"""CeoBroker — unified CEO conversation model.

Each project has a CeoSession with independent conversation history
and a FIFO queue of pending interactions (requests awaiting CEO reply).
CeoExecutor pushes interactions; CEO replies resolve them.
"""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Callable

import yaml
from loguru import logger

from onemancompany.core.config import ENCODING_UTF8

if TYPE_CHECKING:
    from onemancompany.core.vessel import LaunchResult, TaskContext

CEO_SESSION_FILENAME = "ceo_session.yaml"

# Default timeout for EA auto-reply (seconds)
CEO_AUTO_REPLY_TIMEOUT = 120


@dataclass
class CeoInteraction:
    """A single pending interaction awaiting CEO reply."""

    node_id: str
    tree_path: str
    project_id: str
    source_employee: str
    interaction_type: str  # "ceo_request" | "project_confirm"
    message: str
    future: asyncio.Future
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now().isoformat()


class CeoSession:
    """Per-project CEO conversation session."""

    def __init__(self, project_id: str) -> None:
        self.project_id = project_id
        self.project_dir: Path | None = None
        self.history: list[dict] = []
        self._pending: deque[CeoInteraction] = deque()
        self._auto_reply_tasks: dict[str, asyncio.Task] = {}  # node_id → timer task
        self.auto_reply_enabled: bool = True
        self.auto_reply_timeout: int = CEO_AUTO_REPLY_TIMEOUT

    @property
    def has_pending(self) -> bool:
        return len(self._pending) > 0

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    def enqueue(self, interaction: CeoInteraction) -> None:
        """Add interaction to FIFO queue and record in history."""
        self._pending.append(interaction)
        self.push_system_message(interaction.message, source=interaction.source_employee)
        logger.debug(
            "CeoSession[{}] enqueued interaction node_id={} type={}",
            self.project_id,
            interaction.node_id,
            interaction.interaction_type,
        )
        # Start auto-reply timer
        if self.auto_reply_enabled:
            self._start_auto_reply_timer(interaction)

    def _start_auto_reply_timer(self, interaction: CeoInteraction) -> None:
        """Start a background timer that auto-replies if CEO doesn't respond."""
        async def _timer() -> None:
            try:
                await asyncio.sleep(self.auto_reply_timeout)
                # Check if this interaction is still pending (CEO might have replied)
                if interaction in self._pending:
                    reply = await _ea_auto_reply(interaction.node_id, interaction.message)
                    # Re-check after async call — CEO may have replied while EA was thinking
                    if interaction in self._pending:
                        self._pending.remove(interaction)
                        self.push_system_message(reply, source="ea_auto_reply")
                        if not interaction.future.done():
                            interaction.future.set_result(reply)
                        logger.info(
                            "[CeoSession] EA auto-replied for node={} in project={}",
                            interaction.node_id, self.project_id,
                        )
                        if self.project_dir:
                            self.save_history(self.project_dir)
            except asyncio.CancelledError:
                logger.debug("[CeoSession] Auto-reply timer cancelled for node={}", interaction.node_id)
            except Exception as e:
                logger.error("[CeoSession] Auto-reply error for node={}: {}", interaction.node_id, e)
            finally:
                self._auto_reply_tasks.pop(interaction.node_id, None)

        try:
            task = asyncio.create_task(_timer())
        except RuntimeError:
            # No running event loop (e.g. in tests) — skip auto-reply timer
            logger.debug("[CeoSession] No event loop, skipping auto-reply timer for node={}", interaction.node_id)
            return
        self._auto_reply_tasks[interaction.node_id] = task

    def pop_pending(self) -> CeoInteraction | None:
        """Pop the oldest pending interaction (FIFO)."""
        if self._pending:
            interaction = self._pending.popleft()
            # Cancel auto-reply timer since CEO responded
            timer = self._auto_reply_tasks.pop(interaction.node_id, None)
            if timer and not timer.done():
                timer.cancel()
            return interaction
        return None

    def cancel_all_timers(self) -> None:
        """Cancel all pending auto-reply timers."""
        for task in self._auto_reply_tasks.values():
            if not task.done():
                task.cancel()
        self._auto_reply_tasks.clear()

    def push_system_message(self, text: str, source: str = "") -> dict:
        """Append a system message (from an employee) to history."""
        msg = {
            "role": "system",
            "text": text,
            "source": source,
            "timestamp": datetime.now().isoformat(),
        }
        self.history.append(msg)
        return msg

    def push_ceo_message(self, text: str) -> dict:
        """Append a CEO reply to history."""
        msg = {
            "role": "ceo",
            "text": text,
            "timestamp": datetime.now().isoformat(),
        }
        self.history.append(msg)
        return msg

    def save_history(self, project_dir: Path) -> None:
        """Persist conversation history to YAML."""
        path = project_dir / CEO_SESSION_FILENAME
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.dump({"history": self.history}, allow_unicode=True, sort_keys=False),
            encoding=ENCODING_UTF8,
        )
        logger.debug("CeoSession[{}] saved {} messages to {}", self.project_id, len(self.history), path)

    def load_history(self, project_dir: Path) -> None:
        """Load conversation history from YAML."""
        path = project_dir / CEO_SESSION_FILENAME
        if path.exists():
            data = yaml.safe_load(path.read_text(encoding=ENCODING_UTF8)) or {}
            self.history = data.get("history", [])
            logger.debug("CeoSession[{}] loaded {} messages from {}", self.project_id, len(self.history), path)

    def to_summary(self) -> dict:
        """Return a serializable summary for API responses."""
        return {
            "project_id": self.project_id,
            "has_pending": self.has_pending,
            "pending_count": self.pending_count,
            "message_count": len(self.history),
            "last_message": self.history[-1] if self.history else None,
        }


async def _ea_auto_reply(node_id: str, description: str) -> str:
    """EA reads the request description and decides accept/reject on behalf of CEO."""
    import json
    import re

    from onemancompany.agents.base import _extract_text, make_llm, tracked_ainvoke
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

    try:
        resp = await asyncio.wait_for(
            tracked_ainvoke(llm, prompt, category="ea_auto_reply", employee_id=EA_ID),
            timeout=60,
        )
    except asyncio.TimeoutError:
        logger.warning("[ea_auto_reply] LLM call timed out for node={}, defaulting to accept", node_id)
        return "[EA Auto-Reply] Decision: ACCEPT\nAuto-approved (EA LLM call timed out)"
    raw = _extract_text(resp.content)

    decision = "accept"
    reason = "EA auto-approved (no valid response parsed)"
    try:
        json_match = re.search(r"\{.*\}", raw, re.DOTALL)
        if json_match:
            parsed = json.loads(json_match.group())
            decision = parsed.get("decision", "accept")
            reason = parsed.get("reason", "")
    except (json.JSONDecodeError, AttributeError) as exc:
        logger.debug("[ea_auto_reply] failed to parse EA response: {}", exc)

    reply_text = f"[EA Auto-Reply] Decision: {decision.upper()}\n{reason}"
    logger.info("[ea_auto_reply] node={} decision={} reason={}", node_id, decision, reason)
    return reply_text


class CeoBroker:
    """Central manager for all CEO per-project sessions."""

    def __init__(self) -> None:
        self._sessions: dict[str, CeoSession] = {}

    @staticmethod
    def _base_project_id(project_id: str) -> str:
        """Extract base project ID, stripping iteration suffix.

        e.g. "abc123_name_date/iter_001" → "abc123_name_date"
        """
        return project_id.split("/")[0] if "/" in project_id else project_id

    def get_or_create_session(self, project_id: str) -> CeoSession:
        key = self._base_project_id(project_id)
        if key not in self._sessions:
            self._sessions[key] = CeoSession(project_id=key)
        return self._sessions[key]

    def get_session(self, project_id: str) -> CeoSession | None:
        return self._sessions.get(self._base_project_id(project_id))

    def list_sessions(self) -> list[dict]:
        summaries = [s.to_summary() for s in self._sessions.values()]
        summaries.sort(key=lambda s: (not s["has_pending"], s["project_id"]))
        return summaries

    def recover(self, projects_dir: Path) -> None:
        """Rebuild sessions from disk on restart.

        Loads conversation history from ceo_session.yaml files.
        Pending Futures are recreated by schedule_node -> CeoExecutor.execute().
        """
        from onemancompany.core.config import TASK_TREE_FILENAME

        if not projects_dir.exists():
            return

        for tree_path in projects_dir.rglob(TASK_TREE_FILENAME):
            try:
                # Lazy import to avoid circular deps
                from onemancompany.core.task_tree import get_tree
                tree = get_tree(tree_path)
            except Exception as exc:
                logger.warning("[CeoBroker] Skipping corrupt tree {}: {}", tree_path, exc)
                continue

            project_id = tree.project_id
            project_dir = tree_path.parent

            # Load session history if it exists
            history_path = project_dir / CEO_SESSION_FILENAME
            session = self.get_or_create_session(project_id)
            session.project_dir = project_dir
            if history_path.exists():
                session.load_history(project_dir)
                logger.debug("[CeoBroker] Recovered session for project={} ({} messages)",
                             project_id, len(session.history))

    def _resolve_project_dir(self, project_id: str) -> Path | None:
        """Try to find the project directory from tree files."""
        from onemancompany.core.config import PROJECTS_DIR, TASK_TREE_FILENAME

        # project_id format: "shortid_name_date/iter_001"
        parts = project_id.split("/")
        if len(parts) >= 2:
            base = parts[0]
            iter_name = parts[1]
            for proj_dir in PROJECTS_DIR.glob(f"{base}*/iterations/{iter_name}"):
                if (proj_dir / TASK_TREE_FILENAME).exists():
                    return proj_dir
        # Fallback: try base project dir directly
        if parts:
            for proj_dir in PROJECTS_DIR.glob(f"{parts[0]}*"):
                if proj_dir.is_dir() and (proj_dir / TASK_TREE_FILENAME).exists():
                    return proj_dir
        return None

    async def handle_input(self, project_id: str, text: str) -> dict:
        from onemancompany.core.events import CompanyEvent, event_bus
        from onemancompany.core.models import EventType
        from onemancompany.core.config import SYSTEM_AGENT

        session = self.get_or_create_session(project_id)
        if session.project_dir is None:
            session.project_dir = self._resolve_project_dir(project_id)
        if session.has_pending:
            interaction = session.pop_pending()
            session.push_ceo_message(text)
            if not interaction.future.done():
                interaction.future.set_result(text)
            logger.info(
                "[CeoBroker] Resolved pending {} for project={} node={}",
                interaction.interaction_type, project_id, interaction.node_id,
            )
            # Persist history to disk
            if session.project_dir:
                session.save_history(session.project_dir)
            # Broadcast to frontend so TUI updates in real-time
            await event_bus.publish(CompanyEvent(
                type=EventType.CEO_SESSION_MESSAGE,
                payload={
                    "project_id": project_id,
                    "node_id": interaction.node_id,
                    "role": "ceo",
                    "text": text,
                },
                agent=SYSTEM_AGENT,
            ))
            return {"type": "resolved", "node_id": interaction.node_id}
        else:
            session.push_ceo_message(text)
            # Persist history to disk
            if session.project_dir:
                session.save_history(session.project_dir)
            # Broadcast to frontend
            await event_bus.publish(CompanyEvent(
                type=EventType.CEO_SESSION_MESSAGE,
                payload={
                    "project_id": project_id,
                    "role": "ceo",
                    "text": text,
                },
                agent=SYSTEM_AGENT,
            ))
            logger.info("[CeoBroker] No pending for project={} — followup", project_id)
            return {"type": "followup", "text": text}


class CeoExecutor:
    """Virtual executor for CEO (00001) — implements Launcher protocol (duck-typed).

    Does not call any LLM. Pushes the task as a message into the project's
    CeoSession, then waits for the CEO to reply in the TUI.
    """

    async def execute(
        self,
        task_description: str,
        context: TaskContext,
        on_log: Callable[[str, str], None] | None = None,
    ) -> LaunchResult:
        from onemancompany.core.events import CompanyEvent, event_bus
        from onemancompany.core.models import EventType
        from onemancompany.core.config import SYSTEM_AGENT
        from onemancompany.core.vessel import LaunchResult as _LaunchResult

        broker = get_ceo_broker()
        project_id = context.project_id or "default"
        session = broker.get_or_create_session(project_id)
        if context.work_dir:
            session.project_dir = Path(context.work_dir)

        future = asyncio.get_running_loop().create_future()
        interaction = CeoInteraction(
            node_id=context.task_id,
            tree_path="",
            project_id=project_id,
            source_employee=context.employee_id,
            interaction_type="ceo_request",
            message=task_description,
            future=future,
        )
        session.enqueue(interaction)

        # Broadcast to frontend
        await event_bus.publish(CompanyEvent(
            type=EventType.CEO_SESSION_MESSAGE,
            payload={
                "project_id": project_id,
                "node_id": context.task_id,
                "message": task_description,
                "source_employee": context.employee_id,
                "interaction_type": "ceo_request",
            },
            agent=SYSTEM_AGENT,
        ))

        if on_log:
            on_log("ceo_request", f"Awaiting CEO reply for: {task_description[:100]}")

        logger.info("[CeoExecutor] Enqueued request for project={} node={}", project_id, context.task_id)

        ceo_response = await future

        if context.work_dir:
            session.save_history(Path(context.work_dir))

        return _LaunchResult(output=ceo_response, model_used="ceo")

    def is_ready(self) -> bool:
        return True


# Singleton
_broker: CeoBroker | None = None


def get_ceo_broker() -> CeoBroker:
    global _broker
    if _broker is None:
        _broker = CeoBroker()
    return _broker
