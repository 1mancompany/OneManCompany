"""Tests for runtime_context — lightweight ContextVar-based runtime context."""
from __future__ import annotations

from onemancompany.core.runtime_context import (
    _interaction_type,
    _interaction_work_dir,
    get_interaction_type,
    get_interaction_work_dir,
)


class TestRuntimeContext:
    def test_get_interaction_type_default(self):
        assert get_interaction_type() == ""

    def test_get_interaction_work_dir_default(self):
        assert get_interaction_work_dir() == ""

    def test_get_interaction_type_set(self):
        token = _interaction_type.set("one_on_one")
        try:
            assert get_interaction_type() == "one_on_one"
        finally:
            _interaction_type.reset(token)

    def test_get_interaction_work_dir_set(self):
        token = _interaction_work_dir.set("/tmp/workdir")
        try:
            assert get_interaction_work_dir() == "/tmp/workdir"
        finally:
            _interaction_work_dir.reset(token)
