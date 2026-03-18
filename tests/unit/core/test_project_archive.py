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
# _is_iteration
# ---------------------------------------------------------------------------

class TestVersionDetection:
    def test_iteration_id(self):
        assert pa._is_iteration("iter_001") is True
        assert pa._is_iteration("iter_0001") is True

    def test_iteration_rejects_slug(self):
        assert pa._is_iteration("my-project") is False

    def test_iteration_rejects_timestamp(self):
        assert pa._is_iteration("20240101_120000_abcdef") is False



# ---------------------------------------------------------------------------
# create_named_project (v2)
# ---------------------------------------------------------------------------

class TestCreateNamedProject:
    def test_creates_project_dir_and_subdirs(self, tmp_path):
        slug = pa.create_named_project("My App")
        proj_dir = tmp_path / slug
        assert proj_dir.exists()
        assert (proj_dir / "iterations").exists()
        # workspace is now per-iteration, not at project root
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
        # Write a file to iter_001 workspace subdirectory
        doc1 = pa.load_iteration(slug, iter_id1)
        ws1 = Path(doc1["project_dir"]) / "workspace"
        (ws1 / "hello.txt").write_text("hello")
        # Create second iteration — should copy hello.txt into new workspace
        iter_id2 = pa.create_iteration(slug, "task2", "COO")
        doc2 = pa.load_iteration(slug, iter_id2)
        ws2 = Path(doc2["project_dir"]) / "workspace"
        assert (ws2 / "hello.txt").exists()
        assert (ws2 / "hello.txt").read_text() == "hello"

    def test_iteration_copies_legacy_layout_workspace(self, tmp_path):
        """Old iterations stored files directly in iter_dir (no workspace/ subdir).
        create_iteration should still copy those files into the new workspace."""
        slug = pa.create_named_project("LegacyCopy")
        iter_id1 = pa.create_iteration(slug, "task1", "COO")
        doc1 = pa.load_iteration(slug, iter_id1)
        iter_dir1 = Path(doc1["project_dir"])
        # Simulate old layout: remove workspace/, put files directly in iter_dir
        ws1 = iter_dir1 / "workspace"
        if ws1.exists():
            import shutil
            shutil.rmtree(ws1)
        (iter_dir1 / "legacy.txt").write_text("old-data")
        # Create second iteration — should copy legacy.txt
        iter_id2 = pa.create_iteration(slug, "task2", "COO")
        doc2 = pa.load_iteration(slug, iter_id2)
        ws2 = Path(doc2["project_dir"]) / "workspace"
        assert (ws2 / "legacy.txt").exists()
        assert (ws2 / "legacy.txt").read_text() == "old-data"


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

    def test_list_projects_v2_only(self, tmp_path):
        pa.create_named_project("Named")
        projects = pa.list_projects()
        assert len(projects) == 1
        assert projects[0].get("is_named") is True


# ---------------------------------------------------------------------------
# append_action
# ---------------------------------------------------------------------------

class TestAppendAction:
    def test_appends_to_v2_iteration(self, tmp_path):
        slug = pa.create_named_project("V2 Test")
        iter_id = pa.create_iteration(slug, "task", "COO")
        pa.append_action(iter_id, "00005", "review", "Looks good")
        doc = pa.load_iteration(slug, iter_id)
        assert len(doc["timeline"]) == 1

    def test_noop_on_missing_project(self, tmp_path):
        # Should not raise
        pa.append_action("nonexistent-slug", "00005", "test", "")


# ---------------------------------------------------------------------------
# complete_project
# ---------------------------------------------------------------------------

