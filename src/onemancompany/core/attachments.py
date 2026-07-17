"""Shared helpers for rendering attachment instructions into LLM prompts.

The CEO submits attachments through multiple paths (task creation, 1-on-1
chat, EA / project conversations). All of them need to tell the agent to
read each attached file before responding. The renderer is centralized
here so prompt wording and quoting rules stay consistent.
"""

from __future__ import annotations

import json as _json
from pathlib import Path


def build_attachment_prompt(attachments: list[dict]) -> str:
    """Describe attachments with both display and read-safe path formats.

    The plain path in ``(saved at ...)`` is for human readability. The
    ``read("...")`` argument and attachment display name use JSON quoting so
    backslashes, quotes, and newlines stay safe/parsable without inventing
    custom escaping rules.
    """
    if not attachments:
        return ""

    lines: list[str] = []
    for attachment in attachments:
        raw_filename = attachment.get("filename", "file")
        quoted_filename = _json.dumps("file" if raw_filename is None else str(raw_filename))
        raw_path = attachment.get("path", "")
        path = "" if raw_path is None else str(raw_path).strip()
        if path:
            lines.append(f"- Attachment: {quoted_filename} (saved at {path}) [read({_json.dumps(path)})]")
        else:
            lines.append(f"- Attachment: {quoted_filename}")

    return (
        "\n\nCEO attached the following files:\n"
        + "\n".join(lines)
        + "\nRead each attachment with read() before responding so you can inspect the actual file contents."
    )


def build_attachment_prompt_from_paths(paths: list[str]) -> str:
    """Same as :func:`build_attachment_prompt`, but accepts bare path strings.

    Used by the conversation flow where ``Message.attachments`` stores just
    saved-file paths. The display filename is derived from the basename.
    """
    if not paths:
        return ""
    attachments = [{"filename": Path(p).name, "path": p} for p in paths if p]
    return build_attachment_prompt(attachments)
