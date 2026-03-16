"""Unit tests for core/journal.py — Journal evidence chain."""

from __future__ import annotations

import pytest

from onemancompany.core.journal import EvidenceKind, EvidenceRecord, Journal


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_record(agent: str = "HR", kind: EvidenceKind = EvidenceKind.TASK_DISPATCH) -> EvidenceRecord:
    return EvidenceRecord(kind=kind, agent=agent, payload={"key": "value"})


# ---------------------------------------------------------------------------
# Journal.write_sync and query basics
# ---------------------------------------------------------------------------

class TestJournalBasics:
    def test_write_and_query(self, tmp_path):
        j = Journal(str(tmp_path / "journal"))
        rec = _make_record()
        j.write_sync(rec)

        results = j.query(agent="HR")
        assert len(results) == 1
        assert results[0].kind == EvidenceKind.TASK_DISPATCH

    def test_query_empty_dir(self, tmp_path):
        j = Journal(str(tmp_path / "journal"))
        assert j.query(agent="HR") == []

    def test_count(self, tmp_path):
        j = Journal(str(tmp_path / "journal"))
        j.write_sync(_make_record())
        j.write_sync(_make_record())
        assert j.count(agent="HR") == 2

    def test_query_with_kind_filter(self, tmp_path):
        j = Journal(str(tmp_path / "journal"))
        j.write_sync(_make_record(kind=EvidenceKind.TASK_DISPATCH))
        j.write_sync(_make_record(kind=EvidenceKind.COST_RECORDED))

        results = j.query(agent="HR", kind=EvidenceKind.COST_RECORDED)
        assert len(results) == 1
        assert results[0].kind == EvidenceKind.COST_RECORDED


# ---------------------------------------------------------------------------
# Journal.query — corrupted record (lines 84-85)
# ---------------------------------------------------------------------------

class TestJournalCorruptedRecord:
    def test_corrupted_json_skipped_in_query(self, tmp_path):
        """Lines 84-85: corrupted JSON file is skipped (continue) during query."""
        j = Journal(str(tmp_path / "journal"))

        # Write a valid record first
        j.write_sync(_make_record())

        # Write a corrupted JSON file in the same directory
        agent_dir = tmp_path / "journal" / "HR"
        corrupted_file = agent_dir / "task_dispatch-9999999999-corrupted.json"
        corrupted_file.write_text("{invalid json content!!!", encoding="utf-8")

        # Query should skip the corrupted file and return only valid records
        results = j.query(agent="HR")
        assert len(results) == 1
        assert results[0].kind == EvidenceKind.TASK_DISPATCH

    def test_all_corrupted_returns_empty(self, tmp_path):
        """Lines 84-85: when all records are corrupted, query returns empty list."""
        j = Journal(str(tmp_path / "journal"))

        agent_dir = tmp_path / "journal" / "HR"
        agent_dir.mkdir(parents=True)
        (agent_dir / "task_dispatch-9999999999-aaaaaa.json").write_text("not json", encoding="utf-8")
        (agent_dir / "task_dispatch-9999999998-bbbbbb.json").write_text("{}", encoding="utf-8")

        results = j.query(agent="HR")
        # "{}" is valid JSON but won't validate as EvidenceRecord (missing fields)
        # so both should be skipped
        assert len(results) == 0
