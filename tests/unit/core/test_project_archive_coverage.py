"""Coverage tests for core/project_archive.py — missing lines."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml


# ---------------------------------------------------------------------------
# _rebase_project_dir (lines 102-116)
# ---------------------------------------------------------------------------

class TestRebaseProjectPath:
    def test_rebase_from_foreign_path(self, monkeypatch):
        import onemancompany.core.project_archive as pa_mod
        fake_projects = Path("/tmp/test_projects")
        monkeypatch.setattr(pa_mod, "PROJECTS_DIR", fake_projects)

        result = pa_mod._rebase_project_dir(
            "/remote/data/company/business/projects/my-proj/tree.yaml"
        )
        assert result == fake_projects / "my-proj" / "tree.yaml"

    def test_rebase_already_local(self, tmp_path, monkeypatch):
        import onemancompany.core.project_archive as pa_mod
        monkeypatch.setattr(pa_mod, "PROJECTS_DIR", tmp_path)

        local_path = tmp_path / "my-proj" / "tree.yaml"
        result = pa_mod._rebase_project_dir(str(local_path))
        assert result == local_path

    def test_rebase_no_marker(self, monkeypatch):
        import onemancompany.core.project_archive as pa_mod
        monkeypatch.setattr(pa_mod, "PROJECTS_DIR", Path("/tmp/test"))
        result = pa_mod._rebase_project_dir("/some/random/path/file.yaml")
        assert result == Path("/some/random/path/file.yaml")


# ---------------------------------------------------------------------------
# _slugify (line 126-127)
# ---------------------------------------------------------------------------

class TestSlugify:
    def test_basic(self):
        from onemancompany.core.project_archive import _slugify
        assert _slugify("Hello World") == "hello-world"

    def test_empty_after_sanitize(self):
        from onemancompany.core.project_archive import _slugify
        result = _slugify("!!!")
        assert result.startswith("project-")

    def test_long_name_truncated(self):
        from onemancompany.core.project_archive import _slugify
        result = _slugify("a" * 100, max_len=10)
        assert len(result) <= 10


# ---------------------------------------------------------------------------
# _is_iteration / _split_qualified_iter (lines 136-137, 148-150)
# ---------------------------------------------------------------------------

class TestIterationHelpers:
    def test_is_iteration_qualified(self):
        from onemancompany.core.project_archive import _is_iteration
        assert _is_iteration("slug/iter_002") is True

    def test_is_iteration_bare(self):
        from onemancompany.core.project_archive import _is_iteration
        assert _is_iteration("iter_002") is True

    def test_is_iteration_false(self):
        from onemancompany.core.project_archive import _is_iteration
        assert _is_iteration("project-name") is False

    def test_split_qualified(self):
        from onemancompany.core.project_archive import _split_qualified_iter
        slug, iter_id = _split_qualified_iter("my-proj/iter_003")
        assert slug == "my-proj"
        assert iter_id == "iter_003"

    def test_split_bare(self):
        from onemancompany.core.project_archive import _split_qualified_iter
        slug, iter_id = _split_qualified_iter("iter_001")
        assert slug == ""
        assert iter_id == "iter_001"


# ---------------------------------------------------------------------------
# _find_project_for_iteration (lines 164-169)
# ---------------------------------------------------------------------------

class TestFindProjectForIteration:
    def test_qualified_slug_returns_slug(self, tmp_path, monkeypatch):
        import onemancompany.core.project_archive as pa_mod
        monkeypatch.setattr(pa_mod, "PROJECTS_DIR", tmp_path)
        # Even if file doesn't exist, qualified slug returns the slug
        result = pa_mod._find_project_for_iteration("my-proj/iter_001")
        assert result == "my-proj"


# ---------------------------------------------------------------------------
# _auto_project_name (lines 224-228)
# ---------------------------------------------------------------------------

class TestAutoProjectName:
    def test_short_description(self):
        from onemancompany.core.project_archive import _auto_project_name
        assert _auto_project_name("Build login page") == "Build login page"

    def test_long_description_truncated(self):
        from onemancompany.core.project_archive import _auto_project_name
        long = "This is a very long description that exceeds fifty characters and needs truncation"
        result = _auto_project_name(long)
        assert len(result) <= 50

    def test_empty_description(self):
        from onemancompany.core.project_archive import _auto_project_name
        assert _auto_project_name("   ") == "Untitled Project"


# ---------------------------------------------------------------------------
# _llm_project_name (lines 233-257)
# ---------------------------------------------------------------------------

class TestLlmProjectName:
    @pytest.mark.asyncio
    async def test_llm_success(self):
        from onemancompany.core.project_archive import _llm_project_name

        mock_resp = MagicMock()
        mock_resp.content = "Login Page Redesign"

        with patch("onemancompany.agents.base.make_llm"), \
             patch("onemancompany.agents.base.tracked_ainvoke",
                   new_callable=AsyncMock, return_value=mock_resp):
            result = await _llm_project_name("Redesign the login page")
        assert result == "Login Page Redesign"

    @pytest.mark.asyncio
    async def test_llm_failure_falls_back(self):
        from onemancompany.core.project_archive import _llm_project_name

        with patch("onemancompany.agents.base.make_llm",
                   side_effect=RuntimeError("no key")):
            result = await _llm_project_name("Build a game")
        assert result == "Build a game"


# ---------------------------------------------------------------------------
# async_create_project_from_task (lines 275-298)
# ---------------------------------------------------------------------------

class TestAsyncCreateProjectFromTask:
    @pytest.mark.asyncio
    async def test_creates_project(self, tmp_path, monkeypatch):
        import onemancompany.core.project_archive as pa_mod
        monkeypatch.setattr(pa_mod, "PROJECTS_DIR", tmp_path)

        with patch.object(pa_mod, "create_named_project", return_value="proj123"), \
             patch.object(pa_mod, "create_iteration", return_value="iter_001"), \
             patch("onemancompany.core.async_utils.spawn_background"):
            pid, iid = await pa_mod.async_create_project_from_task("Build auth module")
        assert pid == "proj123"
        assert iid == "iter_001"


# ---------------------------------------------------------------------------
# update_project_name (lines 303-312)
# ---------------------------------------------------------------------------

class TestUpdateProjectName:
    def test_update_name(self, tmp_path, monkeypatch):
        import onemancompany.core.project_archive as pa_mod
        monkeypatch.setattr(pa_mod, "PROJECTS_DIR", tmp_path)

        proj_dir = tmp_path / "proj1"
        proj_dir.mkdir()
        with open(proj_dir / "project.yaml", "w") as f:
            yaml.dump({"name": "Old Name", "iterations": []}, f)

        pa_mod.update_project_name("proj1", "New Name")
        with open(proj_dir / "project.yaml") as f:
            doc = yaml.safe_load(f)
        assert doc["name"] == "New Name"

    def test_update_name_nonexistent(self, tmp_path, monkeypatch):
        import onemancompany.core.project_archive as pa_mod
        monkeypatch.setattr(pa_mod, "PROJECTS_DIR", tmp_path)
        # Should not raise
        pa_mod.update_project_name("nonexistent", "New Name")


# ---------------------------------------------------------------------------
# create_project_from_task (lines 322-325)
# ---------------------------------------------------------------------------

class TestCreateProjectFromTask:
    def test_sync_create(self, tmp_path, monkeypatch):
        import onemancompany.core.project_archive as pa_mod
        monkeypatch.setattr(pa_mod, "PROJECTS_DIR", tmp_path)

        with patch.object(pa_mod, "create_named_project", return_value="proj1"), \
             patch.object(pa_mod, "create_iteration", return_value="iter_001"):
            pid, iid = pa_mod.create_project_from_task("Build feature")
        assert pid == "proj1"
        assert iid == "iter_001"


# ---------------------------------------------------------------------------
# create_named_project — collision (lines 334-335)
# ---------------------------------------------------------------------------

class TestCreateNamedProject:
    def test_uuid_collision(self, tmp_path, monkeypatch):
        import onemancompany.core.project_archive as pa_mod
        monkeypatch.setattr(pa_mod, "PROJECTS_DIR", tmp_path)

        # Pre-create a dir that would collide
        call_count = [0]
        real_uuid4 = __import__("uuid").uuid4

        def mock_uuid4():
            call_count[0] += 1
            if call_count[0] == 1:
                # Return a fixed uuid that will collide
                class FakeUUID:
                    hex = "aaaaaaaaaaaa"
                return FakeUUID()
            return real_uuid4()

        (tmp_path / "aaaaaaaaaaaa").mkdir()
        with patch("onemancompany.core.project_archive.uuid.uuid4", side_effect=mock_uuid4):
            pid = pa_mod.create_named_project("Test Project")
        assert (tmp_path / pid).exists()


# ---------------------------------------------------------------------------
# update_project_status / load_project (lines 572-578)
# ---------------------------------------------------------------------------

class TestUpdateProjectStatus:
    def test_update_status_nonexistent(self, tmp_path, monkeypatch):
        import onemancompany.core.project_archive as pa_mod
        monkeypatch.setattr(pa_mod, "PROJECTS_DIR", tmp_path)
        # Should not raise
        pa_mod.update_project_status("nonexistent", "completed")


# ---------------------------------------------------------------------------
# _list_files_rg / _list_files_walk (lines 708-741)
# ---------------------------------------------------------------------------

class TestListFiles:
    def test_list_files_walk(self, tmp_path, monkeypatch):
        import onemancompany.core.project_archive as pa_mod
        monkeypatch.setattr(pa_mod, "PROJECTS_DIR", tmp_path)

        proj_dir = tmp_path / "proj1"
        proj_dir.mkdir()
        (proj_dir / "main.py").write_text("print('hi')")
        (proj_dir / "utils.py").write_text("# utils")

        result = pa_mod._list_files_walk(proj_dir, limit=100, project_id="proj1")
        assert "main.py" in result
        assert "utils.py" in result

    def test_list_files_walk_cap(self, tmp_path, monkeypatch):
        import onemancompany.core.project_archive as pa_mod
        monkeypatch.setattr(pa_mod, "PROJECTS_DIR", tmp_path)

        proj_dir = tmp_path / "proj1"
        proj_dir.mkdir()
        for i in range(10):
            (proj_dir / f"file{i}.py").write_text(f"# {i}")

        result = pa_mod._list_files_walk(proj_dir, limit=3, project_id="proj1")
        assert len(result) == 3


# ---------------------------------------------------------------------------
# _safe_file_count (lines 739-741)
# ---------------------------------------------------------------------------

class TestSafeFileCount:
    def test_safe_file_count_error(self, monkeypatch):
        import onemancompany.core.project_archive as pa_mod
        monkeypatch.setattr(pa_mod, "list_project_files",
                           MagicMock(side_effect=RuntimeError("boom")))
        assert pa_mod._safe_file_count("proj1") == 0


# ---------------------------------------------------------------------------
# list_projects — iteration loading (lines 762, 882)
# ---------------------------------------------------------------------------

class TestListProjects:
    def test_list_projects_skips_no_iterations(self, tmp_path, monkeypatch):
        import onemancompany.core.project_archive as pa_mod
        monkeypatch.setattr(pa_mod, "PROJECTS_DIR", tmp_path)

        proj_dir = tmp_path / "proj1"
        proj_dir.mkdir()
        with open(proj_dir / "project.yaml", "w") as f:
            yaml.dump({"name": "No Iters"}, f)

        result = pa_mod.list_projects()
        # Should skip projects without iterations key
        assert len(result) == 0
