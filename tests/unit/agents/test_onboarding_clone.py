"""Tests for clone_talent_repo in onboarding.py."""
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from onemancompany.agents.onboarding import clone_talent_repo


def _make_fake_proc(side_effect):
    """Create a mock async subprocess that runs side_effect to simulate git clone."""
    proc = AsyncMock()
    proc.returncode = 0
    proc.communicate = AsyncMock(return_value=(b"", b""))

    async def _fake_create(*args, **kwargs):
        # args[0..N] are the command parts; find the target dir (last positional arg)
        cmd = list(args)
        side_effect(cmd)
        return proc

    return _fake_create


class TestCloneTalentRepo:
    @pytest.mark.asyncio
    async def test_single_talent_repo(self, tmp_path, monkeypatch):
        """Single-talent repo (profile.yaml at root) gets copied to talents/{id}/."""
        import onemancompany.agents.onboarding as onboarding_mod
        monkeypatch.setattr(onboarding_mod, "_TALENTS_CLONE_DIR", tmp_path)

        def fake_clone(cmd):
            # cmd is ["git", "clone", url, target_dir]
            clone_dir = Path(cmd[3])
            clone_dir.mkdir(parents=True, exist_ok=True)
            (clone_dir / "profile.yaml").write_text("name: test\nhosting: self\n")

        with patch("asyncio.create_subprocess_exec", new=_make_fake_proc(fake_clone)):
            result = await clone_talent_repo("https://git.example.com/repo.git", "test-talent")

        assert result == tmp_path / "test-talent"
        assert (tmp_path / "test-talent" / "profile.yaml").exists()

    @pytest.mark.asyncio
    async def test_multi_talent_repo(self, tmp_path, monkeypatch):
        """Multi-talent repo (subdirs with profile.yaml) gets flattened."""
        import onemancompany.agents.onboarding as onboarding_mod
        monkeypatch.setattr(onboarding_mod, "_TALENTS_CLONE_DIR", tmp_path)

        def fake_clone(cmd):
            clone_dir = Path(cmd[3])
            clone_dir.mkdir(parents=True, exist_ok=True)
            # Two sub-talents
            (clone_dir / "talent-a").mkdir()
            (clone_dir / "talent-a" / "profile.yaml").write_text("name: A\n")
            (clone_dir / "talent-b").mkdir()
            (clone_dir / "talent-b" / "profile.yaml").write_text("name: B\n")
            (clone_dir / "README.md").write_text("repo readme")

        with patch("asyncio.create_subprocess_exec", new=_make_fake_proc(fake_clone)):
            result = await clone_talent_repo("https://git.example.com/repo.git", "talent-a")

        assert (tmp_path / "talent-a" / "profile.yaml").exists()
        assert (tmp_path / "talent-b" / "profile.yaml").exists()
        assert result == tmp_path / "talent-a"
