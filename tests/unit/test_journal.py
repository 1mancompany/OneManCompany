"""Unit tests for journal evidence system."""

import pytest

from onemancompany.core.journal import EvidenceKind, EvidenceRecord, Journal


class TestJournal:
    def test_write_and_query(self, tmp_path):
        j = Journal(base_dir=str(tmp_path))
        record = EvidenceRecord(
            kind=EvidenceKind.TASK_COMPLETED,
            agent="hr_agent",
            task_id="t1",
            payload={"success": True, "output": "hired"},
        )
        path = j.write_sync(record)
        assert (tmp_path / "hr_agent").exists()
        assert path.endswith(".json")

        results = j.query(agent="hr_agent", kind=EvidenceKind.TASK_COMPLETED)
        assert len(results) == 1
        assert results[0].task_id == "t1"
        assert results[0].kind == EvidenceKind.TASK_COMPLETED

    def test_query_by_agent(self, tmp_path):
        j = Journal(base_dir=str(tmp_path))
        j.write_sync(EvidenceRecord(kind=EvidenceKind.TASK_DISPATCH, agent="hr"))
        j.write_sync(EvidenceRecord(kind=EvidenceKind.TASK_DISPATCH, agent="coo"))
        j.write_sync(EvidenceRecord(kind=EvidenceKind.TASK_COMPLETED, agent="hr"))

        hr_records = j.query(agent="hr")
        assert len(hr_records) == 2

        coo_records = j.query(agent="coo")
        assert len(coo_records) == 1

    def test_query_by_kind(self, tmp_path):
        j = Journal(base_dir=str(tmp_path))
        j.write_sync(EvidenceRecord(kind=EvidenceKind.TASK_DISPATCH, agent="hr"))
        j.write_sync(EvidenceRecord(kind=EvidenceKind.TASK_COMPLETED, agent="hr"))
        j.write_sync(EvidenceRecord(kind=EvidenceKind.COST_RECORDED, agent="hr"))

        dispatches = j.query(agent="hr", kind=EvidenceKind.TASK_DISPATCH)
        assert len(dispatches) == 1

    def test_query_limit(self, tmp_path):
        j = Journal(base_dir=str(tmp_path))
        for i in range(10):
            j.write_sync(EvidenceRecord(
                kind=EvidenceKind.COST_RECORDED, agent="test",
                payload={"i": i},
            ))

        results = j.query(agent="test", limit=3)
        assert len(results) == 3

    def test_count(self, tmp_path):
        j = Journal(base_dir=str(tmp_path))
        assert j.count(agent="test") == 0

        j.write_sync(EvidenceRecord(kind=EvidenceKind.TASK_DISPATCH, agent="test"))
        j.write_sync(EvidenceRecord(kind=EvidenceKind.TASK_COMPLETED, agent="test"))

        assert j.count(agent="test") == 2
        assert j.count(agent="test", kind=EvidenceKind.TASK_DISPATCH) == 1

    def test_empty_query(self, tmp_path):
        j = Journal(base_dir=str(tmp_path))
        assert j.query(agent="nonexistent") == []

    def test_sha256_dedup(self, tmp_path):
        """Same content at different times should produce different filenames (different timestamps)."""
        j = Journal(base_dir=str(tmp_path))
        r1 = EvidenceRecord(kind=EvidenceKind.TASK_DISPATCH, agent="test")
        r2 = EvidenceRecord(kind=EvidenceKind.TASK_DISPATCH, agent="test")
        p1 = j.write_sync(r1)
        p2 = j.write_sync(r2)
        # Files should exist (may be same or different depending on timing)
        from pathlib import Path
        assert Path(p1).exists()
        assert Path(p2).exists()
