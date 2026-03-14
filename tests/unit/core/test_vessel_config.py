"""Unit tests for core/vessel_config.py — VesselConfig loading & migration."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from onemancompany.core.vessel_config import (
    CapabilitiesConfig,
    ContextConfig,
    HooksConfig,
    LimitsConfig,
    PromptSection,
    RunnerConfig,
    VesselConfig,
    _convert_legacy_manifest,
    _load_default_vessel_config,
    load_vessel_config,
    migrate_agent_to_vessel,
    save_vessel_config,
)


# ---------------------------------------------------------------------------
# VesselConfig dataclass defaults
# ---------------------------------------------------------------------------

class TestVesselConfigDefaults:
    def test_default_runner(self):
        cfg = VesselConfig()
        assert cfg.runner.module == ""
        assert cfg.runner.class_name == ""

    def test_default_hooks(self):
        cfg = VesselConfig()
        assert cfg.hooks.module == ""
        assert cfg.hooks.pre_task == ""
        assert cfg.hooks.post_task == ""

    def test_default_limits(self):
        cfg = VesselConfig()
        assert cfg.limits.max_retries == 3
        assert cfg.limits.retry_delays == [5, 15, 30]
        assert cfg.limits.max_subtask_iterations == 3
        assert cfg.limits.max_subtask_depth == 2
        assert cfg.limits.task_timeout_seconds == 600

    def test_default_capabilities(self):
        cfg = VesselConfig()
        assert cfg.capabilities.sandbox is False
        assert cfg.capabilities.file_upload is False
        assert cfg.capabilities.websocket is False
        assert cfg.capabilities.image_generation is False

    def test_default_context(self):
        cfg = VesselConfig()
        assert cfg.context.prompt_sections == []
        assert cfg.context.inject_progress_log is True
        assert cfg.context.inject_task_history is True


# ---------------------------------------------------------------------------
# load_vessel_config
# ---------------------------------------------------------------------------

class TestLoadVesselConfig:
    def test_nonexistent_dir_returns_default(self, tmp_path):
        cfg = load_vessel_config(tmp_path / "nonexistent")
        assert cfg.limits.max_retries == 3
        assert cfg.capabilities.sandbox is False

    def test_load_from_vessel_yaml(self, tmp_path):
        vessel_dir = tmp_path / "vessel"
        vessel_dir.mkdir()
        data = {
            "runner": {"module": "my_runner", "class_name": "MyRunner"},
            "limits": {"max_retries": 5, "retry_delays": [10, 20]},
            "capabilities": {"image_generation": True},
        }
        with open(vessel_dir / "vessel.yaml", "w") as f:
            yaml.dump(data, f)

        cfg = load_vessel_config(tmp_path)
        assert cfg.runner.module == "my_runner"
        assert cfg.runner.class_name == "MyRunner"
        assert cfg.limits.max_retries == 5
        assert cfg.limits.retry_delays == [10, 20]
        assert cfg.capabilities.image_generation is True

    def test_load_from_agent_manifest_fallback(self, tmp_path):
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        manifest = {
            "runner": {"module": "old_runner", "class": "OldRunner"},
            "hooks": {"module": "my_hooks", "pre_task": "before", "post_task": "after"},
            "prompt_sections": [
                {"name": "guide", "file": "guide.md", "priority": 40},
            ],
        }
        with open(agent_dir / "manifest.yaml", "w") as f:
            yaml.dump(manifest, f)

        cfg = load_vessel_config(tmp_path)
        assert cfg.runner.module == "old_runner"
        assert cfg.runner.class_name == "OldRunner"
        assert cfg.hooks.module == "my_hooks"
        assert cfg.hooks.pre_task == "before"
        assert cfg.hooks.post_task == "after"
        assert len(cfg.context.prompt_sections) == 1
        assert cfg.context.prompt_sections[0].name == "guide"

    def test_vessel_yaml_takes_priority_over_agent(self, tmp_path):
        # Create both vessel/ and agent/
        vessel_dir = tmp_path / "vessel"
        vessel_dir.mkdir()
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()

        with open(vessel_dir / "vessel.yaml", "w") as f:
            yaml.dump({"limits": {"max_retries": 7}}, f)
        with open(agent_dir / "manifest.yaml", "w") as f:
            yaml.dump({"runner": {"module": "old", "class": "Old"}}, f)

        cfg = load_vessel_config(tmp_path)
        assert cfg.limits.max_retries == 7
        assert cfg.runner.module == ""  # vessel.yaml wins, no runner there

    def test_context_prompt_sections_parsed(self, tmp_path):
        vessel_dir = tmp_path / "vessel"
        vessel_dir.mkdir()
        data = {
            "context": {
                "prompt_sections": [
                    {"name": "persona", "file": "persona.md", "priority": 10},
                    {"name": "skills", "file": "skills.md", "priority": 30},
                ],
                "inject_progress_log": False,
            }
        }
        with open(vessel_dir / "vessel.yaml", "w") as f:
            yaml.dump(data, f)

        cfg = load_vessel_config(tmp_path)
        assert len(cfg.context.prompt_sections) == 2
        assert cfg.context.prompt_sections[0].name == "persona"
        assert cfg.context.prompt_sections[0].priority == 10
        assert cfg.context.inject_progress_log is False


# ---------------------------------------------------------------------------
# save_vessel_config
# ---------------------------------------------------------------------------

class TestSaveVesselConfig:
    def test_save_and_reload(self, tmp_path):
        cfg = VesselConfig(
            runner=RunnerConfig(module="r", class_name="R"),
            hooks=HooksConfig(module="h", pre_task="pre", post_task="post"),
            context=ContextConfig(
                prompt_sections=[PromptSection(name="x", file="x.md", priority=20)],
                inject_progress_log=False,
            ),
            limits=LimitsConfig(max_retries=10),
            capabilities=CapabilitiesConfig(image_generation=True),
        )
        save_vessel_config(tmp_path, cfg)

        assert (tmp_path / "vessel" / "vessel.yaml").exists()

        loaded = load_vessel_config(tmp_path)
        assert loaded.runner.module == "r"
        assert loaded.runner.class_name == "R"
        assert loaded.hooks.pre_task == "pre"
        assert loaded.context.inject_progress_log is False
        assert len(loaded.context.prompt_sections) == 1
        assert loaded.limits.max_retries == 10
        assert loaded.capabilities.image_generation is True


# ---------------------------------------------------------------------------
# _convert_legacy_manifest
# ---------------------------------------------------------------------------

class TestConvertLegacyManifest:
    def test_basic_conversion(self):
        manifest = {
            "runner": {"module": "custom", "class": "CustomRunner"},
            "hooks": {"module": "hooks", "pre_task": "before"},
            "prompt_sections": [
                {"name": "guide", "file": "prompt_sections/guide.md", "priority": 40},
            ],
        }
        cfg = _convert_legacy_manifest(manifest)
        assert cfg.runner.module == "custom"
        assert cfg.runner.class_name == "CustomRunner"
        assert cfg.hooks.module == "hooks"
        assert cfg.hooks.pre_task == "before"
        assert len(cfg.context.prompt_sections) == 1

    def test_empty_manifest(self):
        cfg = _convert_legacy_manifest({})
        assert cfg.runner.module == ""
        assert cfg.hooks.module == ""
        assert cfg.context.prompt_sections == []


# ---------------------------------------------------------------------------
# migrate_agent_to_vessel
# ---------------------------------------------------------------------------

class TestMigrateAgentToVessel:
    def test_no_agent_dir(self, tmp_path):
        assert migrate_agent_to_vessel(tmp_path) is False

    def test_already_migrated(self, tmp_path):
        vessel_dir = tmp_path / "vessel"
        vessel_dir.mkdir()
        (vessel_dir / "vessel.yaml").write_text("runner: {}")
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        (agent_dir / "manifest.yaml").write_text("runner: {module: old, class: Old}")
        assert migrate_agent_to_vessel(tmp_path) is False

    def test_migration_creates_vessel_yaml(self, tmp_path):
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        manifest = {
            "runner": {"module": "custom", "class": "CustomRunner"},
            "prompt_sections": [{"name": "guide", "file": "prompt_sections/guide.md"}],
        }
        with open(agent_dir / "manifest.yaml", "w") as f:
            yaml.dump(manifest, f)

        # Create prompt_sections/ in agent/
        ps_dir = agent_dir / "prompt_sections"
        ps_dir.mkdir()
        (ps_dir / "guide.md").write_text("# Guide content")

        # Create runner .py
        (agent_dir / "custom.py").write_text("class CustomRunner: pass")

        assert migrate_agent_to_vessel(tmp_path) is True
        assert (tmp_path / "vessel" / "vessel.yaml").exists()
        assert (tmp_path / "vessel" / "prompt_sections" / "guide.md").exists()
        assert (tmp_path / "vessel" / "custom.py").exists()
        # Agent dir should still exist
        assert agent_dir.exists()

    def test_migration_idempotent(self, tmp_path):
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        with open(agent_dir / "manifest.yaml", "w") as f:
            yaml.dump({"runner": {"module": "m", "class": "C"}}, f)

        assert migrate_agent_to_vessel(tmp_path) is True
        assert migrate_agent_to_vessel(tmp_path) is False  # already done


# ---------------------------------------------------------------------------
# _load_default_vessel_config
# ---------------------------------------------------------------------------

class TestLoadDefaultVesselConfig:
    def test_default_config_loads(self):
        cfg = _load_default_vessel_config()
        assert cfg.limits.max_retries == 3
        assert cfg.capabilities.sandbox is False
        assert cfg.capabilities.file_upload is False
