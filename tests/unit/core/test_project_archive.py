"""Unit tests for core/project_archive.py — project CRUD and lifecycle."""

from __future__ import annotations

import yaml
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from onemancompany.core import project_archive as pa


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False)


@pytest.fixture(autouse=True)
def _isolate_projects_dir(tmp_path, monkeypatch):
    """Redirect PROJECTS_DIR to a temp directory for every test."""
    monkeypatch.setattr(pa, "PROJECTS_DIR", tmp_path)
    # Clear per-project locks between tests
    pa._project_locks.clear()
    yield


# ---------------------------------------------------------------------------
# _slugify
# ---------------------------------------------------------------------------

class TestSlugify:
    def test_basic(self):
        assert pa._slugify("My Project") == "my-project"

    def test_special_characters(self):
        assert pa._slugify("Hello! World?") == "hello-world"

    def test_underscores_converted(self):
        assert pa._slugify("some_project_name") == "some-project-name"

    def test_empty_string_generates_uuid(self):
        slug = pa._slugify("")
        assert slug.startswith("project-")

    def test_only_special_chars_generates_uuid(self):
        slug = pa._slugify("!@#$%")
        assert slug.startswith("project-")

    def test_strips_leading_trailing_hyphens(self):
        slug = pa._slugify("--hello--")
        assert slug == "hello"


# ---------------------------------------------------------------------------
# _is_v1 / _is_iteration
# ---------------------------------------------------------------------------

class TestVersionDetection:
    def test_v1_timestamp_id(self):
        assert pa._is_v1("20240101_120000_abcdef") is True

    def test_v1_rejects_slug(self):
        assert pa._is_v1("my-project") is False

    def test_iteration_id(self):
        assert pa._is_iteration("iter_001") is True
        assert pa._is_iteration("iter_0001") is True

    def test_iteration_rejects_slug(self):
        assert pa._is_iteration("my-project") is False

    def test_iteration_rejects_v1(self):
        assert pa._is_iteration("20240101_120000_abcdef") is False


# ---------------------------------------------------------------------------
# create_project (v1)
# ---------------------------------------------------------------------------

class TestCreateProjectV1:
    def test_creates_directory_and_yaml(self, tmp_path):
        pid = pa.create_project("Build widget", "COO")
        proj_dir = tmp_path / pid
        assert proj_dir.exists()
        assert (proj_dir / "project.yaml").exists()

    def test_project_yaml_has_required_fields(self, tmp_path):
        pid = pa.create_project("Build widget", "COO", participants=["00005"])
        doc = pa.load_project(pid)
        assert doc is not None
        assert doc["task"] == "Build widget"
        assert doc["routed_to"] == "COO"
        assert doc["status"] == "in_progress"
        assert doc["current_owner"] == "coo"
        assert "00005" in doc["participants"]

    def test_project_has_cost_structure(self, tmp_path):
        pid = pa.create_project("Test", "HR")
        doc = pa.load_project(pid)
        assert "cost" in doc
        assert doc["cost"]["actual_cost_usd"] == 0.0
        assert doc["cost"]["token_usage"]["total"] == 0


# ---------------------------------------------------------------------------
# create_named_project (v2)
# ---------------------------------------------------------------------------

class TestCreateNamedProject:
    def test_creates_project_dir_and_subdirs(self, tmp_path):
        slug = pa.create_named_project("My App")
        proj_dir = tmp_path / slug
        assert proj_dir.exists()
        assert (proj_dir / "workspace").exists()
        assert (proj_dir / "iterations").exists()
        assert (proj_dir / "project.yaml").exists()

    def test_project_yaml_content(self, tmp_path):
        slug = pa.create_named_project("My App")
        doc = pa.load_named_project(slug)
        assert doc is not None
        assert doc["name"] == "My App"
        assert doc["status"] == "active"
        assert doc["iterations"] == []

    def test_duplicate_name_gets_suffix(self, tmp_path):
        slug1 = pa.create_named_project("Test Project")
        slug2 = pa.create_named_project("Test Project")
        assert slug1 != slug2
        assert slug2.startswith(slug1)


# ---------------------------------------------------------------------------
# create_iteration
# ---------------------------------------------------------------------------

