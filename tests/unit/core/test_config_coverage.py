"""Coverage tests for core/config.py — missing lines."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml


# ---------------------------------------------------------------------------
# OrgDir.description (line 217)
# ---------------------------------------------------------------------------

class TestOrgDirDescription:
    def test_description_property(self):
        from onemancompany.core.config import OrgDir
        for member in OrgDir:
            desc = member.description
            assert isinstance(desc, str)
            assert len(desc) > 0


# ---------------------------------------------------------------------------
# get_ceo_dnd — corrupt file (lines 271-272)
# ---------------------------------------------------------------------------

class TestGetCeoDnd:
    def test_corrupt_yaml_returns_false(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod
        dnd_path = tmp_path / "ceo_dnd.yaml"
        dnd_path.write_text(":::invalid yaml\n  - bad: [")
        monkeypatch.setattr(config_mod, "_CEO_DND_PATH", dnd_path)
        assert config_mod.get_ceo_dnd() is False


# ---------------------------------------------------------------------------
# update_env_var / reload_settings (lines 595-615)
# ---------------------------------------------------------------------------

class TestUpdateEnvVar:
    def test_update_existing_key(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod
        env_path = tmp_path / ".env"
        env_path.write_text("KEY1=old\nKEY2=keep\n")
        monkeypatch.setattr(config_mod, "DATA_ROOT", tmp_path)
        with patch.object(config_mod, "reload_settings"):
            config_mod.update_env_var("KEY1", "new")
        content = env_path.read_text()
        assert "KEY1=new" in content
        assert "KEY2=keep" in content

    def test_add_new_key(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod
        env_path = tmp_path / ".env"
        env_path.write_text("EXISTING=val\n")
        monkeypatch.setattr(config_mod, "DATA_ROOT", tmp_path)
        with patch.object(config_mod, "reload_settings"):
            config_mod.update_env_var("NEW_KEY", "new_val")
        content = env_path.read_text()
        assert "NEW_KEY=new_val" in content

    def test_add_key_to_nonexistent_env(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod
        monkeypatch.setattr(config_mod, "DATA_ROOT", tmp_path)
        with patch.object(config_mod, "reload_settings"):
            config_mod.update_env_var("BRAND_NEW", "value")
        env_path = tmp_path / ".env"
        assert "BRAND_NEW=value" in env_path.read_text()


class TestReloadSettings:
    def test_reload_settings(self, monkeypatch):
        import onemancompany.core.config as config_mod
        old_settings = config_mod.settings
        config_mod.reload_settings()
        # Settings object should be replaced
        assert config_mod.settings is not old_settings


class TestUpdateEnvVarSyncsOsEnviron:
    """Regression: update_env_var must sync os.environ so Settings() sees the
    new value.  main.py calls load_dotenv() at startup which seeds os.environ;
    pydantic BaseSettings reads os.environ with higher priority than .env file.
    If update_env_var only writes the file, the stale os.environ value wins and
    the setting appears to 'revert' on next read.
    """

    def test_update_env_var_updates_os_environ(self, tmp_path, monkeypatch):
        """update_env_var must set os.environ[key] so reload_settings picks it up."""
        import os
        import onemancompany.core.config as config_mod

        env_path = tmp_path / ".env"
        env_path.write_text("DEFAULT_LLM_MODEL=old/model\n")
        monkeypatch.setattr(config_mod, "DATA_ROOT", tmp_path)

        # Simulate startup: load_dotenv seeds os.environ
        os.environ["DEFAULT_LLM_MODEL"] = "old/model"

        with patch.object(config_mod, "reload_settings"):
            config_mod.update_env_var("DEFAULT_LLM_MODEL", "new/model")

        # os.environ must reflect the new value
        assert os.environ["DEFAULT_LLM_MODEL"] == "new/model"

        # Cleanup
        monkeypatch.delenv("DEFAULT_LLM_MODEL", raising=False)

    def test_settings_sees_new_model_after_update(self, tmp_path, monkeypatch):
        """End-to-end: after update_env_var, Settings().default_llm_model must
        return the NEW value, not the stale os.environ value from startup."""
        import onemancompany.core.config as config_mod

        env_path = tmp_path / ".env"
        env_path.write_text("DEFAULT_LLM_MODEL=startup/model\n")
        monkeypatch.setattr(config_mod, "DATA_ROOT", tmp_path)

        # Simulate startup: load_dotenv seeds os.environ with the old value
        monkeypatch.setenv("DEFAULT_LLM_MODEL", "startup/model")

        # Save a new model via update_env_var (calls reload_settings internally)
        config_mod.update_env_var("DEFAULT_LLM_MODEL", "user-chosen/model")

        # os.environ must have the new value so Settings() picks it up
        import os
        assert os.environ["DEFAULT_LLM_MODEL"] == "user-chosen/model"

        # A fresh Settings instance must see the new value
        fresh = config_mod.Settings()
        assert fresh.default_llm_model == "user-chosen/model"


# ---------------------------------------------------------------------------
# sync_founding_defaults (line 629)
# ---------------------------------------------------------------------------

class TestSyncFoundingDefaults:
    def test_sync_founding_defaults_no_profiles(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod
        monkeypatch.setattr(config_mod, "EMPLOYEES_DIR", tmp_path)
        count = config_mod.sync_founding_defaults("openrouter", "gpt-4")
        assert count == 0


# ---------------------------------------------------------------------------
# load_employee_configs — corrupt profile (lines 699-701)
# ---------------------------------------------------------------------------

class TestLoadEmployeeConfigsCorrupt:
    def test_skips_corrupt_profile(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod
        monkeypatch.setattr(config_mod, "EMPLOYEES_DIR", tmp_path)

        emp_dir = tmp_path / "00010"
        emp_dir.mkdir()
        profile = emp_dir / "profile.yaml"
        # Write invalid data that won't deserialize to EmployeeConfig
        profile.write_text("name: 123\nrole: [invalid]\nskills: not_a_list\n")

        result = config_mod.load_employee_configs()
        # Corrupt entry is skipped
        assert "00010" not in result


# ---------------------------------------------------------------------------
# load_employee_profile_yaml / save_employee_profile_yaml (lines 729-738)
# ---------------------------------------------------------------------------

class TestEmployeeProfileYaml:
    def test_load_missing_profile_returns_empty(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod
        monkeypatch.setattr(config_mod, "EMPLOYEES_DIR", tmp_path)
        result = config_mod.load_employee_profile_yaml("00099")
        assert result == {}

    def test_load_existing_profile(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod
        monkeypatch.setattr(config_mod, "EMPLOYEES_DIR", tmp_path)
        emp_dir = tmp_path / "00010"
        emp_dir.mkdir()
        (emp_dir / "profile.yaml").write_text("name: Alice\nrole: eng\n")
        result = config_mod.load_employee_profile_yaml("00010")
        assert result["name"] == "Alice"

    def test_save_profile(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod
        monkeypatch.setattr(config_mod, "EMPLOYEES_DIR", tmp_path)
        config_mod.save_employee_profile_yaml("00010", {"name": "Bob", "role": "Designer"})
        loaded = config_mod.load_employee_profile_yaml("00010")
        assert loaded["name"] == "Bob"


# ---------------------------------------------------------------------------
# load_assets — chat_yaml skip, malformed room (lines 809, 813-814)
# ---------------------------------------------------------------------------

class TestLoadAssetsEdgeCases:
    def test_skips_chat_yaml_and_malformed_room(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod
        tools_dir = tmp_path / "tools"
        rooms_dir = tmp_path / "rooms"
        tools_dir.mkdir()
        rooms_dir.mkdir()
        monkeypatch.setattr(config_mod, "TOOLS_DIR", tools_dir)
        monkeypatch.setattr(config_mod, "ROOMS_DIR", rooms_dir)

        # _chat.yaml should be skipped
        (rooms_dir / "room1_chat.yaml").write_text("history: []")
        # Malformed room (not a dict)
        (rooms_dir / "bad_room.yaml").write_text("- just a list")
        # Valid room
        yaml.dump({"capacity": 10}, open(rooms_dir / "good_room.yaml", "w"))

        tools, rooms = config_mod.load_assets()
        assert "room1_chat" not in rooms
        assert "bad_room" not in rooms
        assert "good_room" in rooms


# ---------------------------------------------------------------------------
# load_workflows — SOP overwrite warning (line 831)
# ---------------------------------------------------------------------------

class TestLoadWorkflows:
    def test_sop_overwrite_warning(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod
        wf_dir = tmp_path / "workflows"
        sop_dir = tmp_path / "sops"
        hr_sop_dir = tmp_path / "hr_sops"
        wf_dir.mkdir()
        sop_dir.mkdir()
        hr_sop_dir.mkdir()
        monkeypatch.setattr(config_mod, "WORKFLOWS_DIR", wf_dir)
        monkeypatch.setattr(config_mod, "SOP_DIR", sop_dir)
        monkeypatch.setattr(config_mod, "HR_SOP_DIR", hr_sop_dir)

        # Same name in both dirs → SOP overwrites workflow (with warning)
        (wf_dir / "onboarding.md").write_text("# Workflow version")
        (sop_dir / "onboarding.md").write_text("# SOP version")

        result = config_mod.load_workflows()
        assert "onboarding" in result
        assert "SOP version" in result["onboarding"]


# ---------------------------------------------------------------------------
# load_ex_employee_configs — corrupt ex-employee (lines 876-878)
# ---------------------------------------------------------------------------

class TestLoadExEmployeeConfigs:
    def test_skips_corrupt_ex_employee(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod
        monkeypatch.setattr(config_mod, "EX_EMPLOYEES_DIR", tmp_path)
        emp_dir = tmp_path / "00010"
        emp_dir.mkdir()
        (emp_dir / "profile.yaml").write_text("name: 123\nrole: [bad]\nskills: invalid\n")
        result = config_mod.load_ex_employee_configs()
        assert "00010" not in result


# ---------------------------------------------------------------------------
# load_custom_settings / save_custom_settings (lines 981, 986-992)
# ---------------------------------------------------------------------------

class TestCustomSettings:
    def test_load_missing_returns_empty(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod
        monkeypatch.setattr(config_mod, "EMPLOYEES_DIR", tmp_path)
        result = config_mod.load_custom_settings("00010")
        assert result == {}

    def test_load_existing(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod
        monkeypatch.setattr(config_mod, "EMPLOYEES_DIR", tmp_path)
        emp_dir = tmp_path / "00010"
        emp_dir.mkdir()
        (emp_dir / "settings.json").write_text('{"email": "test@example.com"}')
        result = config_mod.load_custom_settings("00010")
        assert result["email"] == "test@example.com"

    def test_save_merges(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod
        monkeypatch.setattr(config_mod, "EMPLOYEES_DIR", tmp_path)
        emp_dir = tmp_path / "00010"
        emp_dir.mkdir()
        (emp_dir / "settings.json").write_text('{"existing": true}')
        result = config_mod.save_custom_settings("00010", {"new_key": "val"})
        assert result["existing"] is True
        assert result["new_key"] == "val"


# ---------------------------------------------------------------------------
# _LazyEmployeeConfigs — all delegation methods (lines 1092-1130)
# ---------------------------------------------------------------------------

class TestLazyEmployeeConfigs:
    def _make_lazy(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod
        monkeypatch.setattr(config_mod, "EMPLOYEES_DIR", tmp_path)

        emp_dir = tmp_path / "00010"
        emp_dir.mkdir()
        yaml.dump(
            {"name": "Alice", "role": "Engineer", "skills": ["py"]},
            open(emp_dir / "profile.yaml", "w"),
        )
        return config_mod._LazyEmployeeConfigs()

    def test_iter(self, tmp_path, monkeypatch):
        lazy = self._make_lazy(tmp_path, monkeypatch)
        keys = list(lazy)
        assert "00010" in keys

    def test_items(self, tmp_path, monkeypatch):
        lazy = self._make_lazy(tmp_path, monkeypatch)
        items = list(lazy.items())
        assert len(items) == 1
        assert items[0][0] == "00010"

    def test_values(self, tmp_path, monkeypatch):
        lazy = self._make_lazy(tmp_path, monkeypatch)
        vals = list(lazy.values())
        assert len(vals) == 1

    def test_keys(self, tmp_path, monkeypatch):
        lazy = self._make_lazy(tmp_path, monkeypatch)
        keys = list(lazy.keys())
        assert "00010" in keys

    def test_len(self, tmp_path, monkeypatch):
        lazy = self._make_lazy(tmp_path, monkeypatch)
        assert len(lazy) == 1

    def test_bool(self, tmp_path, monkeypatch):
        lazy = self._make_lazy(tmp_path, monkeypatch)
        assert bool(lazy) is True

    def test_bool_empty(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod
        monkeypatch.setattr(config_mod, "EMPLOYEES_DIR", tmp_path / "empty")
        lazy = config_mod._LazyEmployeeConfigs()
        assert bool(lazy) is False

    def test_setitem_noop(self, tmp_path, monkeypatch):
        lazy = self._make_lazy(tmp_path, monkeypatch)
        lazy["foo"] = "bar"  # no-op, should not raise

    def test_delitem_noop(self, tmp_path, monkeypatch):
        lazy = self._make_lazy(tmp_path, monkeypatch)
        del lazy["anything"]  # no-op

    def test_pop_noop(self, tmp_path, monkeypatch):
        lazy = self._make_lazy(tmp_path, monkeypatch)
        result = lazy.pop("x")
        assert result is None

    def test_clear_noop(self, tmp_path, monkeypatch):
        lazy = self._make_lazy(tmp_path, monkeypatch)
        lazy.clear()  # no-op
        assert len(lazy) == 1  # still reads from disk

    def test_update_noop(self, tmp_path, monkeypatch):
        lazy = self._make_lazy(tmp_path, monkeypatch)
        lazy.update({"foo": "bar"})  # no-op
        assert len(lazy) == 1
