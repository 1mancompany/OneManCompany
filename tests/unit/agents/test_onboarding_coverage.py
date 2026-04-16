"""Coverage tests for agents/onboarding.py — missing lines."""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml


def _setup(tmp_path, monkeypatch):
    import onemancompany.core.config as config_mod
    import onemancompany.agents.onboarding as ob_mod
    monkeypatch.setattr(config_mod, "EMPLOYEES_DIR", tmp_path / "employees")
    monkeypatch.setattr(ob_mod, "EMPLOYEES_DIR", tmp_path / "employees")
    (tmp_path / "employees").mkdir(parents=True, exist_ok=True)
    return tmp_path


# ---------------------------------------------------------------------------
# _load_nickname_pool — file not found (line 115-116)
# ---------------------------------------------------------------------------

class TestLoadNicknamePool:
    def test_pool_file_not_found(self, tmp_path, monkeypatch):
        import onemancompany.agents.onboarding as ob_mod
        import onemancompany.core.config as config_mod
        monkeypatch.setattr(ob_mod, "_NICKNAMES_FILE", tmp_path / "nonexistent.txt")
        monkeypatch.setattr(config_mod, "DATA_ROOT", tmp_path / "no_data")
        pool = ob_mod._load_nickname_pool()
        assert pool == []

    def test_pool_file_exists(self, tmp_path, monkeypatch):
        import onemancompany.agents.onboarding as ob_mod
        import onemancompany.core.config as config_mod
        f = tmp_path / "nicknames.txt"
        f.write_text("风云\n雷电\n\n")
        monkeypatch.setattr(ob_mod, "_NICKNAMES_FILE", f)
        monkeypatch.setattr(config_mod, "DATA_ROOT", tmp_path / "no_data")
        pool = ob_mod._load_nickname_pool()
        assert "风云" in pool
        assert "雷电" in pool

    def test_pick_nickname_exhausted(self, monkeypatch):
        """Cover line 131: all pool candidates collide."""
        import onemancompany.agents.onboarding as ob_mod
        monkeypatch.setattr(ob_mod, "_load_nickname_pool", lambda: ["AB"])
        result = ob_mod._pick_nickname(2, {"AB"})
        # Should generate from random wuxia chars or return empty
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# generate_nickname — no nickname found (line 150)
# ---------------------------------------------------------------------------

class TestGenerateNickname:
    @pytest.mark.asyncio
    async def test_no_unique_nickname(self, monkeypatch):
        import onemancompany.agents.onboarding as ob_mod
        monkeypatch.setattr(ob_mod, "_pick_nickname", lambda *a, **kw: "")
        monkeypatch.setattr(ob_mod, "_get_existing_nicknames", lambda: set())
        result = await ob_mod.generate_nickname("Test", "Engineer")
        assert result == ""


# ---------------------------------------------------------------------------
# install_talent_vessel_config (lines 505, 511-532)
# ---------------------------------------------------------------------------

class TestInstallVesselConfig:
    def test_already_installed(self, tmp_path):
        from onemancompany.agents.onboarding import install_talent_vessel_config
        emp_dir = tmp_path / "00010"
        vessel_dir = emp_dir / "vessel"
        vessel_dir.mkdir(parents=True)
        (vessel_dir / "vessel.yaml").write_text("already: true")
        install_talent_vessel_config(tmp_path / "talent", str(emp_dir), "00010")
        # Should be a no-op
        assert yaml.safe_load((vessel_dir / "vessel.yaml").read_text()) == {"already": True}

    def test_install_from_talent_vessel(self, tmp_path):
        """Cover lines 511-532: copy from talent vessel dir."""
        from onemancompany.agents.onboarding import install_talent_vessel_config
        emp_dir = tmp_path / "00010"
        emp_dir.mkdir(parents=True)
        talent_dir = tmp_path / "talent"
        talent_vessel = talent_dir / "vessel"
        talent_vessel.mkdir(parents=True)
        (talent_vessel / "vessel.yaml").write_text("runner:\n  module: my_runner\nhooks:\n  module: my_hooks\n")
        # Create prompt_sections
        ps = talent_vessel / "prompt_sections"
        ps.mkdir()
        (ps / "custom.md").write_text("content")
        # Create runner module
        (talent_vessel / "my_runner.py").write_text("# runner")
        # Create hooks module
        (talent_vessel / "my_hooks.py").write_text("# hooks")
        install_talent_vessel_config(talent_dir, str(emp_dir), "00010")
        assert (emp_dir / "vessel" / "vessel.yaml").exists()
        assert (emp_dir / "vessel" / "prompt_sections" / "custom.md").exists()
        assert (emp_dir / "vessel" / "my_runner.py").exists()
        assert (emp_dir / "vessel" / "my_hooks.py").exists()

    def test_install_default(self, tmp_path):
        """Cover line 536: fallback to default config."""
        from onemancompany.agents.onboarding import install_talent_vessel_config
        emp_dir = tmp_path / "00010"
        emp_dir.mkdir(parents=True)
        talent_dir = tmp_path / "talent"
        talent_dir.mkdir()
        # No vessel dir in talent — falls back to default
        with patch("onemancompany.core.vessel_config._load_default_vessel_config") as mock_default, \
             patch("onemancompany.core.vessel_config.save_vessel_config"):
            mock_default.return_value = MagicMock()
            install_talent_vessel_config(talent_dir, str(emp_dir), "00010")