class TestCompleteProject:
    def test_completes_v2_latest_iteration(self, tmp_path):
        slug = pa.create_named_project("Complete V2")
        iter_id = pa.create_iteration(slug, "task", "COO")
        pa.append_action(iter_id, "00005", "work", "did stuff")
        pa.complete_project(slug, output="Done")
        doc = pa.load_iteration(slug, iter_id)
        assert doc["status"] == "completed"

    def test_noop_on_missing_project(self, tmp_path):
        pa.complete_project("nonexistent-slug")  # should not raise


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
        slug = pa.create_named_project("Files test")
        pa.create_iteration(slug, "task", "COO")
        result = pa.save_project_file(slug, "hello.txt", "Hello world")
        assert result["status"] == "ok"
        files = pa.list_project_files(slug)
        assert "hello.txt" in files

    def test_save_bytes_file(self, tmp_path):
        slug = pa.create_named_project("Bytes test")
        pa.create_iteration(slug, "task", "COO")
        result = pa.save_project_file(slug, "data.bin", b"\x00\x01\x02")
        assert result["status"] == "ok"

    def test_save_nested_file(self, tmp_path):
        slug = pa.create_named_project("Nested")
        pa.create_iteration(slug, "task", "COO")
        result = pa.save_project_file(slug, "sub/dir/file.py", "print('hi')")
        assert result["status"] == "ok"
        files = pa.list_project_files(slug)
        assert any("file.py" in f for f in files)

    def test_path_traversal_rejected(self, tmp_path):
        slug = pa.create_named_project("Traversal")
        pa.create_iteration(slug, "task", "COO")
        result = pa.save_project_file(slug, "../../etc/passwd", "evil")
        assert result["status"] == "error"

    def test_list_empty_project(self, tmp_path):
        slug = pa.create_named_project("Empty")
        pa.create_iteration(slug, "task", "COO")
        files = pa.list_project_files(slug)
        # project.yaml is excluded from list
        assert "project.yaml" not in files

    def test_list_files_nonexistent_project(self, tmp_path):
        files = pa.list_project_files("does_not_exist")
        assert files == []

    def test_list_files_excludes_internal_files(self, tmp_path):
        """Internal infrastructure files must not appear in user-facing listing."""
        slug = pa.create_named_project("Filter Test")
        pa.create_iteration(slug, "task", "COO")
        ws = Path(pa.get_project_workspace(slug))

        # Create internal files that should be excluded
        (ws / "project.yaml").write_text("status: active")
        (ws / "task_tree.yaml").write_text("tree: []")
        (ws / "task_tree_iter_001.yaml").write_text("archive: []")
        (ws / "nodes").mkdir(exist_ok=True)
        (ws / "nodes" / "abc.yaml").write_text("node: data")

        # Create user files that should be included
        (ws / "output.txt").write_text("result")
        (ws / "report.md").write_text("# Report")
        (ws / "subdir").mkdir(exist_ok=True)
        (ws / "subdir" / "data.csv").write_text("a,b,c")

        files = pa.list_project_files(slug)

        # User files included
        assert "output.txt" in files
        assert "report.md" in files
        assert str(Path("subdir") / "data.csv") in files

        # Internal files excluded
        assert "project.yaml" not in files
        assert "task_tree.yaml" not in files
        assert "task_tree_iter_001.yaml" not in files
        assert not any("nodes" in f for f in files)

    def test_list_files_excludes_all_task_tree_iter_archives(self, tmp_path):
        """task_tree_iter_NNN.yaml variants must all be excluded."""
        slug = pa.create_named_project("Archive Filter")
        pa.create_iteration(slug, "task", "COO")
        ws = Path(pa.get_project_workspace(slug))

        for name in ["task_tree_iter_001.yaml", "task_tree_iter_999.yaml"]:
            (ws / name).write_text("data")
        # Non-iter task_tree yamls are user files, NOT excluded
        (ws / "task_tree_backup.yaml").write_text("data")
        (ws / "task_tree_notes.txt").write_text("not yaml")

        files = pa.list_project_files(slug)

        # task_tree_iter_*.yaml excluded
        assert not any(f.startswith("task_tree_iter_") and f.endswith(".yaml") for f in files)
        # Non-iter yaml and non-yaml kept
        assert "task_tree_backup.yaml" in files
        assert "task_tree_notes.txt" in files


# ---------------------------------------------------------------------------
# _is_internal_file helper
# ---------------------------------------------------------------------------

class TestIsInternalFile:
    def test_project_yaml(self):
        assert pa._is_internal_file("project.yaml") is True

    def test_task_tree_yaml(self):
        assert pa._is_internal_file("task_tree.yaml") is True

    def test_task_tree_iter_yaml(self):
        assert pa._is_internal_file("task_tree_iter_001.yaml") is True
        assert pa._is_internal_file("task_tree_iter_999.yaml") is True

    def test_task_tree_non_iter_yaml_not_excluded(self):
        """Only task_tree_iter_*.yaml is internal, not arbitrary task_tree_*.yaml."""
        assert pa._is_internal_file("task_tree_backup.yaml") is False
        assert pa._is_internal_file("task_tree_notes.txt") is False

    def test_regular_file_not_excluded(self):
        assert pa._is_internal_file("output.txt") is False
        assert pa._is_internal_file("report.yaml") is False


