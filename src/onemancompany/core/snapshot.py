"""Snapshot harness — registry-based ephemeral state persistence.

Each module declares its own serializable state via the ``@snapshot_provider``
decorator.  The snapshot system collects all providers on save and dispatches
back on restore, so main.py never needs to know the details of individual
modules' internal state.

Usage in any module::

    from onemancompany.core.snapshot import snapshot_provider

    @snapshot_provider("my_module")
    class _MySnapshot:
        @staticmethod
        def save() -> dict:
            return {"items": list(_my_items)}

        @staticmethod
        def restore(data: dict) -> None:
            _my_items.update(data.get("items", []))

That's it.  No need to touch main.py.
"""

from __future__ import annotations

import json
import time
from typing import Protocol

from loguru import logger


class SnapshotProvider(Protocol):
    """Protocol for modules that want to persist ephemeral state."""

    @staticmethod
    def save() -> dict:
        """Return JSON-serializable dict of state to persist."""
        ...

    @staticmethod
    def restore(data: dict) -> None:
        """Restore state from a previously saved dict."""
        ...


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_providers: dict[str, SnapshotProvider] = {}


def snapshot_provider(name: str):
    """Decorator to register a snapshot provider class.

    Args:
        name: Unique key under which this provider's data is stored.
              Must be stable across restarts (it's a dict key in the JSON).
    """
    def decorator(cls):
        if name in _providers:
            logger.warning("Snapshot provider '{}' registered twice, overwriting", name)
        _providers[name] = cls
        return cls
    return decorator


# ---------------------------------------------------------------------------
# Save / Restore
# ---------------------------------------------------------------------------

from onemancompany.core.config import COMPANY_DIR as _COMPANY_DIR
SNAPSHOT_PATH = _COMPANY_DIR / ".state_snapshot.json"
SNAPSHOT_MAX_AGE_SECONDS = 300  # 5 minutes — graceful restarts may take time


def save_snapshot() -> None:
    """Collect state from all registered providers and write to disk."""
    snapshot: dict = {"saved_at": time.time(), "providers": {}}

    for name, provider in _providers.items():
        try:
            data = provider.save()
            if data:  # skip empty providers
                snapshot["providers"][name] = data
        except Exception as e:
            logger.error("Snapshot save failed for '{}': {}", name, e)

    try:
        SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT_PATH.write_text(json.dumps(snapshot, default=str), encoding="utf-8")
        provider_names = [n for n in snapshot["providers"]]
        logger.info("Saved snapshot: {} provider(s) [{}]", len(provider_names), ", ".join(provider_names))
    except Exception as e:
        logger.error("Failed to write snapshot file: {}", e)


def restore_snapshot() -> None:
    """Read snapshot from disk and dispatch to registered providers."""
    if not SNAPSHOT_PATH.exists():
        return
    try:
        raw = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
        saved_at = raw.get("saved_at", 0)
        age = time.time() - saved_at
        if age > SNAPSHOT_MAX_AGE_SECONDS:
            SNAPSHOT_PATH.unlink(missing_ok=True)
            logger.info("Snapshot too old ({:.0f}s), discarded", age)
            return

        providers_data = raw.get("providers", {})
        restored = []
        for name, data in providers_data.items():
            provider = _providers.get(name)
            if not provider:
                logger.warning("No provider registered for snapshot key '{}', skipping", name)
                continue
            try:
                provider.restore(data)
                restored.append(name)
            except Exception as e:
                logger.error("Snapshot restore failed for '{}': {}", name, e)

        # Clean up after successful restore
        SNAPSHOT_PATH.unlink(missing_ok=True)
        logger.info("Restored snapshot ({:.1f}s old): {} provider(s) [{}]",
                     age, len(restored), ", ".join(restored))

    except Exception as e:
        logger.error("Failed to read snapshot file: {}", e)
