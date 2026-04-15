"""Unit tests for onemancompany.core.snapshot — 100% coverage."""

import json
import time
from unittest.mock import patch, MagicMock

import pytest

from onemancompany.core.snapshot import (
    _providers,
    snapshot_provider,
    save_snapshot,
    restore_snapshot,
    SNAPSHOT_PATH,
    SNAPSHOT_MAX_AGE_SECONDS,
)


@pytest.fixture(autouse=True)
def clean_providers():
    """Save and restore _providers around each test."""
    saved = dict(_providers)
    _providers.clear()
    yield
    _providers.clear()
    _providers.update(saved)


# ── snapshot_provider decorator ─────────────────────────────

def test_snapshot_provider_registers_class():
    @snapshot_provider("test_mod")
    class _TestSnap:
        @staticmethod
        def save():
            return {"x": 1}

        @staticmethod
        def restore(data):
            pass

    assert "test_mod" in _providers
    assert _providers["test_mod"] is _TestSnap


def test_snapshot_provider_duplicate_warns(caplog):
    @snapshot_provider("dup_mod")
    class _Snap1:
        @staticmethod
        def save():
            return {}
        @staticmethod
        def restore(data):
            pass

    with patch("onemancompany.core.snapshot.logger") as mock_logger:
        @snapshot_provider("dup_mod")
        class _Snap2:
            @staticmethod
            def save():
                return {}
            @staticmethod
            def restore(data):
                pass

        mock_logger.warning.assert_called_once()

    assert _providers["dup_mod"] is _Snap2


# ── save_snapshot ───────────────────────────────────────────

def test_save_snapshot_writes_file(tmp_path):
    @snapshot_provider("s1")
    class _S1:
        @staticmethod
        def save():
            return {"key": "val"}
        @staticmethod
        def restore(data):
            pass

    snapshot_file = tmp_path / ".state_snapshot.json"
    with patch("onemancompany.core.snapshot.SNAPSHOT_PATH", snapshot_file), \
         patch("onemancompany.core.snapshot.write_text_utf") as mock_write:
        save_snapshot()
        mock_write.assert_called_once()
        written = json.loads(mock_write.call_args[0][1])
        assert "s1" in written["providers"]
        assert written["providers"]["s1"] == {"key": "val"}


def test_save_snapshot_skips_empty_providers(tmp_path):
    @snapshot_provider("empty")
    class _Empty:
        @staticmethod
        def save():
            return {}
        @staticmethod
        def restore(data):
            pass

    snapshot_file = tmp_path / ".state_snapshot.json"
    with patch("onemancompany.core.snapshot.SNAPSHOT_PATH", snapshot_file), \
         patch("onemancompany.core.snapshot.write_text_utf") as mock_write:
        save_snapshot()
        written = json.loads(mock_write.call_args[0][1])
        assert "empty" not in written["providers"]


def test_save_snapshot_handles_provider_error(tmp_path):
    @snapshot_provider("bad")
    class _Bad:
        @staticmethod
        def save():
            raise RuntimeError("save boom")
        @staticmethod
        def restore(data):
            pass

    snapshot_file = tmp_path / ".state_snapshot.json"
    with patch("onemancompany.core.snapshot.SNAPSHOT_PATH", snapshot_file), \
         patch("onemancompany.core.snapshot.write_text_utf") as mock_write:
        save_snapshot()  # Should not raise
        written = json.loads(mock_write.call_args[0][1])
        assert "bad" not in written["providers"]


def test_save_snapshot_handles_write_error(tmp_path):
    @snapshot_provider("ok")
    class _Ok:
        @staticmethod
        def save():
            return {"a": 1}
        @staticmethod
        def restore(data):
            pass

    with patch("onemancompany.core.snapshot.SNAPSHOT_PATH", tmp_path / "snap.json"), \
         patch("onemancompany.core.snapshot.write_text_utf", side_effect=OSError("disk full")):
        save_snapshot()  # Should not raise, just log error


# ── restore_snapshot ────────────────────────────────────────

def test_restore_snapshot_no_file():
    fake_path = MagicMock()
    fake_path.exists.return_value = False
    with patch("onemancompany.core.snapshot.SNAPSHOT_PATH", fake_path):
        restore_snapshot()  # Should return early


def test_restore_snapshot_valid(tmp_path):
    restored_data = {}

    @snapshot_provider("r1")
    class _R1:
        @staticmethod
        def save():
            return {}
        @staticmethod
        def restore(data):
            restored_data.update(data)

    snapshot_data = {
        "saved_at": time.time(),
        "providers": {"r1": {"restored": True}},
    }

    fake_path = MagicMock()
    fake_path.exists.return_value = True

    with patch("onemancompany.core.snapshot.SNAPSHOT_PATH", fake_path), \
         patch("onemancompany.core.snapshot.read_text_utf", return_value=json.dumps(snapshot_data)):
        restore_snapshot()

    assert restored_data == {"restored": True}
    fake_path.unlink.assert_called_once_with(missing_ok=True)


def test_restore_snapshot_too_old(tmp_path):
    snapshot_data = {
        "saved_at": time.time() - SNAPSHOT_MAX_AGE_SECONDS - 100,
        "providers": {"r1": {"data": True}},
    }

    fake_path = MagicMock()
    fake_path.exists.return_value = True

    with patch("onemancompany.core.snapshot.SNAPSHOT_PATH", fake_path), \
         patch("onemancompany.core.snapshot.read_text_utf", return_value=json.dumps(snapshot_data)):
        restore_snapshot()

    fake_path.unlink.assert_called_once_with(missing_ok=True)


def test_restore_snapshot_unknown_provider():
    snapshot_data = {
        "saved_at": time.time(),
        "providers": {"unknown_mod": {"x": 1}},
    }

    fake_path = MagicMock()
    fake_path.exists.return_value = True

    with patch("onemancompany.core.snapshot.SNAPSHOT_PATH", fake_path), \
         patch("onemancompany.core.snapshot.read_text_utf", return_value=json.dumps(snapshot_data)):
        restore_snapshot()  # Should warn and skip

    fake_path.unlink.assert_called_once()


def test_restore_snapshot_provider_error():
    @snapshot_provider("fail_restore")
    class _Fail:
        @staticmethod
        def save():
            return {}
        @staticmethod
        def restore(data):
            raise RuntimeError("restore boom")

    snapshot_data = {
        "saved_at": time.time(),
        "providers": {"fail_restore": {"data": 1}},
    }

    fake_path = MagicMock()
    fake_path.exists.return_value = True

    with patch("onemancompany.core.snapshot.SNAPSHOT_PATH", fake_path), \
         patch("onemancompany.core.snapshot.read_text_utf", return_value=json.dumps(snapshot_data)):
        restore_snapshot()  # Should not raise

    fake_path.unlink.assert_called_once()


def test_restore_snapshot_read_error():
    fake_path = MagicMock()
    fake_path.exists.return_value = True

    with patch("onemancompany.core.snapshot.SNAPSHOT_PATH", fake_path), \
         patch("onemancompany.core.snapshot.read_text_utf", side_effect=OSError("read fail")):
        restore_snapshot()  # Should not raise
