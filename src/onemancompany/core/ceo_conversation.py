"""CEO Inbox conversation sessions and message persistence.

Each conversation is tied to a task tree node (node_type="ceo_request").
Messages are stored as YAML lists in {project_dir}/conversations/{node_id}.yaml.
"""
from __future__ import annotations

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
