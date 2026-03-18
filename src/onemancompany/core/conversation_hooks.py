"""Close hooks registry for conversation lifecycle.

Each conversation type can register a close hook via @register_close_hook.
ConversationService.close() calls run_close_hook() to dispatch.

Current hooks are stubs -- full logic will be ported in Task 11 (Legacy API Rewiring).
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

from loguru import logger

from onemancompany.core.conversation import Conversation

_close_hooks: dict[str, Callable[..., Awaitable[dict | None]]] = {}


def register_close_hook(conv_type: str):
    """Decorator to register a close hook for a conversation type."""
    def decorator(fn):
        _close_hooks[conv_type] = fn
        logger.debug("[conversation] registered close hook: {}", conv_type)
        return fn
    return decorator


async def run_close_hook(conv: Conversation, wait: bool = False) -> dict | None:
    """Run the close hook for a conversation type."""
    hook = _close_hooks.get(conv.type)
    if not hook:
        logger.debug("[conversation] no close hook for type={}", conv.type)
        return None

    logger.debug("[conversation] running close hook: type={}, wait={}", conv.type, wait)
    if wait:
        return await hook(conv)
    else:
        asyncio.create_task(_run_hook_safe(hook, conv))
        return None


async def _run_hook_safe(hook, conv: Conversation) -> None:
    """Wrapper for fire-and-forget hooks — logs exceptions instead of swallowing."""
    try:
        await hook(conv)
    except Exception:
        logger.exception("[conversation] async close hook failed for {}", conv.id)


# ---------------------------------------------------------------------------
# Built-in close hooks (stubs -- full logic ported in Task 11)
# ---------------------------------------------------------------------------


@register_close_hook("ceo_inbox")
async def _close_ceo_inbox(conv: Conversation) -> dict | None:
    """Close hook for CEO inbox conversations.

    TODO: Port full logic from routes.py _run_conversation_loop (lines 5635-5657):
    - Generate conversation summary via LLM
    - Transition node: processing -> completed -> accepted
    - Save tree
    - Trigger dependency resolution
    - Auto-resume held parent tasks
    """
    logger.info("[conversation] ceo_inbox close hook: conv={}", conv.id)
    node_id = conv.metadata.get("node_id")
    logger.debug("[conversation] ceo_inbox closing for node_id={}", node_id)
    return {"summary": "", "node_id": node_id}


@register_close_hook("oneonone")
async def _close_oneonone(conv: Conversation) -> dict | None:
    """Close hook for 1-on-1 conversations.

    TODO: Port full logic from routes.py oneonone_end (lines 929-1054):
    - Generate reflection via LLM
    - Parse UPDATED/NO_UPDATE + SUMMARY sections
    - Save work_principles
    - Save guidance notes
    - Reset is_listening
    - Publish guidance_end event
    """
    logger.info("[conversation] oneonone close hook: conv={}, employee={}", conv.id, conv.employee_id)
    return {"reflection": "", "principles_updated": False}
