"""Unit tests for core/config.py — comprehensive coverage for all uncovered functions."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from onemancompany.core.config import EmployeeConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_profile(directory: Path, emp_id: str, data: dict) -> Path:
    """Write a profile.yaml to directory/emp_id/."""
    emp_dir = directory / emp_id
    emp_dir.mkdir(parents=True, exist_ok=True)
    profile = emp_dir / "profile.yaml"
    with open(profile, "w") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False)
    return emp_dir


# ---------------------------------------------------------------------------
# _read_app_config_from_disk / load_app_config / reload_app_config / is_hot_reload_enabled
# ---------------------------------------------------------------------------

class TestAppConfig:
    def test_read_app_config_missing_file(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod

        monkeypatch.setattr(config_mod, "APP_CONFIG_PATH", tmp_path / "missing.yaml")
        result = config_mod._read_app_config_from_disk()
        assert result == {}

    def test_read_app_config_from_disk(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod

        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("hot_reload: true\nport: 9000\n")
        monkeypatch.setattr(config_mod, "APP_CONFIG_PATH", cfg_file)
        result = config_mod._read_app_config_from_disk()
        assert result["hot_reload"] is True
        assert result["port"] == 9000

    def test_load_app_config_returns_cached(self, monkeypatch):
        import onemancompany.core.config as config_mod

        monkeypatch.setattr(config_mod, "_app_config", {"cached": True})
        result = config_mod.load_app_config()
        assert result == {"cached": True}

    def test_reload_app_config(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod

        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("hot_reload: true\n")
        monkeypatch.setattr(config_mod, "APP_CONFIG_PATH", cfg_file)
        result = config_mod.reload_app_config()
        assert result["hot_reload"] is True
        # Verify internal cache is updated
        assert config_mod._app_config["hot_reload"] is True

    def test_is_hot_reload_enabled_true(self, monkeypatch):
        import onemancompany.core.config as config_mod

        monkeypatch.setattr(config_mod, "_app_config", {"hot_reload": True})
        assert config_mod.is_hot_reload_enabled() is True

    def test_is_hot_reload_enabled_false(self, monkeypatch):
        import onemancompany.core.config as config_mod

        monkeypatch.setattr(config_mod, "_app_config", {})
        assert config_mod.is_hot_reload_enabled() is False


# ---------------------------------------------------------------------------
# load_employee_configs
# ---------------------------------------------------------------------------

class TestLoadEmployeeConfigs:
    def test_dir_not_exists(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod

        monkeypatch.setattr(config_mod, "EMPLOYEES_DIR", tmp_path / "nonexistent")
        result = config_mod.load_employee_configs()
        assert result == {}

    def test_skips_non_directory(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod

        monkeypatch.setattr(config_mod, "EMPLOYEES_DIR", tmp_path)
        # Create a file, not a directory
        (tmp_path / "readme.txt").write_text("not a dir")
        result = config_mod.load_employee_configs()
        assert result == {}

    def test_skips_dir_without_profile(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod

        monkeypatch.setattr(config_mod, "EMPLOYEES_DIR", tmp_path)
        (tmp_path / "00010").mkdir()
        # No profile.yaml inside
        result = config_mod.load_employee_configs()
        assert result == {}

    def test_loads_valid_profiles(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod

        monkeypatch.setattr(config_mod, "EMPLOYEES_DIR", tmp_path)
        _write_profile(tmp_path, "00010", {"name": "Alice", "role": "Engineer", "skills": ["python"]})
        _write_profile(tmp_path, "00011", {"name": "Bob", "role": "Designer", "skills": ["figma"]})
        result = config_mod.load_employee_configs()
        assert len(result) == 2
        assert result["00010"].name == "Alice"
        assert result["00011"].role == "Designer"


# ---------------------------------------------------------------------------
# load_employee_skills
# ---------------------------------------------------------------------------

class TestLoadEmployeeSkills:
    def test_skills_dir_missing(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod

        monkeypatch.setattr(config_mod, "EMPLOYEES_DIR", tmp_path)
        result = config_mod.load_employee_skills("00010")
        assert result == {}

    def test_loads_folder_skills_only(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod

        monkeypatch.setattr(config_mod, "EMPLOYEES_DIR", tmp_path)
        skills_dir = tmp_path / "00010" / "skills"
        skills_dir.mkdir(parents=True)
        # Folder-based skill with SKILL.md
        (skills_dir / "python").mkdir()
        (skills_dir / "python" / "SKILL.md").write_text("# Python\nExpert level")
        # Plain .md file — should be ignored
        (skills_dir / "stale.md").write_text("should be ignored")
        # Subdirectory without SKILL.md — should be ignored
        (skills_dir / "empty_dir").mkdir()

        result = config_mod.load_employee_skills("00010")
        assert len(result) == 1
        assert "python" in result
        assert "Python" in result["python"]


# ---------------------------------------------------------------------------
# load_employee_guidance / save_employee_guidance
# ---------------------------------------------------------------------------

class TestEmployeeGuidance:
    def test_load_missing_file(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod

        monkeypatch.setattr(config_mod, "EMPLOYEES_DIR", tmp_path)
        result = config_mod.load_employee_guidance("00010")
        assert result == []

    def test_load_with_list_data(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod

        monkeypatch.setattr(config_mod, "EMPLOYEES_DIR", tmp_path)
        emp_dir = tmp_path / "00010"
        emp_dir.mkdir()
        guidance_path = emp_dir / "guidance.yaml"
        yaml.dump(["note1", "note2"], open(guidance_path, "w"))

        result = config_mod.load_employee_guidance("00010")
        assert result == ["note1", "note2"]

    def test_load_with_non_list_data(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod

        monkeypatch.setattr(config_mod, "EMPLOYEES_DIR", tmp_path)
        emp_dir = tmp_path / "00010"
        emp_dir.mkdir()
        guidance_path = emp_dir / "guidance.yaml"
        # Write a dict instead of list
        yaml.dump({"key": "value"}, open(guidance_path, "w"))

        result = config_mod.load_employee_guidance("00010")
        assert result == []



# ---------------------------------------------------------------------------
# load_work_principles
# ---------------------------------------------------------------------------

class TestWorkPrinciples:
    def test_load_missing_file(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod

        monkeypatch.setattr(config_mod, "EMPLOYEES_DIR", tmp_path)
        result = config_mod.load_work_principles("00010")
        assert result == ""

    def test_load_existing_file(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod

        monkeypatch.setattr(config_mod, "EMPLOYEES_DIR", tmp_path)
        emp_dir = tmp_path / "00010"
        emp_dir.mkdir()
        (emp_dir / "work_principles.md").write_text("# Principles\n1. Be good")

        result = config_mod.load_work_principles("00010")
        assert "Be good" in result



# ---------------------------------------------------------------------------
# ensure_employee_dir
# ---------------------------------------------------------------------------

class TestEnsureEmployeeDir:
    def test_creates_directories(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod

        monkeypatch.setattr(config_mod, "EMPLOYEES_DIR", tmp_path)
        result = config_mod.ensure_employee_dir("00010")
        assert result == tmp_path / "00010"
        assert result.is_dir()
        assert (result / "skills").is_dir()


# ---------------------------------------------------------------------------
# slugify_tool_name
# ---------------------------------------------------------------------------

class TestSlugifyToolName:
    def test_basic_name(self):
        from onemancompany.core.config import slugify_tool_name

        assert slugify_tool_name("My Cool Tool") == "my_cool_tool"

    def test_special_characters(self):
        from onemancompany.core.config import slugify_tool_name

        assert slugify_tool_name("tool@v2.0!") == "toolv20"

    def test_cjk_characters(self):
        from onemancompany.core.config import slugify_tool_name

        result = slugify_tool_name("代码工具")
        assert result == "代码工具"

    def test_multiple_underscores(self):
        from onemancompany.core.config import slugify_tool_name

        assert slugify_tool_name("a  b   c") == "a_b_c"

    def test_empty_name(self):
        from onemancompany.core.config import slugify_tool_name

        assert slugify_tool_name("!!!") == "unnamed_tool"

    def test_leading_trailing_whitespace(self):
        from onemancompany.core.config import slugify_tool_name

        assert slugify_tool_name("  hello  ") == "hello"


# ---------------------------------------------------------------------------
# load_assets
# ---------------------------------------------------------------------------

class TestLoadAssets:
    def test_folder_based_tool(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod

        tools_dir = tmp_path / "tools"
        rooms_dir = tmp_path / "rooms"
        tools_dir.mkdir()
        rooms_dir.mkdir()
        monkeypatch.setattr(config_mod, "TOOLS_DIR", tools_dir)
        monkeypatch.setattr(config_mod, "ROOMS_DIR", rooms_dir)

        # Folder-based tool
        tool_folder = tools_dir / "my_tool"
        tool_folder.mkdir()
        yaml.dump({"id": "tool1", "name": "My Tool"}, open(tool_folder / "tool.yaml", "w"))
        (tool_folder / "extra_file.txt").write_text("extra")

        tools, rooms = config_mod.load_assets()
        assert "tool1" in tools
        assert tools["tool1"]["name"] == "My Tool"
        assert tools["tool1"]["_folder_name"] == "my_tool"
        assert "extra_file.txt" in tools["tool1"]["_files"]

    def test_legacy_flat_tool(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod

        tools_dir = tmp_path / "tools"
        rooms_dir = tmp_path / "rooms"
        tools_dir.mkdir()
        rooms_dir.mkdir()
        monkeypatch.setattr(config_mod, "TOOLS_DIR", tools_dir)
        monkeypatch.setattr(config_mod, "ROOMS_DIR", rooms_dir)

        # Legacy flat YAML tool
        yaml.dump({"name": "Legacy Tool"}, open(tools_dir / "uuid123.yaml", "w"))

        tools, rooms = config_mod.load_assets()
        assert "uuid123" in tools
        assert tools["uuid123"]["_legacy"] is True

    def test_meeting_rooms(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod

        tools_dir = tmp_path / "tools"
        rooms_dir = tmp_path / "rooms"
        tools_dir.mkdir()
        rooms_dir.mkdir()
        monkeypatch.setattr(config_mod, "TOOLS_DIR", tools_dir)
        monkeypatch.setattr(config_mod, "ROOMS_DIR", rooms_dir)

        yaml.dump({"name": "Room A", "capacity": 10}, open(rooms_dir / "room1.yaml", "w"))

        tools, rooms = config_mod.load_assets()
        assert "room1" in rooms
        assert rooms["room1"]["capacity"] == 10

    def test_dirs_missing(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod

        monkeypatch.setattr(config_mod, "TOOLS_DIR", tmp_path / "no_tools")
        monkeypatch.setattr(config_mod, "ROOMS_DIR", tmp_path / "no_rooms")
        tools, rooms = config_mod.load_assets()
        assert tools == {}
        assert rooms == {}

    def test_folder_without_tool_yaml_ignored(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod

        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        monkeypatch.setattr(config_mod, "TOOLS_DIR", tools_dir)
        monkeypatch.setattr(config_mod, "ROOMS_DIR", tmp_path / "no_rooms")

        # Folder without tool.yaml
        (tools_dir / "empty_folder").mkdir()

        tools, _ = config_mod.load_assets()
        assert tools == {}


# ---------------------------------------------------------------------------
# migrate_legacy_tool
# ---------------------------------------------------------------------------

class TestMigrateLegacyTool:
    def test_basic_migration(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod

        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        monkeypatch.setattr(config_mod, "TOOLS_DIR", tools_dir)

        # Create legacy flat file
        yaml.dump({"name": "Cool Tool", "description": "A tool"},
                  open(tools_dir / "uuid123.yaml", "w"))

        slug, new_id = config_mod.migrate_legacy_tool(
            "uuid123", {"name": "Cool Tool", "description": "A tool"}
        )

        assert slug == "cool_tool"
        assert new_id == "uuid123"
        # New folder-based structure created
        assert (tools_dir / "cool_tool" / "tool.yaml").exists()
        # Old flat file removed
        assert not (tools_dir / "uuid123.yaml").exists()

    def test_collision_handling(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod

        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        monkeypatch.setattr(config_mod, "TOOLS_DIR", tools_dir)

        used_slugs = {"cool_tool"}  # Already used
        slug, new_id = config_mod.migrate_legacy_tool(
            "uuid456", {"name": "Cool Tool"}, used_slugs=used_slugs
        )

        assert slug == "cool_tool_uuid456"
        assert "cool_tool_uuid456" in used_slugs

    def test_no_old_file_to_remove(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod

        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        monkeypatch.setattr(config_mod, "TOOLS_DIR", tools_dir)

        # No old flat file exists — should not raise
        slug, new_id = config_mod.migrate_legacy_tool(
            "uuid789", {"name": "Another Tool"}
        )
        assert slug == "another_tool"

    def test_strips_internal_keys(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod

        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        monkeypatch.setattr(config_mod, "TOOLS_DIR", tools_dir)

        data = {"name": "Test", "_legacy": True, "_internal": "stuff"}
        config_mod.migrate_legacy_tool("tid", data)

        with open(tools_dir / "test" / "tool.yaml") as f:
            saved = yaml.safe_load(f)
        assert "_legacy" not in saved
        assert "_internal" not in saved
        assert saved["id"] == "tid"


# ---------------------------------------------------------------------------
# load_workflows / save_workflow
# ---------------------------------------------------------------------------

class TestWorkflows:
    def test_load_missing_dir(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod

        monkeypatch.setattr(config_mod, "WORKFLOWS_DIR", tmp_path / "nonexistent")
        result = config_mod.load_workflows()
        assert result == {}

    def test_load_md_files(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod

        monkeypatch.setattr(config_mod, "WORKFLOWS_DIR", tmp_path)
        (tmp_path / "onboarding.md").write_text("# Onboarding\nStep 1")
        (tmp_path / "review.md").write_text("# Review\nStep 1")
        (tmp_path / "notes.txt").write_text("not a workflow")

        result = config_mod.load_workflows()
        assert len(result) == 2
        assert "onboarding" in result
        assert "review" in result
        assert "Onboarding" in result["onboarding"]

    def test_save_creates_dir(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod

        wf_dir = tmp_path / "workflows"
        monkeypatch.setattr(config_mod, "WORKFLOWS_DIR", wf_dir)

        config_mod.save_workflow("new_flow", "# New Flow\nContent")

        assert (wf_dir / "new_flow.md").exists()
        assert "New Flow" in (wf_dir / "new_flow.md").read_text()


# ---------------------------------------------------------------------------
# load_ex_employee_configs
# ---------------------------------------------------------------------------

class TestLoadExEmployeeConfigs:
    def test_dir_not_exists(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod

        monkeypatch.setattr(config_mod, "EX_EMPLOYEES_DIR", tmp_path / "nonexistent")
        result = config_mod.load_ex_employee_configs()
        assert result == {}

    def test_skips_non_dir(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod

        monkeypatch.setattr(config_mod, "EX_EMPLOYEES_DIR", tmp_path)
        (tmp_path / "readme.txt").write_text("not a dir")
        result = config_mod.load_ex_employee_configs()
        assert result == {}

    def test_skips_dir_without_profile(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod

        monkeypatch.setattr(config_mod, "EX_EMPLOYEES_DIR", tmp_path)
        (tmp_path / "00010").mkdir()
        result = config_mod.load_ex_employee_configs()
        assert result == {}

    def test_loads_valid_ex_profiles(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod

        monkeypatch.setattr(config_mod, "EX_EMPLOYEES_DIR", tmp_path)
        _write_profile(tmp_path, "00010", {"name": "Former", "role": "Engineer", "skills": ["python"]})
        result = config_mod.load_ex_employee_configs()
        assert "00010" in result
        assert result["00010"].name == "Former"


# ---------------------------------------------------------------------------
# move_employee_to_ex
# ---------------------------------------------------------------------------

class TestMoveEmployeeToEx:
    def test_move_with_existing_dst(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod

        emp_dir = tmp_path / "employees"
        ex_dir = tmp_path / "ex-employees"
        emp_dir.mkdir()
        ex_dir.mkdir()
        monkeypatch.setattr(config_mod, "EMPLOYEES_DIR", emp_dir)
        monkeypatch.setattr(config_mod, "EX_EMPLOYEES_DIR", ex_dir)
        monkeypatch.setattr(config_mod, "employee_configs", {
            "00010": EmployeeConfig(name="T", role="E", skills=[])
        })

        # Create employee folder
        (emp_dir / "00010").mkdir()
        (emp_dir / "00010" / "profile.yaml").write_text("name: T\nrole: E\nskills: []\n")

        # Pre-existing destination should be overwritten
        (ex_dir / "00010").mkdir()
        (ex_dir / "00010" / "old.txt").write_text("old data")

        result = config_mod.move_employee_to_ex("00010")
        assert result is True
        assert not (emp_dir / "00010").exists()
        assert (ex_dir / "00010").exists()
        # Old data should have been replaced
        assert not (ex_dir / "00010" / "old.txt").exists()


# ---------------------------------------------------------------------------
# move_ex_employee_back
# ---------------------------------------------------------------------------

class TestMoveExEmployeeBack:
    def test_move_with_existing_dst(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod

        emp_dir = tmp_path / "employees"
        ex_dir = tmp_path / "ex-employees"
        emp_dir.mkdir()
        ex_dir.mkdir()
        monkeypatch.setattr(config_mod, "EMPLOYEES_DIR", emp_dir)
        monkeypatch.setattr(config_mod, "EX_EMPLOYEES_DIR", ex_dir)
        # Create ex-employee folder
        (ex_dir / "00010").mkdir()
        (ex_dir / "00010" / "profile.yaml").write_text("name: Rehired\nrole: Engineer\nskills: [python]\n")

        # Pre-existing destination should be overwritten
        (emp_dir / "00010").mkdir()
        (emp_dir / "00010" / "old.txt").write_text("old data")

        result = config_mod.move_ex_employee_back("00010")
        assert result is True
        assert not (ex_dir / "00010").exists()
        assert (emp_dir / "00010").exists()
        # Verify employee loadable from disk
        loaded = config_mod.load_employee_configs()
        assert "00010" in loaded


# ---------------------------------------------------------------------------
# load_company_culture / save_company_culture
# ---------------------------------------------------------------------------

class TestCompanyCulture:
    def test_load_missing_file(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod

        monkeypatch.setattr(config_mod, "COMPANY_CULTURE_FILE", tmp_path / "missing.yaml")
        result = config_mod.load_company_culture()
        assert result == []

    def test_load_list_data(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod

        culture_file = tmp_path / "culture.yaml"
        yaml.dump([{"value": "Innovation"}, {"value": "Quality"}], open(culture_file, "w"))
        monkeypatch.setattr(config_mod, "COMPANY_CULTURE_FILE", culture_file)

        result = config_mod.load_company_culture()
        assert len(result) == 2
        assert result[0]["value"] == "Innovation"

    def test_load_non_list_data(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod

        culture_file = tmp_path / "culture.yaml"
        yaml.dump({"key": "value"}, open(culture_file, "w"))
        monkeypatch.setattr(config_mod, "COMPANY_CULTURE_FILE", culture_file)

        result = config_mod.load_company_culture()
        assert result == []


# ---------------------------------------------------------------------------
# load_company_direction / save_company_direction
# ---------------------------------------------------------------------------

class TestCompanyDirection:
    def test_load_missing_file(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod

        monkeypatch.setattr(config_mod, "COMPANY_DIRECTION_FILE", tmp_path / "missing.yaml")
        result = config_mod.load_company_direction()
        assert result == ""

    def test_load_dict_with_direction(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod

        direction_file = tmp_path / "direction.yaml"
        yaml.dump({"direction": "Go global!"}, open(direction_file, "w"))
        monkeypatch.setattr(config_mod, "COMPANY_DIRECTION_FILE", direction_file)

        result = config_mod.load_company_direction()
        assert result == "Go global!"

    def test_load_non_dict_data(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod

        direction_file = tmp_path / "direction.yaml"
        yaml.dump("just a string", open(direction_file, "w"))
        monkeypatch.setattr(config_mod, "COMPANY_DIRECTION_FILE", direction_file)

        result = config_mod.load_company_direction()
        assert result == ""

    def test_load_dict_without_direction_key(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod

        direction_file = tmp_path / "direction.yaml"
        yaml.dump({"other": "data"}, open(direction_file, "w"))
        monkeypatch.setattr(config_mod, "COMPANY_DIRECTION_FILE", direction_file)

        result = config_mod.load_company_direction()
        assert result == ""

    def test_save_direction(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod

        direction_file = tmp_path / "direction.yaml"
        monkeypatch.setattr(config_mod, "COMPANY_DIRECTION_FILE", direction_file)

        config_mod.save_company_direction("Go global!")

        with open(direction_file) as f:
            data = yaml.safe_load(f)
        assert data["direction"] == "Go global!"
        assert "updated_at" in data
        assert data["updated_by"] == "CEO"


# ---------------------------------------------------------------------------
# load_manifest / invalidate_manifest_cache
# ---------------------------------------------------------------------------

class TestManifest:
    def test_load_cached(self, monkeypatch):
        import onemancompany.core.config as config_mod

        monkeypatch.setattr(config_mod, "MANIFEST_CACHE", {"00010": {"tools": ["a"]}})
        result = config_mod.load_manifest("00010")
        assert result == {"tools": ["a"]}

    def test_load_missing_file(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod

        monkeypatch.setattr(config_mod, "EMPLOYEES_DIR", tmp_path)
        monkeypatch.setattr(config_mod, "MANIFEST_CACHE", {})
        result = config_mod.load_manifest("00010")
        assert result is None

    def test_load_from_disk(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod
        import json

        monkeypatch.setattr(config_mod, "EMPLOYEES_DIR", tmp_path)
        cache = {}
        monkeypatch.setattr(config_mod, "MANIFEST_CACHE", cache)

        emp_dir = tmp_path / "00010"
        emp_dir.mkdir()
        manifest = {"tools": ["sandbox_execute_code"], "version": 2}
        (emp_dir / "manifest.json").write_text(json.dumps(manifest))

        result = config_mod.load_manifest("00010")
        assert result == manifest
        # Should be cached now
        assert "00010" in cache

    def test_invalidate_all(self, monkeypatch):
        import onemancompany.core.config as config_mod

        cache = {"00010": {"a": 1}, "00011": {"b": 2}}
        monkeypatch.setattr(config_mod, "MANIFEST_CACHE", cache)

        config_mod.invalidate_manifest_cache()
        assert cache == {}

    def test_invalidate_specific(self, monkeypatch):
        import onemancompany.core.config as config_mod

        cache = {"00010": {"a": 1}, "00011": {"b": 2}}
        monkeypatch.setattr(config_mod, "MANIFEST_CACHE", cache)

        config_mod.invalidate_manifest_cache("00010")
        assert "00010" not in cache
        assert "00011" in cache

    def test_invalidate_missing_key(self, monkeypatch):
        import onemancompany.core.config as config_mod

        cache = {"00010": {"a": 1}}
        monkeypatch.setattr(config_mod, "MANIFEST_CACHE", cache)

        # Should not raise
        config_mod.invalidate_manifest_cache("99999")
        assert cache == {"00010": {"a": 1}}


# ---------------------------------------------------------------------------
# list_available_talents
# ---------------------------------------------------------------------------

class TestListAvailableTalents:
    def test_talents_dir_not_exists(self, tmp_path, monkeypatch):
        """TALENTS_DIR doesn't exist => returns []."""
        import onemancompany.core.config as config_mod

        monkeypatch.setattr(config_mod, "TALENTS_DIR", tmp_path / "nonexistent")
        result = config_mod.list_available_talents()
        assert result == []

    def test_lists_talents_with_profiles(self, tmp_path, monkeypatch):
        """Talent dirs with profile.yaml are listed."""
        import onemancompany.core.config as config_mod

        monkeypatch.setattr(config_mod, "TALENTS_DIR", tmp_path)

        # Valid talent with all fields
        t1 = tmp_path / "talent_a"
        t1.mkdir()
        yaml.dump({
            "id": "ta",
            "name": "Talent A",
            "role": "Engineer",
            "remote": True,
            "description": "A great talent",
            "api_provider": "anthropic",
        }, open(t1 / "profile.yaml", "w"))

        # Valid talent with minimal fields (defaults tested)
        t2 = tmp_path / "talent_b"
        t2.mkdir()
        yaml.dump({"name": "Talent B"}, open(t2 / "profile.yaml", "w"))

        # Non-directory file — should be skipped
        (tmp_path / "readme.txt").write_text("not a talent")

        # Directory without profile.yaml — should be skipped
        (tmp_path / "no_profile").mkdir()

        result = config_mod.list_available_talents()
        assert len(result) == 2
        # Check talent_a
        ta = next(r for r in result if r["name"] == "Talent A")
        assert ta["id"] == "ta"
        assert ta["role"] == "Engineer"
        assert ta["remote"] is True
        assert ta["description"] == "A great talent"
        assert ta["api_provider"] == "anthropic"
        # Check talent_b defaults
        tb = next(r for r in result if r["name"] == "Talent B")
        assert tb["id"] == "talent_b"  # falls back to dir name
        assert tb["role"] == ""
        assert tb["remote"] is False
        assert tb["api_provider"] == "openrouter"