# ---------------------------------------------------------------------------
# get_project_workspace / get_project_dir
# ---------------------------------------------------------------------------

class TestGetProjectDir:
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

class TestAcceptanceAndCriteria:
    def test_set_acceptance_criteria(self, tmp_path):
        slug = pa.create_named_project("AC Test")
        iter_id = pa.create_iteration(slug, "task", "COO")
        pa.set_acceptance_criteria(slug, ["Test passes", "No bugs"], "00005")
        doc = pa.load_project(slug)
        assert doc["acceptance_criteria"] == ["Test passes", "No bugs"]
        assert doc["responsible_officer"] == "00005"

    def test_set_project_budget(self, tmp_path):
        slug = pa.create_named_project("Budget")
        iter_id = pa.create_iteration(slug, "task", "COO")
        pa.set_project_budget(slug, 1.5)
        doc = pa.load_project(slug)
        assert doc["cost"]["budget_estimate_usd"] == 1.5


class TestRecordProjectCost:
    def test_accumulates_cost(self, tmp_path):
        slug = pa.create_named_project("Cost")
        iter_id = pa.create_iteration(slug, "task", "COO")
        pa.record_project_cost(slug, "00005", "gpt-4", 100, 50, 0.01)
        pa.record_project_cost(slug, "00006", "gpt-4", 200, 100, 0.02)
        doc = pa.load_project(slug)
        cost = doc["cost"]
        assert cost["actual_cost_usd"] == pytest.approx(0.03)
        assert cost["token_usage"]["input"] == 300
        assert cost["token_usage"]["output"] == 150
        assert cost["token_usage"]["total"] == 450
        assert len(cost["breakdown"]) == 2

    def test_noop_on_missing_project(self, tmp_path):
        pa.record_project_cost("nonexistent-slug", "00005", "m", 0, 0, 0.0)


# ---------------------------------------------------------------------------
# _resolve_and_load / _save_resolved bridge
# ---------------------------------------------------------------------------

class TestResolveAndLoad:
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

    def test_nonexistent_slug(self, tmp_path):
        version, doc, key = pa._resolve_and_load("nonexistent-slug")
        assert version == "v2"
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

    def test_skips_non_v2_projects_in_named_list(self, tmp_path):
        """Projects without 'iterations' key excluded from named list."""
        proj_dir = tmp_path / "legacy-proj"
        proj_dir.mkdir()
        _write_yaml(proj_dir / "project.yaml", {"name": "Legacy", "status": "completed"})
        projects = pa.list_named_projects()
        project_ids = [p["project_id"] for p in projects]
        assert "legacy-proj" not in project_ids


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
        """Noop when project not found."""
        pa.set_acceptance_criteria("nonexistent-slug", ["c1"], "00005")

    def test_set_project_budget_missing(self, tmp_path):
        """Noop when project not found."""
        pa.set_project_budget("nonexistent-slug", 1.0)


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
        """Fallback to iteration dir when iteration has no project_dir."""
        slug = pa.create_named_project("Fallback WS")
        iter_id = pa.create_iteration(slug, "task", "COO")
        # Remove project_dir from iteration doc
        doc = pa.load_iteration(slug, iter_id)
        doc.pop("project_dir", None)
        pa._save_iteration(slug, iter_id, doc)

        d = pa.get_project_dir(iter_id)
        assert "iter_001" in d



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
        ws1 = Path(doc1["project_dir"]) / "workspace"
        sub_dir = ws1 / "src" / "components"
        sub_dir.mkdir(parents=True)
        (sub_dir / "main.py").write_text("print('hello')")
        (ws1 / "readme.md").write_text("README")

        # Create second iteration — should copy both files and directories
        iter_id2 = pa.create_iteration(slug, "task2", "COO")
        doc2 = pa.load_iteration(slug, iter_id2)
        ws2 = Path(doc2["project_dir"]) / "workspace"

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