# ---------------------------------------------------------------------------
# resolve_talent_dir (lines 558, 591, 597)
# ---------------------------------------------------------------------------

class TestResolveTalentDir:
    def test_empty_talent_id(self):
        from onemancompany.agents.onboarding import resolve_talent_dir
        assert resolve_talent_dir("") is None

    def test_resolve_builtin(self, tmp_path, monkeypatch):
        import onemancompany.agents.onboarding as ob_mod
        builtin = tmp_path / "builtin"
        (builtin / "my_talent").mkdir(parents=True)
        monkeypatch.setattr(ob_mod, "_TALENTS_CLONE_DIR", tmp_path / "clone")
        monkeypatch.setattr(ob_mod, "_BUILTIN_TALENTS_DIR", builtin)
        result = ob_mod.resolve_talent_dir("my_talent")
        assert result == builtin / "my_talent"


# ---------------------------------------------------------------------------
# clone_talent_repo (lines 591-619)
# ---------------------------------------------------------------------------

class TestCloneTalentRepo:
    @pytest.mark.asyncio
    async def test_clone_single_talent(self, tmp_path, monkeypatch):
        import onemancompany.agents.onboarding as ob_mod
        clone_dir = tmp_path / "clone"
        monkeypatch.setattr(ob_mod, "_TALENTS_CLONE_DIR", clone_dir)

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))

        # Create the temp clone dir with a profile.yaml
        created_dirs = []
        real_mkdtemp = __import__("tempfile").mkdtemp

        def patched_mkdtemp(**kwargs):
            d = real_mkdtemp(**kwargs)
            created_dirs.append(d)
            Path(d).mkdir(parents=True, exist_ok=True)
            (Path(d) / "profile.yaml").write_text("id: my_talent\n")
            return d

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc), \
             patch("tempfile.mkdtemp", side_effect=patched_mkdtemp):
            result = await ob_mod.clone_talent_repo("https://github.com/test/repo", "my_talent")
        assert result == clone_dir / "my_talent"

    @pytest.mark.asyncio
    async def test_clone_multi_talent(self, tmp_path, monkeypatch):
        import onemancompany.agents.onboarding as ob_mod
        clone_dir = tmp_path / "clone"
        monkeypatch.setattr(ob_mod, "_TALENTS_CLONE_DIR", clone_dir)

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))

        created_dirs = []
        real_mkdtemp = __import__("tempfile").mkdtemp

        def patched_mkdtemp(**kwargs):
            d = real_mkdtemp(**kwargs)
            created_dirs.append(d)
            p = Path(d)
            p.mkdir(parents=True, exist_ok=True)
            # Multi-talent: no root profile.yaml, but sub dirs have it
            sub = p / "sub_talent"
            sub.mkdir()
            (sub / "profile.yaml").write_text("id: sub_talent\n")
            return d

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc), \
             patch("tempfile.mkdtemp", side_effect=patched_mkdtemp):
            result = await ob_mod.clone_talent_repo("https://github.com/test/repo", "sub_talent")
        assert (clone_dir / "sub_talent").exists()

    @pytest.mark.asyncio
    async def test_clone_git_failure(self, tmp_path, monkeypatch):
        import subprocess
        import onemancompany.agents.onboarding as ob_mod
        clone_dir = tmp_path / "clone"
        monkeypatch.setattr(ob_mod, "_TALENTS_CLONE_DIR", clone_dir)

        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(return_value=(b"", b"error"))

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc):
            with pytest.raises(subprocess.CalledProcessError):
                await ob_mod.clone_talent_repo("https://bad/repo", "talent")


# ---------------------------------------------------------------------------
# _inject_default_skills — EA skills + sync logic (lines 637-665)
# ---------------------------------------------------------------------------

