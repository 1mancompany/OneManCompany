"""Tool result size limits — prevents context explosion from large outputs."""

from __future__ import annotations

import uuid
from pathlib import Path

from loguru import logger

# Per-tool result max before persisting to disk (chars)
DEFAULT_MAX_RESULT_SIZE = 30_000  # ~7.5K tokens

# Preview size returned to agent when result is persisted
PREVIEW_SIZE = 2000  # first N chars shown as preview

# Max aggregate tool results per task execution
MAX_RESULTS_PER_TASK = 100_000


def maybe_persist_result(
    result_text: str,
    tool_name: str,
    project_dir: str = "",
    max_size: int = DEFAULT_MAX_RESULT_SIZE,
) -> str:
    """If result exceeds max_size, save to disk and return preview + path.

    Returns the original text if within limit, or a preview message if persisted.
    """
    if len(result_text) <= max_size:
        return result_text

    # Determine save directory
    if project_dir:
        save_dir = Path(project_dir) / "tool_results"
    else:
        from onemancompany.core.config import DATA_ROOT

        save_dir = DATA_ROOT / "tool_results"

    save_dir.mkdir(parents=True, exist_ok=True)

    # Generate unique filename
    result_id = uuid.uuid4().hex[:8]
    filepath = save_dir / f"{tool_name}_{result_id}.txt"

    # Save full content to disk
    filepath.write_text(result_text, encoding="utf-8")

    # Build preview
    preview = result_text[:PREVIEW_SIZE]
    total_lines = result_text.count("\n")

    message = (
        f"Output too large ({len(result_text)} chars). "
        f"Full output saved to: {filepath}\n\n"
        f"Preview (first {PREVIEW_SIZE} chars):\n"
        f"{preview}\n"
        f"...\n"
        f"({total_lines} total lines. Use read(\"{filepath}\") to see full content.)"
    )

    logger.debug(
        "[TOOL_LIMIT] {} result persisted: {} → {} chars",
        tool_name,
        len(result_text),
        len(message),
    )
    return message
