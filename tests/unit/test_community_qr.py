from pathlib import Path
import tomllib


ROOT = Path(__file__).parents[2]
JOIN_DIR = ROOT / "docs" / "join"
PROJECT_REF = "ylxbdvgwmiakbovjdfsq"
BUCKET = "community-assets"
OBJECT_NAME = "wechat-group.png"
ADMIN_EMAIL = "yuzxfred@gmail.com"


def test_public_page_loads_the_single_supabase_qr_object() -> None:
    html = (JOIN_DIR / "index.html").read_text(encoding="utf-8")

    expected_url = (
        f"https://{PROJECT_REF}.supabase.co/storage/v1/object/public/"
        f"{BUCKET}/{OBJECT_NAME}"
    )
    assert expected_url in html
    assert "Date.now()" in html
    assert 'src="wechat-group.png"' not in html


def test_admin_page_uses_user_auth_and_fixed_upload_target() -> None:
    html = (JOIN_DIR / "admin" / "index.html").read_text(encoding="utf-8")

    assert f"https://{PROJECT_REF}.supabase.co" in html
    assert "/auth/v1/token?grant_type=password" in html
    assert f"{BUCKET}" in html
    assert f"{OBJECT_NAME}" in html
    assert "'x-upsert': 'true'" in html
    assert "service_role" not in html
    assert "SERVICE_ROLE" not in html


def test_storage_migration_enforces_admin_and_object_boundaries() -> None:
    migrations = list((ROOT / "supabase" / "migrations").glob("*_community_qr_storage.sql"))
    assert len(migrations) == 1
    sql = migrations[0].read_text(encoding="utf-8")

    assert f"'{BUCKET}'" in sql
    assert f"name = '{OBJECT_NAME}'" in sql
    assert f"auth.jwt() ->> 'email' = '{ADMIN_EMAIL}'" in sql
    assert "allowed_mime_types" in sql
    assert "file_size_limit" in sql
    assert "service_role" not in sql


def test_group_qr_is_not_stored_in_git_anymore() -> None:
    assert not (JOIN_DIR / OBJECT_NAME).exists()


def test_admin_only_project_disables_public_signups() -> None:
    with (ROOT / "supabase" / "config.toml").open("rb") as config_file:
        config = tomllib.load(config_file)

    assert config["auth"]["enable_signup"] is False
    assert config["auth"]["email"]["enable_signup"] is False


def test_maintenance_docs_use_the_complete_pages_admin_url() -> None:
    docs = (JOIN_DIR / "README.md").read_text(encoding="utf-8")

    assert "https://1mancompany.github.io/OneManCompany/join/admin/" in docs
    assert "open `/join/admin/`" not in docs