class TestCreateIteration:
    def test_creates_iteration_yaml(self, tmp_path):
        slug = pa.create_named_project("Iter Project")
        iter_id = pa.create_iteration(slug, "First task", "COO")
        assert iter_id == "iter_001"
        doc = pa.load_iteration(slug, iter_id)
        assert doc is not None
        assert doc["task"] == "First task"
        assert doc["routed_to"] == "COO"
        assert doc["status"] == "in_progress"

    def test_second_iteration_increments(self, tmp_path):
        slug = pa.create_named_project("Multi Iter")
        pa.create_iteration(slug, "Task 1", "COO")
        iter_id = pa.create_iteration(slug, "Task 2", "HR")
        assert iter_id == "iter_002"

    def test_iteration_updates_project_yaml(self, tmp_path):
        slug = pa.create_named_project("Updated")
        pa.create_iteration(slug, "Task 1", "COO")
        proj = pa.load_named_project(slug)
        assert "iter_001" in proj["iterations"]

    def test_iteration_has_cost_structure(self, tmp_path):
        slug = pa.create_named_project("Cost Test")
        iter_id = pa.create_iteration(slug, "Cost task", "COO")
        doc = pa.load_iteration(slug, iter_id)
        assert doc["cost"]["actual_cost_usd"] == 0.0

    def test_nonexistent_project_raises(self, tmp_path):
        with pytest.raises(ValueError, match="not found"):
            pa.create_iteration("nonexistent", "task", "COO")

    def test_iteration_workspace_created(self, tmp_path):
        slug = pa.create_named_project("WS")
        iter_id = pa.create_iteration(slug, "task", "COO")
        doc = pa.load_iteration(slug, iter_id)
        ws = Path(doc["project_dir"])
        assert ws.exists()

    def test_iteration_copies_previous_workspace(self, tmp_path):
        slug = pa.create_named_project("CopyWS")
        iter_id1 = pa.create_iteration(slug, "task1", "COO")
        # Write a file to iter_001 workspace
        doc1 = pa.load_iteration(slug, iter_id1)
        ws1 = Path(doc1["project_dir"])
        (ws1 / "hello.txt").write_text("hello")
        # Create second iteration — should copy hello.txt
        iter_id2 = pa.create_iteration(slug, "task2", "COO")
        doc2 = pa.load_iteration(slug, iter_id2)
        ws2 = Path(doc2["project_dir"])
        assert (ws2 / "hello.txt").exists()
        assert (ws2 / "hello.txt").read_text() == "hello"


# ---------------------------------------------------------------------------
# load / list helpers
# ---------------------------------------------------------------------------

class TestLoadAndList:
    def test_load_named_project_not_found(self, tmp_path):
        assert pa.load_named_project("nonexistent") is None

    def test_load_iteration_not_found(self, tmp_path):
        slug = pa.create_named_project("Test")
        assert pa.load_iteration(slug, "iter_999") is None

    def test_list_named_projects(self, tmp_path):
        pa.create_named_project("Alpha")
        pa.create_named_project("Beta")
        projects = pa.list_named_projects()
        names = [p["name"] for p in projects]
        assert "Alpha" in names
        assert "Beta" in names

    def test_list_named_projects_empty(self, tmp_path):
        assert pa.list_named_projects() == []

    def test_list_projects_mixed_v1_v2(self, tmp_path):
        pa.create_project("Legacy task", "HR")
        pa.create_named_project("Named")
        projects = pa.list_projects()
        assert len(projects) == 2
        named_projs = [p for p in projects if p.get("is_named")]
        legacy_projs = [p for p in projects if not p.get("is_named")]
        assert len(named_projs) == 1
        assert len(legacy_projs) == 1


# ---------------------------------------------------------------------------
# append_action
# ---------------------------------------------------------------------------

class TestAppendAction:
    def test_appends_to_v1_project(self, tmp_path):
        pid = pa.create_project("Test", "COO")
        pa.append_action(pid, "00005", "code_commit", "Fixed bug")
        doc = pa.load_project(pid)
        assert len(doc["timeline"]) == 1
        assert doc["timeline"][0]["action"] == "code_commit"
        assert doc["current_owner"] == "00005"

    def test_appends_to_v2_iteration(self, tmp_path):
        slug = pa.create_named_project("V2 Test")
        iter_id = pa.create_iteration(slug, "task", "COO")
        pa.append_action(iter_id, "00005", "review", "Looks good")
        doc = pa.load_iteration(slug, iter_id)
        assert len(doc["timeline"]) == 1

    def test_noop_on_missing_project(self, tmp_path):
        # Should not raise
        pa.append_action("nonexistent_20240101_120000_abcdef", "00005", "test", "")


