"""Tests for clone_talent_repo in onboarding.py."""
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from onemancompany.agents.onboarding import clone_talent_repo


class TestCloneTalentRepo:
    @pytest.mark.asyncio
    async def test_clone_new_repo(self, tmp_path, monkeypatch):
        """Clones repo when directory doesn't exist."""
        import onemancompany.agents.onboarding as onboarding_mod
        monkeypatch.setattr(onboarding_mod, "_LEGACY_TALENTS_DIR", tmp_path)

        with patch("onemancompany.agents.onboarding.subprocess") as mock_sub:
            mock_sub.run = MagicMock()
            result = await clone_talent_repo("https://git.example.com/repo.git", "test-talent")

        assert result == tmp_path / "test-talent"
        mock_sub.run.assert_called_once()
        args = mock_sub.run.call_args
        assert args[0][0][0] == "git"
        assert args[0][0][1] == "clone"

    @pytest.mark.asyncio
    async def test_pull_existing_repo(self, tmp_path, monkeypatch):
        """Does git pull when directory already exists."""
        import onemancompany.agents.onboarding as onboarding_mod
        monkeypatch.setattr(onboarding_mod, "_LEGACY_TALENTS_DIR", tmp_path)

        (tmp_path / "existing-talent").mkdir()

        with patch("onemancompany.agents.onboarding.subprocess") as mock_sub:
            mock_sub.run = MagicMock()
            result = await clone_talent_repo("https://git.example.com/repo.git", "existing-talent")

        assert result == tmp_path / "existing-talent"
        mock_sub.run.assert_called_once()
        args = mock_sub.run.call_args
        assert args[0][0][1] == "-C"