# ---------------------------------------------------------------------------
# load_talent_profile
# ---------------------------------------------------------------------------

class TestLoadTalentProfile:
    def test_missing_profile(self, tmp_path, monkeypatch):
        """No profile.yaml => returns {}."""
        import onemancompany.core.config as config_mod

        monkeypatch.setattr(config_mod, "TALENTS_DIR", tmp_path)
        result = config_mod.load_talent_profile("nonexistent")
        assert result == {}

    def test_existing_profile(self, tmp_path, monkeypatch):
        """Valid profile.yaml => returns parsed dict."""
        import onemancompany.core.config as config_mod

        monkeypatch.setattr(config_mod, "TALENTS_DIR", tmp_path)
        talent_dir = tmp_path / "my_talent"
        talent_dir.mkdir()
        yaml.dump({"name": "My Talent", "role": "Designer"}, open(talent_dir / "profile.yaml", "w"))

        result = config_mod.load_talent_profile("my_talent")
        assert result["name"] == "My Talent"
        assert result["role"] == "Designer"


# ---------------------------------------------------------------------------
# load_talent_tools
# ---------------------------------------------------------------------------

class TestLoadTalentTools:
    def test_missing_manifest(self, tmp_path, monkeypatch):
        """No manifest.yaml => returns []."""
        import onemancompany.core.config as config_mod

        monkeypatch.setattr(config_mod, "TALENTS_DIR", tmp_path)
        result = config_mod.load_talent_tools("nonexistent")
        assert result == []

    def test_loads_builtin_and_custom(self, tmp_path, monkeypatch):
        """Reads both builtin_tools and custom_tools from manifest."""
        import onemancompany.core.config as config_mod

        monkeypatch.setattr(config_mod, "TALENTS_DIR", tmp_path)
        tools_dir = tmp_path / "my_talent" / "tools"
        tools_dir.mkdir(parents=True)
        yaml.dump({
            "builtin_tools": ["search", "calculator"],
            "custom_tools": ["my_custom"],
        }, open(tools_dir / "manifest.yaml", "w"))

        result = config_mod.load_talent_tools("my_talent")
        assert result == ["search", "calculator", "my_custom"]

    def test_empty_manifest(self, tmp_path, monkeypatch):
        """manifest.yaml with no tools keys => returns []."""
        import onemancompany.core.config as config_mod

        monkeypatch.setattr(config_mod, "TALENTS_DIR", tmp_path)
        tools_dir = tmp_path / "my_talent" / "tools"
        tools_dir.mkdir(parents=True)
        yaml.dump({}, open(tools_dir / "manifest.yaml", "w"))

        result = config_mod.load_talent_tools("my_talent")
        assert result == []