# ---------------------------------------------------------------------------
# complete_project
# ---------------------------------------------------------------------------

class TestCompleteProject:
    def test_completes_v1_project(self, tmp_path):
        pid = pa.create_project("Test", "HR")
        pa.complete_project(pid, output="All done")
        doc = pa.load_project(pid)
        assert doc["status"] == "completed"
        assert doc["output"] == "All done"
        assert doc["completed_at"] is not None

    def test_completes_v2_latest_iteration(self, tmp_path):
        slug = pa.create_named_project("Complete V2")
        iter_id = pa.create_iteration(slug, "task", "COO")
        pa.append_action(iter_id, "00005", "work", "did stuff")
        pa.complete_project(slug, output="Done")
        doc = pa.load_iteration(slug, iter_id)
        assert doc["status"] == "completed"

    def test_noop_on_missing_project(self, tmp_path):
        pa.complete_project("fake_20240101_120000_abcdef")  # should not raise


# ---------------------------------------------------------------------------
# archive_project
# ---------------------------------------------------------------------------

class TestArchiveProject:
    def test_archives_named_project(self, tmp_path):
        slug = pa.create_named_project("Archive Me")
        pa.archive_project(slug)
        doc = pa.load_named_project(slug)
        assert doc["status"] == "archived"
        assert doc["archived_at"] is not None

    def test_noop_on_nonexistent(self, tmp_path):
        pa.archive_project("nonexistent")  # should not raise


# ---------------------------------------------------------------------------
# save_project_file / list_project_files
# ---------------------------------------------------------------------------

class TestProjectFiles:
    def test_save_and_list_text_file(self, tmp_path):
        pid = pa.create_project("Files test", "COO")
        result = pa.save_project_file(pid, "hello.txt", "Hello world")
        assert result["status"] == "ok"
        files = pa.list_project_files(pid)
        assert "hello.txt" in files

    def test_save_bytes_file(self, tmp_path):
        pid = pa.create_project("Bytes test", "COO")
        result = pa.save_project_file(pid, "data.bin", b"\x00\x01\x02")
        assert result["status"] == "ok"

    def test_save_nested_file(self, tmp_path):
        pid = pa.create_project("Nested", "COO")
        result = pa.save_project_file(pid, "sub/dir/file.py", "print('hi')")
        assert result["status"] == "ok"
        files = pa.list_project_files(pid)
        assert any("file.py" in f for f in files)

    def test_path_traversal_rejected(self, tmp_path):
        pid = pa.create_project("Traversal", "COO")
        result = pa.save_project_file(pid, "../../etc/passwd", "evil")
        assert result["status"] == "error"

    def test_list_empty_project(self, tmp_path):
        pid = pa.create_project("Empty", "COO")
        files = pa.list_project_files(pid)
        # project.yaml is excluded from list
        assert "project.yaml" not in files

    def test_list_files_nonexistent_project(self, tmp_path):
        files = pa.list_project_files("does_not_exist_20240101_120000_aaa111")
        assert files == []


# ---------------------------------------------------------------------------
# get_project_workspace / get_project_dir
# ---------------------------------------------------------------------------

class TestGetProjectDir:
    def test_v1_project_dir(self, tmp_path):
        pid = pa.create_project("Dir test", "COO")
        d = pa.get_project_dir(pid)
        assert Path(d).exists()

    def test_v2_named_project_workspace(self, tmp_path):
        slug = pa.create_named_project("WS Test")
        ws = pa.get_project_workspace(slug)
        assert Path(ws).exists()
        # With no iteration, falls back to shared workspace/
        assert "workspace" in ws

    def test_v2_named_project_with_iteration(self, tmp_path):
        slug = pa.create_named_project("WS Iter")
        pa.create_iteration(slug, "task", "COO")
        ws = pa.get_project_workspace(slug)
        assert Path(ws).exists()
        assert "iter_001" in ws


