import pytest
from pathlib import Path


class TestAppendOfficeVibesToEnv:
    def test_enabled_writes_to_env(self, tmp_path):
        env_path = tmp_path / ".env"
        env_path.write_text("# test\nDEFAULT_API_PROVIDER=openrouter\n")
        from onemancompany.onboard import _append_office_vibes_to_env
        _append_office_vibes_to_env(env_path, True)
        content = env_path.read_text()
        assert "OFFICE_VIBES=1" in content

    def test_disabled_does_not_write(self, tmp_path):
        env_path = tmp_path / ".env"
        env_path.write_text("# test\n")
        from onemancompany.onboard import _append_office_vibes_to_env
        _append_office_vibes_to_env(env_path, False)
        content = env_path.read_text()
        assert "OFFICE_VIBES" not in content
