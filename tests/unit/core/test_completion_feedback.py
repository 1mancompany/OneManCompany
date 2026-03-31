"""Unit tests for project completion feedback — CEO confirmation message quality."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from onemancompany.core.vessel import (
    _collect_work_results,
    _list_deliverables,
    _result_preview,
    _summarize_project_for_ceo,
)


class TestResultPreview:
    def test_single_line(self):
        assert _result_preview("hello world") == "hello world"

    def test_multi_line_truncates_to_3(self):
        text = "line1\nline2\nline3\nline4\nline5"
        result = _result_preview(text)
        assert "line1" in result
        assert "line2" in result
        assert "line3" in result
        assert "line4" not in result

    def test_respects_max_chars(self):
        text = "a" * 1000
        result = _result_preview(text, max_chars=100)
        assert len(result) <= 100

    def test_empty_string(self):
        assert _result_preview("") == ""

    def test_whitespace_only(self):
        assert _result_preview("   \n  \n  ") == ""


class TestListDeliverables:
    def test_lists_files_excludes_system(self, tmp_path):
        (tmp_path / "proposal.md").write_text("hello")
        (tmp_path / "task_tree.yaml").write_text("tree")
        (tmp_path / "llm_traces.jsonl").write_text("traces")
        (tmp_path / "nodes").mkdir()
        (tmp_path / ".DS_Store").write_text("ds")

        result = _list_deliverables(str(tmp_path))
        assert result == ["proposal.md"]

    def test_empty_dir(self, tmp_path):
        assert _list_deliverables(str(tmp_path)) == []

    def test_nonexistent_dir(self):
        assert _list_deliverables("/nonexistent/path") == []

    def test_multiple_files_sorted(self, tmp_path):
        (tmp_path / "report.pdf").write_text("r")
        (tmp_path / "analysis.md").write_text("a")
        (tmp_path / "budget.xlsx").write_text("b")

        result = _list_deliverables(str(tmp_path))
        assert result == ["analysis.md", "budget.xlsx", "report.pdf"]

    def test_excludes_iteration_yaml(self, tmp_path):
        (tmp_path / "iteration.yaml").write_text("iter")
        (tmp_path / "deliverable.md").write_text("d")

        result = _list_deliverables(str(tmp_path))
        assert result == ["deliverable.md"]


class TestCollectWorkResults:
    def test_collects_completed_task_nodes(self):
        """Verify _collect_work_results returns completed task nodes, not system nodes."""
        from unittest.mock import MagicMock
        from onemancompany.core.task_lifecycle import NodeType, TaskPhase

        node1 = MagicMock()
        node1.node_type = NodeType.TASK
        node1.status = TaskPhase.FINISHED.value
        node1.is_ceo_node = False
        node1.result = "did some work"
        node1.employee_id = "00005"

        node2 = MagicMock()
        node2.node_type = NodeType.WATCHDOG_NUDGE
        node2.status = TaskPhase.FINISHED.value
        node2.is_ceo_node = False
        node2.result = "nudge"

        node3 = MagicMock()
        node3.node_type = NodeType.CEO_REQUEST
        node3.status = TaskPhase.FINISHED.value
        node3.is_ceo_node = True
        node3.result = "confirmed"

        node4 = MagicMock()
        node4.node_type = NodeType.TASK
        node4.status = TaskPhase.FAILED.value
        node4.is_ceo_node = False
        node4.result = "error happened"
        node4.employee_id = "00010"

        tree = MagicMock()
        tree.all_nodes.return_value = [node1, node2, node3, node4]

        results = _collect_work_results(tree, "/tmp/proj")
        # Should include node1 (finished task) and node4 (failed task with result)
        # Should exclude node2 (system) and node3 (ceo node)
        assert len(results) == 2
        assert node1 in results
        assert node4 in results


class TestSummarizeProjectForCeo:
    def _make_work_node(self, employee_id, title, result, status="finished"):
        node = MagicMock()
        node.employee_id = employee_id
        node.title = title
        node.description_preview = title
        node.result = result
        node.status = status
        return node

    def test_ea_summary_returned_on_success(self):
        mock_response = MagicMock()
        mock_response.content = "项目已完成，提案已修订。"
        nodes = [self._make_work_node("00005", "Write proposal", "Done writing")]

        with patch("onemancompany.core.vessel.make_llm"), \
             patch("onemancompany.agents.base.tracked_ainvoke", new_callable=AsyncMock, return_value=mock_response):
            result = asyncio.get_event_loop().run_until_complete(
                _summarize_project_for_ceo("Test Project", nodes, ["proposal.md"])
            )
        assert result == "项目已完成，提案已修订。"

    def test_fallback_on_llm_failure(self):
        nodes = [self._make_work_node("00005", "Write proposal", "Done writing")]

        with patch("onemancompany.core.vessel.make_llm"), \
             patch("onemancompany.agents.base.tracked_ainvoke", new_callable=AsyncMock, side_effect=Exception("LLM down")):
            result = asyncio.get_event_loop().run_until_complete(
                _summarize_project_for_ceo("Test Project", nodes, [])
            )
        assert "Work summary:" in result
        assert "00005" in result
        assert "Write proposal" in result

    def test_empty_work_nodes_returns_empty(self):
        result = asyncio.get_event_loop().run_until_complete(
            _summarize_project_for_ceo("Test Project", [], [])
        )
        assert result == ""