# ---------------------------------------------------------------------------
# Acceptance criteria / dispatch / cost recording
# ---------------------------------------------------------------------------

class TestAcceptanceAndDispatch:
    def test_set_acceptance_criteria(self, tmp_path):
        pid = pa.create_project("AC Test", "COO")
        pa.set_acceptance_criteria(pid, ["Test passes", "No bugs"], "00005")
        doc = pa.load_project(pid)
        assert doc["acceptance_criteria"] == ["Test passes", "No bugs"]
        assert doc["responsible_officer"] == "00005"

    def test_record_dispatch(self, tmp_path):
        pid = pa.create_project("Dispatch", "COO")
        pa.record_dispatch(pid, "00005", "Implement feature")
        doc = pa.load_project(pid)
        assert len(doc["dispatches"]) == 1
        assert doc["dispatches"][0]["employee_id"] == "00005"
        assert doc["dispatches"][0]["status"] == "in_progress"

    def test_record_dispatch_completion(self, tmp_path):
        pid = pa.create_project("DispComp", "COO")
        pa.record_dispatch(pid, "00005", "Task")
        pa.record_dispatch_completion(pid, "00005")
        doc = pa.load_project(pid)
        assert doc["dispatches"][0]["status"] == "completed"
        assert doc["dispatches"][0].get("completed_at") is not None

    def test_all_dispatches_complete_true(self, tmp_path):
        pid = pa.create_project("AllComp", "COO")
        pa.record_dispatch(pid, "00005", "Task")
        pa.record_dispatch_completion(pid, "00005")
        assert pa.all_dispatches_complete(pid) is True

    def test_all_dispatches_complete_false(self, tmp_path):
        pid = pa.create_project("NotComp", "COO")
        pa.record_dispatch(pid, "00005", "Task")
        assert pa.all_dispatches_complete(pid) is False

    def test_all_dispatches_complete_no_dispatches(self, tmp_path):
        pid = pa.create_project("NoDisp", "COO")
        assert pa.all_dispatches_complete(pid) is True

    def test_all_dispatches_complete_missing_project(self, tmp_path):
        assert pa.all_dispatches_complete("fake_20240101_120000_aaa111") is True


class TestAcceptanceAndReview:
    def test_set_acceptance_result(self, tmp_path):
        pid = pa.create_project("Accept", "COO")
        pa.set_acceptance_result(pid, True, "00005", "Looks good")
        doc = pa.load_project(pid)
        assert doc["acceptance_result"]["accepted"] is True
        assert doc["acceptance_result"]["officer_id"] == "00005"

    def test_set_ea_review_result(self, tmp_path):
        pid = pa.create_project("EA Review", "COO")
        pa.set_ea_review_result(pid, False, "Needs rework")
        doc = pa.load_project(pid)
        assert doc["ea_review_result"]["approved"] is False
        assert doc["ea_review_result"]["notes"] == "Needs rework"

    def test_set_project_budget(self, tmp_path):
        pid = pa.create_project("Budget", "COO")
        pa.set_project_budget(pid, 1.5)
        doc = pa.load_project(pid)
        assert doc["cost"]["budget_estimate_usd"] == 1.5


class TestRecordProjectCost:
    def test_accumulates_cost(self, tmp_path):
        pid = pa.create_project("Cost", "COO")
        pa.record_project_cost(pid, "00005", "gpt-4", 100, 50, 0.01)
        pa.record_project_cost(pid, "00006", "gpt-4", 200, 100, 0.02)
        doc = pa.load_project(pid)
        cost = doc["cost"]
        assert cost["actual_cost_usd"] == pytest.approx(0.03)
        assert cost["token_usage"]["input"] == 300
        assert cost["token_usage"]["output"] == 150
        assert cost["token_usage"]["total"] == 450
        assert len(cost["breakdown"]) == 2

    def test_noop_on_missing_project(self, tmp_path):
        pa.record_project_cost("fake_20240101_120000_aaa111", "00005", "m", 0, 0, 0.0)


# ---------------------------------------------------------------------------
# _resolve_and_load / _save_resolved bridge
# ---------------------------------------------------------------------------

