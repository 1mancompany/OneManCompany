"""Tests for meeting minutes archive and query."""
from __future__ import annotations


def test_archive_meeting(tmp_path, monkeypatch):
    import onemancompany.core.meeting_minutes as mm

    monkeypatch.setattr(mm, "MINUTES_DIR", tmp_path)
    minute_id = mm.archive_meeting(
        room_id="all_hands_room",
        topic="Kickoff",
        project_id="abc123_test_0324120000",
        participants=["00003", "00006"],
        messages=[{"speaker": "COO", "message": "Hello", "time": "12:00:00"}],
        conclusion="Agreed on plan",
    )
    assert minute_id
    doc = mm.load_minute(minute_id)
    assert doc["room_id"] == "all_hands_room"
    assert doc["project_id"] == "abc123_test_0324120000"
    assert len(doc["messages"]) == 1
    assert doc["conclusion"] == "Agreed on plan"


def test_query_by_project(tmp_path, monkeypatch):
    import onemancompany.core.meeting_minutes as mm

    monkeypatch.setattr(mm, "MINUTES_DIR", tmp_path)
    mm.archive_meeting(
        room_id="room1",
        topic="A",
        project_id="proj1",
        participants=["00003"],
        messages=[],
        conclusion="",
    )
    mm.archive_meeting(
        room_id="room2",
        topic="B",
        project_id="proj2",
        participants=["00003"],
        messages=[],
        conclusion="",
    )
    results = mm.query_minutes(project_id="proj1")
    assert len(results) == 1
    assert results[0]["project_id"] == "proj1"


def test_query_by_employee(tmp_path, monkeypatch):
    import onemancompany.core.meeting_minutes as mm

    monkeypatch.setattr(mm, "MINUTES_DIR", tmp_path)
    mm.archive_meeting(
        room_id="room1",
        topic="A",
        project_id="",
        participants=["00003", "00006"],
        messages=[],
        conclusion="",
    )
    mm.archive_meeting(
        room_id="room2",
        topic="B",
        project_id="",
        participants=["00008"],
        messages=[],
        conclusion="",
    )
    results = mm.query_minutes(employee_id="00006")
    assert len(results) == 1
    assert "00006" in results[0]["participants"]


def test_query_empty_dir(tmp_path, monkeypatch):
    import onemancompany.core.meeting_minutes as mm

    # Point to a non-existent directory
    monkeypatch.setattr(mm, "MINUTES_DIR", tmp_path / "nonexistent")
    results = mm.query_minutes()
    assert results == []


def test_load_minute_missing(tmp_path, monkeypatch):
    import onemancompany.core.meeting_minutes as mm

    monkeypatch.setattr(mm, "MINUTES_DIR", tmp_path)
    doc = mm.load_minute("does_not_exist")
    assert doc == {}


def test_query_returns_summary_without_messages(tmp_path, monkeypatch):
    import onemancompany.core.meeting_minutes as mm

    monkeypatch.setattr(mm, "MINUTES_DIR", tmp_path)
    mm.archive_meeting(
        room_id="room1",
        topic="Test",
        project_id="",
        participants=["00003"],
        messages=[{"speaker": "A", "message": "hi", "time": "10:00:00"}],
        conclusion="Done",
    )
    results = mm.query_minutes()
    assert len(results) == 1
    assert "messages" not in results[0]
    assert results[0]["message_count"] == 1


def test_query_filters_by_room_id(tmp_path, monkeypatch):
    """Line 72: room_id filter skips non-matching meetings."""
    import onemancompany.core.meeting_minutes as mm
    import yaml
    monkeypatch.setattr(mm, "MINUTES_DIR", tmp_path)
    doc1 = {"room_id": "room_A", "project_id": "", "participants": ["00003"], "messages": []}
    doc2 = {"room_id": "room_B", "project_id": "", "participants": ["00003"], "messages": []}
    (tmp_path / "meeting_a.yaml").write_text(yaml.dump(doc1))
    (tmp_path / "meeting_b.yaml").write_text(yaml.dump(doc2))
    results = mm.query_minutes(room_id="room_A")
    assert len(results) == 1
    assert results[0]["room_id"] == "room_A"


def test_query_filters_by_project_id(tmp_path, monkeypatch):
    """Line 72-73: project_id filter skips non-matching meetings."""
    import onemancompany.core.meeting_minutes as mm
    import yaml
    monkeypatch.setattr(mm, "MINUTES_DIR", tmp_path)
    # Write two meeting files directly to avoid timestamp collision
    doc1 = {"room_id": "room1", "project_id": "proj_1", "participants": ["00003"], "messages": []}
    doc2 = {"room_id": "room1", "project_id": "proj_2", "participants": ["00003"], "messages": []}
    (tmp_path / "meeting_a.yaml").write_text(yaml.dump(doc1))
    (tmp_path / "meeting_b.yaml").write_text(yaml.dump(doc2))
    results = mm.query_minutes(project_id="proj_1")
    assert len(results) == 1
    assert results[0]["project_id"] == "proj_1"


def test_query_respects_limit(tmp_path, monkeypatch):
    """Line 80-81: limit cuts off results."""
    import onemancompany.core.meeting_minutes as mm
    import yaml
    monkeypatch.setattr(mm, "MINUTES_DIR", tmp_path)
    for i in range(5):
        doc = {"room_id": "room1", "project_id": "", "participants": ["00003"], "messages": []}
        (tmp_path / f"meeting_{i}.yaml").write_text(yaml.dump(doc))
    results = mm.query_minutes(limit=2)
    assert len(results) == 2