class TestInjectDefaultSkills:
    def test_inject_with_ea_skills(self, tmp_path, monkeypatch):
        import onemancompany.agents.onboarding as ob_mod
        monkeypatch.setattr(ob_mod, "_DEFAULT_SKILLS_DIR", tmp_path / "default_skills")

        # Create task_lifecycle (default) and project-brainstorming (EA-only)
        for skill_name in ("task_lifecycle", "project-brainstorming"):
            src_dir = tmp_path / "default_skills" / skill_name
            src_dir.mkdir(parents=True)
            (src_dir / "SKILL.md").write_text(f"---\nname: {skill_name}\n---\nContent")

        skills_dir = tmp_path / "00010" / "skills"
        skills_dir.mkdir(parents=True)

        with patch("onemancompany.core.config.EA_ID", "00010"):
            ob_mod._inject_default_skills(skills_dir, employee_id="00010")
        assert (skills_dir / "task_lifecycle" / "SKILL.md").exists()
        assert (skills_dir / "project-brainstorming" / "SKILL.md").exists()

    def test_inject_sync_existing(self, tmp_path, monkeypatch):
        """Cover lines 647-665: sync SKILL.md for existing skill + subdir sync."""
        import onemancompany.agents.onboarding as ob_mod
        monkeypatch.setattr(ob_mod, "_DEFAULT_SKILLS_DIR", tmp_path / "default_skills")

        src_dir = tmp_path / "default_skills" / "task_lifecycle"
        src_dir.mkdir(parents=True)
        (src_dir / "SKILL.md").write_text("UPDATED CONTENT")
        hooks = src_dir / "hooks"
        hooks.mkdir()
        (hooks / "hook.py").write_text("# hook")

        skills_dir = tmp_path / "skills"
        dst_dir = skills_dir / "task_lifecycle"
        dst_dir.mkdir(parents=True)
        (dst_dir / "SKILL.md").write_text("OLD CONTENT")

        ob_mod._inject_default_skills(skills_dir, employee_id="00020")
        assert (dst_dir / "SKILL.md").read_text() == "UPDATED CONTENT"
        assert (dst_dir / "hooks" / "hook.py").exists()


# ---------------------------------------------------------------------------
# _assign_default_avatar (lines 673-694)
# ---------------------------------------------------------------------------

class TestAssignDefaultAvatar:
    def test_already_has_avatar(self, tmp_path):
        from onemancompany.agents.onboarding import _assign_default_avatar
        emp_dir = tmp_path / "00010"
        emp_dir.mkdir()
        (emp_dir / "avatar.png").write_text("img")
        _assign_default_avatar(emp_dir, "00010")

    def test_no_avatars_dir(self, tmp_path, monkeypatch):
        from onemancompany.agents.onboarding import _assign_default_avatar
        import onemancompany.core.config as config_mod
        monkeypatch.setattr(config_mod, "COMPANY_DIR", tmp_path / "company")
        emp_dir = tmp_path / "00010"
        emp_dir.mkdir()
        _assign_default_avatar(emp_dir, "00010")

    def test_no_avatar_files(self, tmp_path, monkeypatch):
        from onemancompany.agents.onboarding import _assign_default_avatar
        import onemancompany.core.config as config_mod
        monkeypatch.setattr(config_mod, "COMPANY_DIR", tmp_path / "company")
        avatars_dir = tmp_path / "company" / "human_resource" / "avatars"
        avatars_dir.mkdir(parents=True)
        emp_dir = tmp_path / "00010"
        emp_dir.mkdir()
        _assign_default_avatar(emp_dir, "00010")

    def test_assign_avatar(self, tmp_path, monkeypatch):
        from onemancompany.agents.onboarding import _assign_default_avatar
        import onemancompany.core.config as config_mod
        monkeypatch.setattr(config_mod, "COMPANY_DIR", tmp_path / "company")
        avatars_dir = tmp_path / "company" / "human_resource" / "avatars"
        avatars_dir.mkdir(parents=True)
        (avatars_dir / "avatar1.png").write_text("img1")
        emp_dir = tmp_path / "00010"
        emp_dir.mkdir()
        _assign_default_avatar(emp_dir, "00010")
        assert (emp_dir / "avatar.png").exists()


# ---------------------------------------------------------------------------
# copy_talent_assets — skills (lines 733-737)
# ---------------------------------------------------------------------------