class TestResolveAndLoad:
    def test_v1_project(self, tmp_path):
        pid = pa.create_project("V1", "COO")
        version, doc, key = pa._resolve_and_load(pid)
        assert version == "v1"
        assert doc is not None
        assert key == pid

    def test_v2_slug(self, tmp_path):
        slug = pa.create_named_project("V2 Resolve")
        pa.create_iteration(slug, "task", "COO")
        version, doc, key = pa._resolve_and_load(slug)
        assert version == "v2"
        assert doc is not None
        assert slug in key

    def test_v2_iteration_id(self, tmp_path):
        slug = pa.create_named_project("Iter Resolve")
        iter_id = pa.create_iteration(slug, "task", "COO")
        version, doc, key = pa._resolve_and_load(iter_id)
        assert version == "v2"
        assert doc is not None
        assert iter_id in key

    def test_auto_prefix(self, tmp_path):
        # _auto_ prefix is treated as v1
        version, doc, key = pa._resolve_and_load("_auto_test")
        assert version == "v1"
        assert doc is None

    def test_nonexistent_slug(self, tmp_path):
        version, doc, key = pa._resolve_and_load("nonexistent-slug")
        # Falls through to v1 fallback
        assert doc is None


# ---------------------------------------------------------------------------
# _find_project_for_iteration
# ---------------------------------------------------------------------------

class TestFindProjectForIteration:
    def test_finds_correct_project(self, tmp_path):
        slug = pa.create_named_project("Find Test")
        pa.create_iteration(slug, "task", "COO")
        found = pa._find_project_for_iteration("iter_001")
        assert found == slug

    def test_returns_none_for_unknown(self, tmp_path):
        assert pa._find_project_for_iteration("iter_999") is None


# ---------------------------------------------------------------------------
# get_cost_summary
# ---------------------------------------------------------------------------

class TestGetCostSummary:
    def test_empty_summary(self, tmp_path):
        with patch("onemancompany.core.state.company_state") as mock_state:
            mock_state.employees = {}
            mock_state.ex_employees = {}
            summary = pa.get_cost_summary()
            assert summary["total"]["cost_usd"] == 0.0
            assert summary["total"]["total_tokens"] == 0
            assert summary["by_department"] == {}
            assert summary["recent_projects"] == []

    def test_v1_project_cost_aggregated(self, tmp_path):
        pid = pa.create_project("Cost Agg", "COO")
        pa.record_project_cost(pid, "00005", "gpt-4", 100, 50, 0.01)

        mock_emp = MagicMock()
        mock_emp.department = "Engineering"

        with patch("onemancompany.core.state.company_state") as mock_state:
            mock_state.employees = {"00005": mock_emp}
            mock_state.ex_employees = {}
            summary = pa.get_cost_summary()
            assert summary["total"]["cost_usd"] == pytest.approx(0.01)
            assert "Engineering" in summary["by_department"]


# ---------------------------------------------------------------------------
# _get_project_lock
# ---------------------------------------------------------------------------

class TestProjectLock:
    def test_returns_same_lock_for_same_id(self):
        pa._project_locks.clear()
        lock1 = pa._get_project_lock("test-id")
        lock2 = pa._get_project_lock("test-id")
        assert lock1 is lock2

    def test_returns_different_locks_for_different_ids(self):
        pa._project_locks.clear()
        lock1 = pa._get_project_lock("id-a")
        lock2 = pa._get_project_lock("id-b")
        assert lock1 is not lock2


# ---------------------------------------------------------------------------
# list_named_projects edge cases
# ---------------------------------------------------------------------------

class TestListNamedProjectsEdgeCases:
    def test_skips_non_directory_entries(self, tmp_path):
        """Line 302: non-directory entries are skipped."""
        (tmp_path / "readme.txt").write_text("not a project")
        projects = pa.list_named_projects()
        assert projects == []

    def test_skips_directory_without_project_yaml(self, tmp_path):
        """Line 305: no project.yaml — continue."""
        (tmp_path / "orphan_dir").mkdir()
        projects = pa.list_named_projects()
        assert projects == []

    def test_skips_invalid_yaml(self, tmp_path):
        """Line 309-310: invalid YAML in project.yaml — continue."""
        proj_dir = tmp_path / "bad-project"
        proj_dir.mkdir()
        (proj_dir / "project.yaml").write_text(": bad: yaml: {{}")
        projects = pa.list_named_projects()
        assert projects == []

    def test_skips_v1_projects_in_named_list(self, tmp_path):
        """Line 313: v1 project (no 'iterations' key) excluded from named list."""
        pid = pa.create_project("Legacy", "COO")
        projects = pa.list_named_projects()
        # The v1 project should not appear
        project_ids = [p["project_id"] for p in projects]
        assert pid not in project_ids