# ---------------------------------------------------------------------------
# load_talent_skills
# ---------------------------------------------------------------------------

class TestLoadTalentSkills:
    def test_missing_skills_dir(self, tmp_path, monkeypatch):
        """No skills dir => returns []."""
        import onemancompany.core.config as config_mod

        monkeypatch.setattr(config_mod, "TALENTS_DIR", tmp_path)
        result = config_mod.load_talent_skills("nonexistent")
        assert result == []

    def test_loads_md_files(self, tmp_path, monkeypatch):
        """Loads .md files from skills dir, ignores other files."""
        import onemancompany.core.config as config_mod

        monkeypatch.setattr(config_mod, "TALENTS_DIR", tmp_path)
        skills_dir = tmp_path / "my_talent" / "skills"
        skills_dir.mkdir(parents=True)
        (skills_dir / "python.md").write_text("# Python\nExpert")
        (skills_dir / "go.md").write_text("# Go\nBeginner")
        (skills_dir / "notes.txt").write_text("should be ignored")
        (skills_dir / "subdir").mkdir()  # should be ignored

        result = config_mod.load_talent_skills("my_talent")
        assert len(result) == 2
        assert any("Python" in s for s in result)
        assert any("Go" in s for s in result)


# ---------------------------------------------------------------------------
# move_employee_to_ex / move_ex_employee_back — early returns
# ---------------------------------------------------------------------------

class TestMoveEmployeeEarlyReturns:
    def test_move_to_ex_src_not_exists(self, tmp_path, monkeypatch):
        """Source employee dir doesn't exist => returns False."""
        import onemancompany.core.config as config_mod

        monkeypatch.setattr(config_mod, "EMPLOYEES_DIR", tmp_path)
        result = config_mod.move_employee_to_ex("nonexistent")
        assert result is False

    def test_move_ex_back_src_not_exists(self, tmp_path, monkeypatch):
        """Source ex-employee dir doesn't exist => returns False."""
        import onemancompany.core.config as config_mod

        monkeypatch.setattr(config_mod, "EX_EMPLOYEES_DIR", tmp_path)
        result = config_mod.move_ex_employee_back("nonexistent")
        assert result is False
