"""Tests for CeoBroker data structures and persistence."""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest
import yaml

from onemancompany.core.ceo_broker import CeoInteraction, CeoSession


class TestCeoInteraction:
    def test_creation(self):
        future = asyncio.get_event_loop().create_future()
        interaction = CeoInteraction(
            node_id="abc123",
            tree_path="/tmp/tree.yaml",
            project_id="proj_001/iter_001",
            source_employee="00003",
            interaction_type="ceo_request",
            message="Alex requests deployment approval",
            future=future,
        )
        assert interaction.node_id == "abc123"
        assert interaction.interaction_type == "ceo_request"
        assert interaction.created_at  # auto-filled


class TestCeoSession:
    def test_push_system_message(self):
        session = CeoSession(project_id="proj_001")
        session.push_system_message("Deploy approval needed", source="00003")
        assert len(session.history) == 1
        assert session.history[0]["role"] == "system"
        assert session.history[0]["source"] == "00003"

    def test_push_ceo_message(self):
        session = CeoSession(project_id="proj_001")
        session.push_ceo_message("Approved")
        assert len(session.history) == 1
        assert session.history[0]["role"] == "ceo"

    def test_enqueue_and_has_pending(self):
        session = CeoSession(project_id="proj_001")
        assert session.has_pending is False
        future = asyncio.get_event_loop().create_future()
        interaction = CeoInteraction(
            node_id="abc",
            tree_path="/tmp/t.yaml",
            project_id="proj_001",
            source_employee="00003",
            interaction_type="ceo_request",
            message="Need approval",
            future=future,
        )
        session.enqueue(interaction)
        assert session.has_pending is True
        assert session.pending_count == 1

    def test_save_and_load_history(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            session = CeoSession(project_id="proj_001")
            session.push_system_message("Hello", source="00003")
            session.push_ceo_message("Hi")
            session.save_history(Path(tmpdir))

            session2 = CeoSession(project_id="proj_001")
            session2.load_history(Path(tmpdir))
            assert len(session2.history) == 2
            assert session2.history[0]["role"] == "system"
            assert session2.history[1]["role"] == "ceo"

    def test_fifo_order(self):
        session = CeoSession(project_id="proj_001")
        loop = asyncio.get_event_loop()
        f1 = loop.create_future()
        f2 = loop.create_future()
        i1 = CeoInteraction(
            node_id="first", tree_path="", project_id="proj_001",
            source_employee="00003", interaction_type="ceo_request",
            message="First", future=f1,
        )
        i2 = CeoInteraction(
            node_id="second", tree_path="", project_id="proj_001",
            source_employee="00004", interaction_type="project_confirm",
            message="Second", future=f2,
        )
        session.enqueue(i1)
        session.enqueue(i2)
        popped = session.pop_pending()
        assert popped.node_id == "first"
        assert session.pending_count == 1