# ---------------------------------------------------------------------------
# load_named_project edge cases
# ---------------------------------------------------------------------------

class TestLoadNamedProjectEdgeCases:
    def test_returns_none_for_v1_project(self, tmp_path):
        """Line 292: project without 'iterations' key returns None."""
        proj_dir = tmp_path / "legacy-proj"
        proj_dir.mkdir()
        _write_yaml(proj_dir / "project.yaml", {"name": "Legacy", "status": "completed"})
        result = pa.load_named_project("legacy-proj")
        assert result is None


# ---------------------------------------------------------------------------
# _resolve_and_load edge cases
# ---------------------------------------------------------------------------

class TestResolveAndLoadEdgeCases:
    def test_v2_slug_without_iterations(self, tmp_path):
        """Line 120: v2 project with no iterations returns project itself."""
        slug = pa.create_named_project("No Iters")
        version, doc, key = pa._resolve_and_load(slug)
        assert version == "v2"
        assert doc is not None
        assert key == slug

    def test_iteration_id_orphaned(self, tmp_path):
        """Line 110: iteration not owned by any project returns None."""
        version, doc, key = pa._resolve_and_load("iter_999")
        assert version == "v2"
        assert doc is None
        assert key == ""


# ---------------------------------------------------------------------------
# _save_resolved edge cases
# ---------------------------------------------------------------------------

class TestSaveResolved:
    def test_v2_save_without_slash(self, tmp_path):
        """Line 131: resolved_key without '/' — save doesn't crash but also doesn't save iteration."""
        slug = pa.create_named_project("Test")
        doc = pa.load_named_project(slug)
        # Save with key that has no "/" — should not crash
        pa._save_resolved("v2", slug, doc)


# ---------------------------------------------------------------------------
# list_projects edge cases
# ---------------------------------------------------------------------------

class TestListProjectsEdgeCases:
    def test_skips_non_dir_in_list(self, tmp_path):
        """Line 520: non-directory entries skipped."""
        (tmp_path / "not_a_dir.txt").write_text("text")
        projects = pa.list_projects()
        assert all(isinstance(p, dict) for p in projects)

    def test_skips_dir_without_yaml(self, tmp_path):
        """Line 523: directory without project.yaml skipped."""
        (tmp_path / "empty_dir").mkdir()
        projects = pa.list_projects()
        names = [p.get("project_id") for p in projects]
        assert "empty_dir" not in names

    def test_skips_bad_yaml_in_list(self, tmp_path):
        """Lines 527-528: bad YAML in project.yaml — continue."""
        proj_dir = tmp_path / "bad-proj"
        proj_dir.mkdir()
        (proj_dir / "project.yaml").write_text(": bad: yaml: {{}")
        projects = pa.list_projects()
        names = [p.get("project_id") for p in projects]
        assert "bad-proj" not in names

    def test_v2_project_with_iterations_in_list(self, tmp_path):
        """Lines 538-547: v2 project with iterations aggregates cost."""
        slug = pa.create_named_project("V2 List")
        iter_id = pa.create_iteration(slug, "task", "COO")
        pa.record_project_cost(slug, "00005", "gpt-4", 100, 50, 0.01)

        projects = pa.list_projects()
        named = [p for p in projects if p.get("is_named")]
        assert len(named) >= 1
        v2_proj = [p for p in named if p["name"] == "V2 List"][0]
        assert v2_proj["iteration_count"] >= 1

    def test_v1_project_in_list(self, tmp_path):
        """Line 588: v1 project listed correctly."""
        pid = pa.create_project("V1 List", "HR")
        pa.record_project_cost(pid, "00005", "gpt-4", 50, 25, 0.005)

        projects = pa.list_projects()
        v1_projs = [p for p in projects if not p.get("is_named")]
        assert len(v1_projs) >= 1
        found = [p for p in v1_projs if p["project_id"] == pid]
        assert len(found) == 1
        assert found[0]["cost_usd"] == pytest.approx(0.005)


