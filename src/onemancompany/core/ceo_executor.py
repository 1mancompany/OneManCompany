"""CeoExecutor — routes CEO-targeted tasks to ConversationService pending queue.

Implements the Launcher protocol (duck-typed). Does not call any LLM.
Pushes the task as an Interaction into the project conversation, then awaits
the Future until CEO replies or EA auto-replies.
"""
from __future__ import annotations

import asyncio
from typing import Callable

from loguru import logger


class CeoExecutor:
    """Virtual executor for CEO (00001) -- implements Launcher protocol (duck-typed).

    Does not call any LLM. Enqueues an Interaction on the project's conversation
    via ConversationService, then waits for the CEO to reply (or EA auto-reply).
    """

    async def execute(
        self,
        task_description: str,
        context: "TaskContext",
        on_log: Callable[[str, str], None] | None = None,
    ) -> "LaunchResult":
        from onemancompany.core.conversation import get_conversation_service, Interaction
        from onemancompany.core.events import CompanyEvent, event_bus
        from onemancompany.core.models import EventType
        from onemancompany.core.config import SYSTEM_AGENT
        from onemancompany.core.vessel import LaunchResult as _LaunchResult

        service = get_conversation_service()
        project_id = context.project_id or "default"

        # Get or create a project conversation
        participants = [context.employee_id] if context.employee_id else []
        conv = await service.get_or_create_project_conversation(project_id, participants)

        # Strip injected [Company Context]...[/Company Context] block from the message
        # shown to CEO -- only show the original task description
        clean_message = task_description
        ctx_start = clean_message.find("[Company Context]")
        if ctx_start >= 0:
            ctx_end = clean_message.find("[/Company Context]")
            if ctx_end >= 0:
                ctx_end += len("[/Company Context]")
                clean_message = (clean_message[:ctx_start] + clean_message[ctx_end:]).strip()
            else:
                clean_message = clean_message[:ctx_start].strip()
        if not clean_message:
            clean_message = "(task description unavailable)"

        # Create interaction with Future
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        interaction = Interaction(
            node_id=context.task_id,
            tree_path="",
            project_id=project_id,
            source_employee=context.employee_id,
            interaction_type="ceo_request",
            message=clean_message,
            future=future,
        )

        # Enqueue on ConversationService (persists message + starts auto-reply timer)
        await service.enqueue_interaction(conv.id, interaction)

        # Broadcast to frontend
        await event_bus.publish(CompanyEvent(
            type=EventType.CEO_SESSION_MESSAGE,
            payload={
                "project_id": project_id,
                "node_id": context.task_id,
                "message": clean_message,
                "source_employee": context.employee_id,
                "interaction_type": "ceo_request",
            },
            agent=SYSTEM_AGENT,
        ))

        if on_log:
            on_log("ceo_request", f"Awaiting CEO reply for: {task_description[:100]}")

        logger.info(
            "[CeoExecutor] Enqueued request for project={} node={} conv={}",
            project_id, context.task_id, conv.id,
        )

        # Block until CEO replies or EA auto-replies
        ceo_response = await future

        return _LaunchResult(output=ceo_response, model_used="ceo")

    def is_ready(self) -> bool:
        return True
