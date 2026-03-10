"""Unit tests for core/config.py — employee profile, talent loading, move to ex."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from onemancompany.core.config import EmployeeConfig


# ---------------------------------------------------------------------------
# ensure_employee_dir
# ---------------------------------------------------------------------------

class TestEnsureEmployeeDir:
    def test_creates_directory_and_skills(self, tmp_path, monkeypatch):
        import onemancompany.core.config as cfg

        monkeypatch.setattr(cfg, "EMPLOYEES_DIR", tmp_path)
        result = cfg.ensure_employee_dir("00099")
        assert result == tmp_path / "00099"
        assert result.is_dir()
        assert (result / "skills").is_dir()

    def test_idempotent(self, tmp_path, monkeypatch):
        import onemancompany.core.config as cfg

        monkeypatch.setattr(cfg, "EMPLOYEES_DIR", tmp_path)
        cfg.ensure_employee_dir("00099")
        cfg.ensure_employee_dir("00099")  # second call should not fail
        assert (tmp_path / "00099").is_dir()


# ---------------------------------------------------------------------------
# save_employee_profile (without template)
# ---------------------------------------------------------------------------

class TestSaveEmployeeProfile:
    def test_saves_yaml_without_template(self, tmp_path, monkeypatch):
        import onemancompany.core.config as cfg

        monkeypatch.setattr(cfg, "EMPLOYEES_DIR", tmp_path)
        monkeypatch.setattr(cfg, "PROFILE_TEMPLATE", tmp_path / "nonexistent.yaml")
        # Ensure employee_configs dict exists
        monkeypatch.setattr(cfg, "employee_configs", {})

        config = EmployeeConfig(
            name="Test Dev",
            nickname="追风",
            role="Engineer",
            skills=["python", "java"],
            level=1,
            department="Engineering",
            employee_number="00099",
            desk_position=[5, 3],
            sprite="employee_blue",
        )

        cfg.save_employee_profile("00099", config)

        profile_path = tmp_path / "00099" / "profile.yaml"
        assert profile_path.exists()

        data = yaml.safe_load(profile_path.read_text())
        assert data["name"] == "Test Dev"
        assert data["nickname"] == "追风"
        assert data["role"] == "Engineer"
        assert "python" in data["skills"]

        # Should be in employee_configs cache
        assert "00099" in cfg.employee_configs

    def test_updates_employee_configs_cache(self, tmp_path, monkeypatch):
        import onemancompany.core.config as cfg

        monkeypatch.setattr(cfg, "EMPLOYEES_DIR", tmp_path)
        monkeypatch.setattr(cfg, "PROFILE_TEMPLATE", tmp_path / "nonexistent.yaml")
        monkeypatch.setattr(cfg, "employee_configs", {})

        config = EmployeeConfig(
            name="Cache Test", role="Designer", skills=["figma"],
        )
        cfg.save_employee_profile("00099", config)

        assert cfg.employee_configs["00099"].name == "Cache Test"


# ---------------------------------------------------------------------------
# save_work_principles
# ---------------------------------------------------------------------------

class TestSaveWorkPrinciples:
    def test_saves_as_skill(self, tmp_path, monkeypatch):
        import onemancompany.core.config as cfg

        monkeypatch.setattr(cfg, "EMPLOYEES_DIR", tmp_path)

        cfg.save_work_principles("00099", "# Principles\n\n1. Work hard\n")

        path = tmp_path / "00099" / "skills" / "work-principles" / "SKILL.md"
        assert path.exists()
        content = path.read_text()
        assert "autoload: true" in content
        assert "Work hard" in content


# ---------------------------------------------------------------------------
# move_employee_to_ex / move_ex_employee_back
# ---------------------------------------------------------------------------

class TestMoveEmployee:
    def test_move_to_ex(self, tmp_path, monkeypatch):
        import onemancompany.core.config as cfg

        emp_dir = tmp_path / "employees"
        ex_dir = tmp_path / "ex-employees"
        emp_dir.mkdir()
        ex_dir.mkdir()
        monkeypatch.setattr(cfg, "EMPLOYEES_DIR", emp_dir)
        monkeypatch.setattr(cfg, "EX_EMPLOYEES_DIR", ex_dir)
        monkeypatch.setattr(cfg, "employee_configs", {"00010": EmployeeConfig(name="T", role="E", skills=[])})

        # Create employee folder
        (emp_dir / "00010").mkdir()
        (emp_dir / "00010" / "profile.yaml").write_text("name: T\nrole: E\nskills: []\n")

        result = cfg.move_employee_to_ex("00010")
        assert result is True
        assert not (emp_dir / "00010").exists()
        assert (ex_dir / "00010").exists()
        assert "00010" not in cfg.employee_configs

    def test_move_nonexistent_returns_false(self, tmp_path, monkeypatch):
        import onemancompany.core.config as cfg

        monkeypatch.setattr(cfg, "EMPLOYEES_DIR", tmp_path)
        result = cfg.move_employee_to_ex("99999")
        assert result is False

    def test_move_ex_back(self, tmp_path, monkeypatch):
        import onemancompany.core.config as cfg

        emp_dir = tmp_path / "employees"
        ex_dir = tmp_path / "ex-employees"
        emp_dir.mkdir()
        ex_dir.mkdir()
        monkeypatch.setattr(cfg, "EMPLOYEES_DIR", emp_dir)
        monkeypatch.setattr(cfg, "EX_EMPLOYEES_DIR", ex_dir)
        monkeypatch.setattr(cfg, "employee_configs", {})

        # Create ex-employee folder
        (ex_dir / "00010").mkdir()
        (ex_dir / "00010" / "profile.yaml").write_text("name: Rehired\nrole: Engineer\nskills: [python]\n")

        result = cfg.move_ex_employee_back("00010")
        assert result is True
        assert not (ex_dir / "00010").exists()
        assert (emp_dir / "00010").exists()
        assert "00010" in cfg.employee_configs
        assert cfg.employee_configs["00010"].name == "Rehired"

    def test_move_ex_back_nonexistent_returns_false(self, tmp_path, monkeypatch):
        import onemancompany.core.config as cfg

        monkeypatch.setattr(cfg, "EX_EMPLOYEES_DIR", tmp_path)
        result = cfg.move_ex_employee_back("99999")
        assert result is False


# ---------------------------------------------------------------------------
# load_talent_profile
# ---------------------------------------------------------------------------

class TestLoadTalentProfile:
    def test_loads_profile(self, tmp_path, monkeypatch):
        import onemancompany.core.config as cfg

        talent_dir = tmp_path / "coding"
        talent_dir.mkdir()
        (talent_dir / "profile.yaml").write_text("id: coding\nname: Coding Talent\nrole: Engineer\n")
        monkeypatch.setattr(cfg, "TALENTS_DIR", tmp_path)

        result = cfg.load_talent_profile("coding")
        assert result["id"] == "coding"
        assert result["name"] == "Coding Talent"

    def test_missing_talent_returns_empty(self, tmp_path, monkeypatch):
        import onemancompany.core.config as cfg

        monkeypatch.setattr(cfg, "TALENTS_DIR", tmp_path)
        result = cfg.load_talent_profile("nonexistent")
        assert result == {}


# ---------------------------------------------------------------------------
# load_talent_tools
# ---------------------------------------------------------------------------

class TestLoadTalentTools:
    def test_loads_builtin_and_custom(self, tmp_path, monkeypatch):
        import onemancompany.core.config as cfg

        tools_dir = tmp_path / "coding" / "tools"
        tools_dir.mkdir(parents=True)
        (tools_dir / "manifest.yaml").write_text(
            "builtin_tools:\n  - sandbox_execute_code\n  - sandbox_run_command\n"
            "custom_tools:\n  - custom_build\n"
        )
        monkeypatch.setattr(cfg, "TALENTS_DIR", tmp_path)

        tools = cfg.load_talent_tools("coding")
        assert "sandbox_execute_code" in tools
        assert "sandbox_run_command" in tools
        assert "custom_build" in tools

    def test_missing_manifest_returns_empty(self, tmp_path, monkeypatch):
        import onemancompany.core.config as cfg

        monkeypatch.setattr(cfg, "TALENTS_DIR", tmp_path)
        tools = cfg.load_talent_tools("nonexistent")
        assert tools == []


# ---------------------------------------------------------------------------
# load_talent_skills
# ---------------------------------------------------------------------------

class TestLoadTalentSkills:
    def test_loads_skill_markdown_files(self, tmp_path, monkeypatch):
        import onemancompany.core.config as cfg

        skills_dir = tmp_path / "coding" / "skills"
        skills_dir.mkdir(parents=True)
        (skills_dir / "python.md").write_text("# Python\nPython expertise")
        (skills_dir / "java.md").write_text("# Java\nJava expertise")
        (skills_dir / "notes.txt").write_text("not a skill")  # non-md ignored
        monkeypatch.setattr(cfg, "TALENTS_DIR", tmp_path)

        skills = cfg.load_talent_skills("coding")
        assert len(skills) == 2
        assert any("Python" in s for s in skills)
        assert any("Java" in s for s in skills)

    def test_missing_skills_dir_returns_empty(self, tmp_path, monkeypatch):
        import onemancompany.core.config as cfg

        monkeypatch.setattr(cfg, "TALENTS_DIR", tmp_path)
        skills = cfg.load_talent_skills("nonexistent")
        assert skills == []


# ---------------------------------------------------------------------------
# list_available_talents
# ---------------------------------------------------------------------------

class TestListAvailableTalents:
    def test_lists_talents(self, tmp_path, monkeypatch):
        import onemancompany.core.config as cfg

        for tid in ["coding", "artist"]:
            d = tmp_path / tid
            d.mkdir()
            (d / "profile.yaml").write_text(f"id: {tid}\nname: {tid.title()} Talent\nrole: Engineer\n")

        monkeypatch.setattr(cfg, "TALENTS_DIR", tmp_path)

        talents = cfg.list_available_talents()
        assert len(talents) == 2
        ids = {t["id"] for t in talents}
        assert ids == {"coding", "artist"}

    def test_empty_directory(self, tmp_path, monkeypatch):
        import onemancompany.core.config as cfg

        monkeypatch.setattr(cfg, "TALENTS_DIR", tmp_path)
        talents = cfg.list_available_talents()
        assert talents == []

    def test_skips_entries_without_profile(self, tmp_path, monkeypatch):
        import onemancompany.core.config as cfg

        (tmp_path / "no_profile").mkdir()
        (tmp_path / "readme.md").write_text("text file, not dir")

        d = tmp_path / "valid"
        d.mkdir()
        (d / "profile.yaml").write_text("id: valid\nname: Valid\nrole: Engineer\n")

        monkeypatch.setattr(cfg, "TALENTS_DIR", tmp_path)
        talents = cfg.list_available_talents()
        assert len(talents) == 1
        assert talents[0]["id"] == "valid"


# ---------------------------------------------------------------------------
# load_employee_configs
# ---------------------------------------------------------------------------

class TestLoadEmployeeConfigs:
    def test_loads_all_profiles(self, tmp_path, monkeypatch):
        import onemancompany.core.config as cfg

        for eid in ["00002", "00003"]:
            d = tmp_path / eid
            d.mkdir()
            (d / "profile.yaml").write_text(f"name: Emp{eid}\nrole: Engineer\nskills:\n  - python\n")

        monkeypatch.setattr(cfg, "EMPLOYEES_DIR", tmp_path)
        configs = cfg.load_employee_configs()
        assert len(configs) == 2
        assert configs["00002"].name == "Emp00002"

    def test_missing_dir_returns_empty(self, tmp_path, monkeypatch):
        import onemancompany.core.config as cfg

        monkeypatch.setattr(cfg, "EMPLOYEES_DIR", tmp_path / "nonexistent")
        configs = cfg.load_employee_configs()
        assert configs == {}


# ---------------------------------------------------------------------------
# EmployeeConfig model
# ---------------------------------------------------------------------------

class TestEmployeeConfig:
    def test_defaults(self):
        cfg = EmployeeConfig(name="Test", role="Engineer", skills=["python"])
        assert cfg.level == 1
        assert cfg.temperature == 0.7
        assert cfg.api_provider == "openrouter"
        assert cfg.hosting == "company"
        assert cfg.auth_method == "api_key"
        assert cfg.remote is False

    def test_full_fields(self):
        cfg = EmployeeConfig(
            name="Full",
            role="Designer",
            skills=["figma"],
            nickname="追风",
            level=3,
            department="Design",
            hosting="self",
            auth_method="oauth",
            remote=True,
        )
        assert cfg.nickname == "追风"
        assert cfg.hosting == "self"
        assert cfg.remote is True