# ---------------------------------------------------------------------------
# get_cost_summary with v2 projects
# ---------------------------------------------------------------------------

class TestGetCostSummaryV2:
    def test_v2_project_cost_aggregated(self, tmp_path):
        """Lines 733-764: v2 project cost aggregation in get_cost_summary."""
        slug = pa.create_named_project("Cost V2")
        iter_id = pa.create_iteration(slug, "cost task", "COO")
        pa.record_project_cost(slug, "00005", "gpt-4", 100, 50, 0.01)

        mock_emp = MagicMock()
        mock_emp.department = "Engineering"

        with patch("onemancompany.core.state.company_state") as mock_state:
            mock_state.employees = {"00005": mock_emp}
            mock_state.ex_employees = {}
            summary = pa.get_cost_summary()
            assert summary["total"]["cost_usd"] >= 0.01
            assert len(summary["recent_projects"]) >= 1

    def test_cost_summary_skips_bad_entries(self, tmp_path):
        """Lines 721-729: non-dirs, missing yamls, bad yamls skipped."""
        (tmp_path / "readme.txt").write_text("not a project")
        (tmp_path / "no_yaml_dir").mkdir()
        bad_dir = tmp_path / "bad_yaml"
        bad_dir.mkdir()
        (bad_dir / "project.yaml").write_text(": bad")

        with patch("onemancompany.core.state.company_state") as mock_state:
            mock_state.employees = {}
            mock_state.ex_employees = {}
            summary = pa.get_cost_summary()
            assert summary["total"]["cost_usd"] == 0.0


# ---------------------------------------------------------------------------
# various noop-on-missing paths
# ---------------------------------------------------------------------------

class TestNoopOnMissing:
    def test_set_acceptance_criteria_missing(self, tmp_path):
        """Line 588: noop when project not found."""
        pa.set_acceptance_criteria("nonexistent_20240101_120000_aaa111", ["c1"], "00005")

    def test_record_dispatch_missing(self, tmp_path):
        """Line 598: noop when project not found."""
        pa.record_dispatch("nonexistent_20240101_120000_aaa111", "00005", "task")

    def test_record_dispatch_completion_missing(self, tmp_path):
        """Line 614: noop when project not found."""
        pa.record_dispatch_completion("nonexistent_20240101_120000_aaa111", "00005")

    def test_set_acceptance_result_missing(self, tmp_path):
        """Line 638: noop when project not found."""
        pa.set_acceptance_result("nonexistent_20240101_120000_aaa111", True, "00005")

    def test_set_ea_review_missing(self, tmp_path):
        """Line 652: noop when project not found."""
        pa.set_ea_review_result("nonexistent_20240101_120000_aaa111", True)

    def test_set_project_budget_missing(self, tmp_path):
        """Line 665: noop when project not found."""
        pa.set_project_budget("nonexistent_20240101_120000_aaa111", 1.0)


# ---------------------------------------------------------------------------
# _resolve_workspace
# ---------------------------------------------------------------------------

class TestResolveWorkspace:
    def test_iteration_workspace(self, tmp_path):
        """Lines 451-461: iteration ID resolves to iteration workspace."""
        slug = pa.create_named_project("WS Test")
        iter_id = pa.create_iteration(slug, "task", "COO")
        d = pa.get_project_dir(iter_id)
        assert "iter_001" in d

    def test_iteration_without_project_dir(self, tmp_path):
        """Fallback to shared workspace when iteration has no project_dir."""
        slug = pa.create_named_project("Fallback WS")
        iter_id = pa.create_iteration(slug, "task", "COO")
        # Remove project_dir from iteration doc
        doc = pa.load_iteration(slug, iter_id)
        doc.pop("project_dir", None)
        pa._save_iteration(slug, iter_id, doc)

        d = pa.get_project_dir(iter_id)
        assert "workspace" in d

    def test_auto_prefix_project_dir(self, tmp_path):
        """_auto_ prefix treated as v1."""
        d = pa.get_project_dir("_auto_test")
        assert "_auto_test" in d


# ---------------------------------------------------------------------------
# archive_project
# ---------------------------------------------------------------------------

