"""Close hooks registry for conversation lifecycle.

Each conversation type can register a close hook via @register_close_hook.
ConversationService.close() calls run_close_hook() to dispatch.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Awaitable, Callable

from loguru import logger

from onemancompany.core.conversation import Conversation, resolve_conv_dir, load_messages
from onemancompany.core.models import ConversationType, EventType

_close_hooks: dict[str, Callable[..., Awaitable[dict | None]]] = {}


def register_close_hook(conv_type: str):
    """Decorator to register a close hook for a conversation type."""
    def decorator(fn):
        _close_hooks[conv_type] = fn
        logger.debug("[conversation] registered close hook: {}", conv_type)
        return fn
    return decorator


def _reset_hooks() -> None:
    """Clear all registered hooks. Only for testing."""
    _close_hooks.clear()


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
# Built-in close hooks
# ---------------------------------------------------------------------------


@register_close_hook(ConversationType.CEO_INBOX)
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


@register_close_hook(ConversationType.ONE_ON_ONE)
async def _close_oneonone(conv: Conversation) -> dict | None:
    """Close hook for 1-on-1 conversations.

    Generates employee reflection on the meeting, updates work principles
    if CEO gave actionable guidance, and saves 1-on-1 summary to guidance notes.
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    from onemancompany.agents.base import make_llm
    from onemancompany.core.llm_utils import llm_invoke_with_retry
    from onemancompany.core import store as _store
    from onemancompany.core.events import event_bus, CompanyEvent
    from onemancompany.core.store import load_employee as _load_emp

    employee_id = conv.employee_id
    logger.info("[conversation] oneonone close hook: conv={}, employee={}", conv.id, employee_id)

    emp_data = _load_emp(employee_id)
    if not emp_data:
        logger.error("[conversation] oneonone close: employee {} not found", employee_id)
        return {"reflection": "", "principles_updated": False, "note_saved": False}

    emp_name = emp_data.get("name", "")
    emp_nickname = emp_data.get("nickname", "")
    emp_role = emp_data.get("role", "")
    emp_dept = emp_data.get("department", "")

    # Load conversation messages from disk
    conv_dir = resolve_conv_dir(conv)
    messages = load_messages(conv_dir)

    if not messages:
        logger.debug("[conversation] oneonone close: no messages, skipping reflection")
        await _store.save_employee_runtime(employee_id, is_listening=False)
        return {"reflection": "", "principles_updated": False, "note_saved": False}

    # Build transcript from conversation messages
    transcript_lines = []
    for msg in messages:
        speaker = "CEO" if msg.role == "ceo" else emp_name
        transcript_lines.append(f"{speaker}: {msg.text}")
    transcript = "\n".join(transcript_lines)

    current_principles = emp_data.get("work_principles", "") or "(No work principles yet)"

    reflection_prompt = (
        f"You are {emp_name} ({emp_nickname}, {emp_role}, Department: {emp_dept}).\n\n"
        f"You just had a 1-on-1 meeting with the CEO. Here is the conversation transcript:\n\n"
        f"{transcript}\n\n"
        f"Your current work principles:\n{current_principles}\n\n"
        f"Do TWO things:\n\n"
        f"1. PRINCIPLES: Did the CEO convey any actionable guidance, directives, or expectations "
        f"that should be incorporated into your work principles?\n"
        f"   If YES — output UPDATED: followed by the complete updated work principles in Markdown.\n"
        f"   If NO — output NO_UPDATE\n\n"
        f"2. SUMMARY: Write a concise 1-1 meeting note (2-4 sentences) summarizing the key "
        f"discussion points, decisions, and any action items from this conversation. "
        f"Include the date. Format: SUMMARY: followed by the note text.\n\n"
        f"Output format (both sections required):\n"
        f"UPDATED: ... or NO_UPDATE\n"
        f"SUMMARY: ..."
    )

    principles_updated = False
    note_saved = False

    try:
        llm = make_llm(employee_id)
        result = await llm_invoke_with_retry(llm, [
            SystemMessage(content="You are an employee reflecting on a meeting with the CEO."),
            HumanMessage(content=reflection_prompt),
        ], category="oneonone", employee_id=employee_id)
        response_text = result.content.strip()
    except Exception as e:
        logger.error("[conversation] oneonone reflection failed for {}: {}", employee_id, e)
        await _store.save_employee_runtime(employee_id, is_listening=False)
        await event_bus.publish(CompanyEvent(
            type=EventType.GUIDANCE_END,
            payload={"employee_id": employee_id, "name": emp_name,
                     "principles_updated": False, "note_saved": False},
            agent="CEO",
        ))
        return {"reflection": "", "principles_updated": False, "note_saved": False}

    # Parse principles update — require markers on own line to reduce false positives
    # Prepend newline so markers at start of response also match
    _resp = "\n" + response_text
    if "\nUPDATED:" in _resp and "\nNO_UPDATE" not in _resp.split("\nSUMMARY:")[0]:
        updated_start = _resp.index("\nUPDATED:") + len("\nUPDATED:")
        summary_start = _resp.find("\nSUMMARY:")
        if summary_start > updated_start:
            new_principles = _resp[updated_start:summary_start].strip()
        else:
            new_principles = _resp[updated_start:].strip()
        if new_principles:
            await _store.save_work_principles(employee_id, new_principles)
            principles_updated = True
            logger.info("[conversation] oneonone: updated work principles for {}", employee_id)

    # Parse and save 1-1 summary as guidance note
    if "\nSUMMARY:" in _resp:
        summary_start = _resp.index("\nSUMMARY:") + len("\nSUMMARY:")
        summary_text = _resp[summary_start:].strip()
        if summary_text:
            date_str = datetime.now().strftime("%Y-%m-%d")
            note = f"**{date_str} 1-1 Meeting**\n{summary_text}"
            existing_notes = _store.load_employee_guidance(employee_id)
            existing_notes.append(note)
            await _store.save_guidance(employee_id, existing_notes)
            note_saved = True
            logger.info("[conversation] oneonone: saved guidance note for {}", employee_id)

    # End the meeting
    await _store.save_employee_runtime(employee_id, is_listening=False)
    await event_bus.publish(CompanyEvent(
        type=EventType.GUIDANCE_END,
        payload={
            "employee_id": employee_id,
            "name": emp_name,
            "principles_updated": principles_updated,
            "note_saved": note_saved,
        },
        agent="CEO",
    ))

    logger.info("[conversation] oneonone close complete: employee={}, principles_updated={}, note_saved={}",
                employee_id, principles_updated, note_saved)
    return {"reflection": response_text, "principles_updated": principles_updated, "note_saved": note_saved}