class TestCopyTalentAssets:
    def test_copy_legacy_md_skills(self, tmp_path):
        from onemancompany.agents.onboarding import copy_talent_assets
        talent_dir = tmp_path / "talent"
        talent_dir.mkdir()
        skills_dir = talent_dir / "skills"
        skills_dir.mkdir()
        (skills_dir / "legacy_skill.md").write_text("---\nname: legacy\n---\nContent")

        emp_dir = tmp_path / "employee"
        emp_dir.mkdir()

        with patch("onemancompany.agents.onboarding.register_tool_user"):
            copy_talent_assets(talent_dir, emp_dir)

        assert (emp_dir / "skills" / "legacy_skill" / "SKILL.md").exists()

    def test_copy_nonexistent_talent(self, tmp_path):
        from onemancompany.agents.onboarding import copy_talent_assets
        emp_dir = tmp_path / "employee"
        emp_dir.mkdir()
        copy_talent_assets(tmp_path / "nonexistent", emp_dir)  # should be no-op


# ---------------------------------------------------------------------------
# copy_talent_assets — tools with manifest (lines 755-770)
# ---------------------------------------------------------------------------

class TestCopyTalentTools:
    def test_copy_tools_with_manifest(self, tmp_path, monkeypatch):
        from onemancompany.agents.onboarding import copy_talent_assets
        import onemancompany.agents.onboarding as ob_mod
        tools_dir = tmp_path / "assets_tools"
        tools_dir.mkdir(parents=True)
        monkeypatch.setattr(ob_mod, "TOOLS_DIR", tools_dir)

        talent_dir = tmp_path / "talent"
        talent_dir.mkdir()
        talent_tools = talent_dir / "tools"
        talent_tools.mkdir()
        (talent_tools / "manifest.yaml").write_text("custom_tools:\n  - my_custom_tool\n")
        tool_subdir = talent_tools / "my_tool"
        tool_subdir.mkdir()
        (tool_subdir / "tool.yaml").write_text("name: my_tool\n")
        # Also add a loose config file
        (talent_tools / "config.yaml").write_text("key: val")

        emp_dir = tmp_path / "employee"
        emp_dir.mkdir()

        with patch("onemancompany.agents.onboarding.register_tool_user"):
            copy_talent_assets(talent_dir, emp_dir)

        assert (tools_dir / "my_tool").exists()
        assert (emp_dir / "tools" / "config.yaml").exists()


# ---------------------------------------------------------------------------
# copy_talent_assets — persona from profile.yaml (lines 777-791)
# ---------------------------------------------------------------------------

class TestCopyTalentPersona:
    def test_persona_from_profile(self, tmp_path):
        from onemancompany.agents.onboarding import copy_talent_assets  # noqa: F811
        talent_dir = tmp_path / "talent"
        talent_dir.mkdir()
        (talent_dir / "profile.yaml").write_text("system_prompt_template: 'I am a robot'\n")

        emp_dir = tmp_path / "employee"
        emp_dir.mkdir()

        with patch("onemancompany.agents.onboarding.register_tool_user"):
            copy_talent_assets(talent_dir, emp_dir)

        assert (emp_dir / "prompts" / "talent_persona.md").exists()

    def test_persona_from_prompts_dir(self, tmp_path):
        from onemancompany.agents.onboarding import copy_talent_assets  # noqa: F811
        talent_dir = tmp_path / "talent"
        talent_dir.mkdir()
        prompts = talent_dir / "prompts"
        prompts.mkdir()
        (prompts / "talent_persona.md").write_text("Custom persona")

        emp_dir = tmp_path / "employee"
        emp_dir.mkdir()

        with patch("onemancompany.agents.onboarding.register_tool_user"):
            copy_talent_assets(talent_dir, emp_dir)

        assert (emp_dir / "prompts" / "talent_persona.md").read_text() == "Custom persona"


# ---------------------------------------------------------------------------
# copy_talent_assets — CLAUDE.md (line 798)
# ---------------------------------------------------------------------------

class TestCopyClaudeMd:
    def test_claude_md_from_talent(self, tmp_path):
        from onemancompany.agents.onboarding import copy_talent_assets  # noqa: F811
        talent_dir = tmp_path / "talent"
        talent_dir.mkdir()
        (talent_dir / "CLAUDE.md").write_text("# Custom Claude MD")

        emp_dir = tmp_path / "employee"
        emp_dir.mkdir()

        with patch("onemancompany.agents.onboarding.register_tool_user"):
            copy_talent_assets(talent_dir, emp_dir)

        assert (emp_dir / "CLAUDE.md").read_text() == "# Custom Claude MD"


# ---------------------------------------------------------------------------
# execute_hire — launch/heartbeat script copy (lines 1021-1031)
# ---------------------------------------------------------------------------