class TestArchiveProjectEdgeCases:
    def test_archive_nonexistent_noop(self, tmp_path):
        pa.archive_project("totally_nonexistent")  # should not raise

    def test_archive_sets_fields(self, tmp_path):
        slug = pa.create_named_project("Archive Fields")
        pa.archive_project(slug)
        doc = pa.load_named_project(slug)
        assert doc["status"] == "archived"
        assert doc["archived_at"] is not None


# ---------------------------------------------------------------------------
# _find_project_for_iteration — non-directory entry (line 84)
# ---------------------------------------------------------------------------

class TestFindProjectForIterationNonDir:
    def test_non_directory_skipped(self, tmp_path, monkeypatch):
        """Line 84: non-directory entry in PROJECTS_DIR is skipped."""
        monkeypatch.setattr(pa, "PROJECTS_DIR", tmp_path)
        # Create a file (not a directory) in PROJECTS_DIR
        (tmp_path / "readme.txt").write_text("not a project")
        # Create a valid project directory with a matching iteration
        proj_dir = tmp_path / "my-project"
        proj_dir.mkdir()
        iter_dir = proj_dir / "iterations"
        iter_dir.mkdir()
        (iter_dir / "iter_001.yaml").write_text("task: test")

        # Should find the project, skipping the non-dir file entry
        found = pa._find_project_for_iteration("iter_001")
        assert found == "my-project"


# ---------------------------------------------------------------------------
# create_iteration — copytree for directory content (line 220)
# ---------------------------------------------------------------------------

class TestCreateIterationCopytree:
    def test_copies_directories_from_previous_workspace(self, tmp_path):
        """Line 220: shutil.copytree path when prev_workspace has directories."""
        slug = pa.create_named_project("DirCopy")
        iter_id1 = pa.create_iteration(slug, "task1", "COO")

        # Add a directory with files to the first iteration workspace
        doc1 = pa.load_iteration(slug, iter_id1)
        ws1 = Path(doc1["project_dir"])
        sub_dir = ws1 / "src" / "components"
        sub_dir.mkdir(parents=True)
        (sub_dir / "main.py").write_text("print('hello')")
        (ws1 / "readme.md").write_text("README")

        # Create second iteration — should copy both files and directories
        iter_id2 = pa.create_iteration(slug, "task2", "COO")
        doc2 = pa.load_iteration(slug, iter_id2)
        ws2 = Path(doc2["project_dir"])

        assert (ws2 / "src" / "components" / "main.py").exists()
        assert (ws2 / "src" / "components" / "main.py").read_text() == "print('hello')"
        assert (ws2 / "readme.md").exists()


# ---------------------------------------------------------------------------
# list_project_files — nonexistent project (line 506)
# ---------------------------------------------------------------------------

class TestListProjectFilesNonexistent:
    def test_nonexistent_project_returns_empty(self, tmp_path, monkeypatch):
        """Line 506: list_project_files returns [] when project_dir doesn't exist."""
        # Patch _resolve_workspace to return a path that doesn't exist
        fake_path = tmp_path / "does_not_exist"
        monkeypatch.setattr(pa, "_resolve_workspace", lambda pid: fake_path)
        files = pa.list_project_files("anything")
        assert files == []


# ---------------------------------------------------------------------------
# list_all_projects / get_cost_summary — iteration cost aggregation (line 736)
# ---------------------------------------------------------------------------

class TestCostSummaryIterationSkip:
    def test_missing_iteration_skipped_in_cost_aggregation(self, tmp_path):
        """Line 736: load_iteration returns None for missing iteration — continue."""
        slug = pa.create_named_project("Cost Skip")
        pa.create_iteration(slug, "task1", "COO")
        pa.record_project_cost(slug, "00005", "gpt-4", 100, 50, 0.01)

        # Manually add a fake iteration ID that doesn't exist on disk
        proj_yaml = tmp_path / slug / "project.yaml"
        proj_doc = pa.load_named_project(slug)
        proj_doc["iterations"].append("iter_999")  # nonexistent
        _write_yaml(proj_yaml, proj_doc)

        mock_emp = MagicMock()
        mock_emp.department = "Engineering"

        with patch("onemancompany.core.state.company_state") as mock_state:
            mock_state.employees = {"00005": mock_emp}
            mock_state.ex_employees = {}
            summary = pa.get_cost_summary()
            # Should still aggregate cost from iter_001, skipping iter_999
            assert summary["total"]["cost_usd"] >= 0.01
