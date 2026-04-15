"""Unit tests for onemancompany.core.oauth — 100% coverage."""

import json
import os
import time
import threading
from io import BytesIO
from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock

import pytest

import onemancompany.core.oauth as oauth_mod
from onemancompany.core.oauth import (
    OAuthServiceConfig,
    _token_cache_path,
    _load_tokens,
    _save_tokens,
    _generate_pkce,
    _http_post,
    _refresh_token,
    get_oauth_token,
    get_oauth_header,
    _exchange_code,
    _run_callback_server,
    _trigger_oauth_popup,
    ensure_oauth_token,
    request_credentials,
    _OAuthCallbackHandler,
    _OAuthFlowsSnapshot,
    _active_flows,
    _pending_credentials,
    _flow_lock,
)


TEST_CONFIG = OAuthServiceConfig(
    service_name="test_svc",
    authorize_url="https://example.com/auth",
    token_url="https://example.com/token",
    scopes="read write",
    client_id_env="TEST_CLIENT_ID",
    client_secret_env="TEST_CLIENT_SECRET",
    redirect_port=19999,
    token_lifetime_buffer=60,
)


@pytest.fixture(autouse=True)
def clean_flows():
    """Clear active flows and pending credentials around each test."""
    _active_flows.clear()
    _pending_credentials.clear()
    yield
    _active_flows.clear()
    _pending_credentials.clear()


# ── _token_cache_path ───────────────────────────────────────

def test_token_cache_path(tmp_path):
    with patch.object(oauth_mod, "_TOKEN_DIR", tmp_path / "cache"):
        path = _token_cache_path("my_svc")
        assert path.name == "my_svc_tokens.json"
        assert path.parent.exists()


# ── _load_tokens ────────────────────────────────────────────

def test_load_tokens_no_file(tmp_path):
    with patch.object(oauth_mod, "_TOKEN_DIR", tmp_path):
        result = _load_tokens("no_such")
    assert result == {}


def test_load_tokens_valid_file(tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    token_file = cache_dir / "svc_tokens.json"
    token_file.write_text('{"access_token": "abc"}')

    with patch.object(oauth_mod, "_TOKEN_DIR", cache_dir), \
         patch("onemancompany.core.oauth.read_text_utf", return_value='{"access_token": "abc"}'):
        result = _load_tokens("svc")
    assert result == {"access_token": "abc"}


def test_load_tokens_corrupt_file(tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    token_file = cache_dir / "bad_tokens.json"
    token_file.write_text("not json")

    with patch.object(oauth_mod, "_TOKEN_DIR", cache_dir), \
         patch("onemancompany.core.oauth.read_text_utf", side_effect=Exception("parse error")):
        result = _load_tokens("bad")
    assert result == {}


# ── _save_tokens ────────────────────────────────────────────

def test_save_tokens(tmp_path):
    with patch.object(oauth_mod, "_TOKEN_DIR", tmp_path), \
         patch("onemancompany.core.oauth.write_text_utf") as mock_write:
        _save_tokens("svc", {"access_token": "xyz"})
        mock_write.assert_called_once()
        written = json.loads(mock_write.call_args[0][1])
        assert written["access_token"] == "xyz"


# ── _generate_pkce ──────────────────────────────────────────

def test_generate_pkce():
    verifier, challenge = _generate_pkce()
    assert len(verifier) <= 128
    assert len(challenge) > 0
    # Challenge should be URL-safe base64 without padding
    assert "=" not in challenge


# ── _http_post ──────────────────────────────────────────────

def test_http_post_success():
    mock_resp = MagicMock()
    mock_resp.read.return_value = b'{"access_token": "tok"}'
    mock_resp.status = 200
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_resp):
        status, body = _http_post("https://example.com/token", {"key": "val"})

    assert status == 200
    assert body["access_token"] == "tok"


def test_http_post_success_non_json():
    mock_resp = MagicMock()
    mock_resp.read.return_value = b"not json at all"
    mock_resp.status = 200
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_resp):
        status, body = _http_post("https://example.com/token", {"key": "val"})

    assert status == 200
    assert "raw" in body


def test_http_post_http_error_json():
    import urllib.error
    error = urllib.error.HTTPError("url", 400, "Bad", {}, BytesIO(b'{"error": "invalid"}'))

    with patch("urllib.request.urlopen", side_effect=error):
        status, body = _http_post("https://example.com/token", {"key": "val"})

    assert status == 400
    assert body["error"] == "invalid"


def test_http_post_http_error_non_json():
    import urllib.error
    error = urllib.error.HTTPError("url", 500, "Server Error", {}, BytesIO(b"plain text"))

    with patch("urllib.request.urlopen", side_effect=error):
        status, body = _http_post("https://example.com/token", {"key": "val"})

    assert status == 500
    assert "raw" in body


# ── _refresh_token ──────────────────────────────────────────

def test_refresh_token_no_refresh_token():
    with patch("onemancompany.core.oauth._load_tokens", return_value={}):
        result = _refresh_token(TEST_CONFIG)
    assert result is None


def test_refresh_token_missing_client_id():
    with patch("onemancompany.core.oauth._load_tokens",
               return_value={"refresh_token": "rt"}), \
         patch.dict(os.environ, {}, clear=True):
        result = _refresh_token(TEST_CONFIG)
    assert result is None


def test_refresh_token_success():
    cache = {"refresh_token": "rt", "client_id": "cid", "client_secret": "csec"}
    with patch("onemancompany.core.oauth._load_tokens", return_value=cache), \
         patch("onemancompany.core.oauth._http_post", return_value=(200, {
             "access_token": "new_at", "refresh_token": "new_rt", "expires_in": 900, "scope": "read",
         })), \
         patch("onemancompany.core.oauth._save_tokens") as mock_save:
        result = _refresh_token(TEST_CONFIG)

    assert result is not None
    assert result["access_token"] == "new_at"
    mock_save.assert_called_once()


def test_refresh_token_http_failure():
    cache = {"refresh_token": "rt", "client_id": "cid", "client_secret": "csec"}
    with patch("onemancompany.core.oauth._load_tokens", return_value=cache), \
         patch("onemancompany.core.oauth._http_post", return_value=(401, {"error": "invalid"})):
        result = _refresh_token(TEST_CONFIG)
    assert result is None


def test_refresh_token_uses_env_vars():
    """Falls back to env vars for client_id/secret."""
    cache = {"refresh_token": "rt"}
    with patch("onemancompany.core.oauth._load_tokens", return_value=cache), \
         patch.dict(os.environ, {"TEST_CLIENT_ID": "env_cid", "TEST_CLIENT_SECRET": "env_csec"}), \
         patch("onemancompany.core.oauth._http_post", return_value=(200, {
             "access_token": "at", "expires_in": 900,
         })), \
         patch("onemancompany.core.oauth._save_tokens"):
        result = _refresh_token(TEST_CONFIG)
    assert result is not None


# ── get_oauth_token ─────────────────────────────────────────

def test_get_oauth_token_cached():
    cache = {"access_token": "cached", "expires_at": time.time() + 1000}
    with patch("onemancompany.core.oauth._load_tokens", return_value=cache):
        result = get_oauth_token(TEST_CONFIG)
    assert result == "cached"


def test_get_oauth_token_expired_refresh_succeeds():
    cache = {"access_token": "old", "expires_at": time.time() - 100}
    with patch("onemancompany.core.oauth._load_tokens", return_value=cache), \
         patch("onemancompany.core.oauth._refresh_token", return_value={"access_token": "refreshed"}):
        result = get_oauth_token(TEST_CONFIG)
    assert result == "refreshed"


def test_get_oauth_token_no_token():
    with patch("onemancompany.core.oauth._load_tokens", return_value={}), \
         patch("onemancompany.core.oauth._refresh_token", return_value=None):
        result = get_oauth_token(TEST_CONFIG)
    assert result is None


# ── get_oauth_header ────────────────────────────────────────

def test_get_oauth_header_with_token():
    with patch("onemancompany.core.oauth.get_oauth_token", return_value="tok123"):
        result = get_oauth_header(TEST_CONFIG)
    assert result == {"Authorization": "Bearer tok123"}


def test_get_oauth_header_no_token():
    with patch("onemancompany.core.oauth.get_oauth_token", return_value=None):
        result = get_oauth_header(TEST_CONFIG)
    assert result == {}


# ── _OAuthCallbackHandler ──────────────────────────────────

def test_callback_handler_with_code():
    handler = MagicMock(spec=_OAuthCallbackHandler)
    handler.path = "/callback?code=AUTH_CODE&state=s"
    handler.wfile = BytesIO()
    handler.send_response = MagicMock()
    handler.send_header = MagicMock()
    handler.end_headers = MagicMock()

    _OAuthCallbackHandler._config = TEST_CONFIG
    _OAuthCallbackHandler._verifier = "v"

    with patch("onemancompany.core.oauth._exchange_code", return_value={"access_token": "at"}):
        _OAuthCallbackHandler.do_GET(handler)

    assert _OAuthCallbackHandler.auth_code == "AUTH_CODE"
    handler.send_response.assert_called_with(200)


def test_callback_handler_with_code_exchange_error():
    handler = MagicMock(spec=_OAuthCallbackHandler)
    handler.path = "/callback?code=CODE"
    handler.wfile = BytesIO()
    handler.send_response = MagicMock()
    handler.send_header = MagicMock()
    handler.end_headers = MagicMock()

    _OAuthCallbackHandler._config = TEST_CONFIG
    _OAuthCallbackHandler._verifier = "v"

    with patch("onemancompany.core.oauth._exchange_code", return_value={"error": "failed"}):
        _OAuthCallbackHandler.do_GET(handler)

    handler.send_response.assert_called_with(200)


def test_callback_handler_no_config():
    handler = MagicMock(spec=_OAuthCallbackHandler)
    handler.path = "/callback?code=CODE"
    handler.wfile = BytesIO()
    handler.send_response = MagicMock()
    handler.send_header = MagicMock()
    handler.end_headers = MagicMock()
    # Must set on the mock instance since spec creates its own attribute
    handler._config = None

    _OAuthCallbackHandler._config = None
    _OAuthCallbackHandler._verifier = ""

    _OAuthCallbackHandler.do_GET(handler)
    handler.send_response.assert_called_with(200)


def test_callback_handler_error_response():
    handler = MagicMock(spec=_OAuthCallbackHandler)
    handler.path = "/callback?error=access_denied"
    handler.wfile = BytesIO()
    handler.send_response = MagicMock()
    handler.send_header = MagicMock()
    handler.end_headers = MagicMock()

    _OAuthCallbackHandler.auth_code = None
    _OAuthCallbackHandler.do_GET(handler)
    handler.send_response.assert_called_with(400)


def test_callback_handler_log_message_suppressed():
    handler = MagicMock(spec=_OAuthCallbackHandler)
    _OAuthCallbackHandler.log_message(handler, "test %s", "arg")
    # Should not raise — log_message is a no-op


# ── _exchange_code ──────────────────────────────────────────

def test_exchange_code_success():
    with patch.dict(os.environ, {"TEST_CLIENT_ID": "cid", "TEST_CLIENT_SECRET": "csec"}), \
         patch("onemancompany.core.oauth._http_post", return_value=(200, {
             "access_token": "at", "refresh_token": "rt", "expires_in": 900, "scope": "read",
         })), \
         patch("onemancompany.core.oauth._save_tokens"):
        result = _exchange_code(TEST_CONFIG, "code123", "verifier")

    assert result["access_token"] == "at"
    assert TEST_CONFIG.service_name not in _active_flows


def test_exchange_code_no_verifier():
    with patch.dict(os.environ, {"TEST_CLIENT_ID": "cid", "TEST_CLIENT_SECRET": "csec"}), \
         patch("onemancompany.core.oauth._http_post", return_value=(200, {
             "access_token": "at", "expires_in": 900,
         })) as mock_post, \
         patch("onemancompany.core.oauth._save_tokens"):
        result = _exchange_code(TEST_CONFIG, "code123", "")

    # Verify code_verifier was NOT included in POST data
    call_data = mock_post.call_args[0][1]
    assert "code_verifier" not in call_data


def test_exchange_code_failure():
    with patch.dict(os.environ, {"TEST_CLIENT_ID": "cid", "TEST_CLIENT_SECRET": "csec"}), \
         patch("onemancompany.core.oauth._http_post", return_value=(400, {"error": "bad_code"})):
        result = _exchange_code(TEST_CONFIG, "bad", "v")

    assert "error" in result


# ── _run_callback_server ────────────────────────────────────

def test_run_callback_server_port_in_use():
    """When port is already bound, _run_callback_server returns early."""
    with patch("onemancompany.core.oauth.HTTPServer", side_effect=OSError("port in use")):
        _run_callback_server(TEST_CONFIG, "verifier")  # Should not raise


def test_run_callback_server_runs_until_deadline():
    """Server runs handle_request until auth_code is set or deadline passes."""
    mock_server = MagicMock()
    mock_server.timeout = 120

    call_count = 0

    def fake_handle():
        nonlocal call_count
        call_count += 1
        _OAuthCallbackHandler.auth_code = "got_code"

    mock_server.handle_request = fake_handle

    _OAuthCallbackHandler.auth_code = None
    _active_flows[TEST_CONFIG.service_name] = time.time() + 120

    with patch("onemancompany.core.oauth.HTTPServer", return_value=mock_server):
        _run_callback_server(TEST_CONFIG, "verifier")

    assert call_count >= 1
    mock_server.server_close.assert_called_once()
    assert TEST_CONFIG.service_name not in _active_flows


# ── _trigger_oauth_popup ────────────────────────────────────

def test_trigger_oauth_popup_no_client_id():
    with patch.dict(os.environ, {}, clear=True):
        result = _trigger_oauth_popup(TEST_CONFIG)
    assert result == ""


def test_trigger_oauth_popup_success():
    with patch.dict(os.environ, {"TEST_CLIENT_ID": "cid"}), \
         patch("onemancompany.core.oauth.threading.Thread") as mock_thread_cls:
        mock_thread = MagicMock()
        mock_thread_cls.return_value = mock_thread

        mock_loop = MagicMock()
        mock_loop.is_running.return_value = True

        with patch("asyncio.get_event_loop", return_value=mock_loop), \
             patch("asyncio.run_coroutine_threadsafe"):
            result = _trigger_oauth_popup(TEST_CONFIG)

    assert "client_id=cid" in result
    assert TEST_CONFIG.service_name in _active_flows
    mock_thread.start.assert_called_once()


def test_trigger_oauth_popup_event_publish_fails():
    with patch.dict(os.environ, {"TEST_CLIENT_ID": "cid"}), \
         patch("onemancompany.core.oauth.threading.Thread") as mock_thread_cls:
        mock_thread = MagicMock()
        mock_thread_cls.return_value = mock_thread

        with patch("asyncio.get_event_loop", side_effect=RuntimeError("no loop")):
            result = _trigger_oauth_popup(TEST_CONFIG)

    assert result != ""


def test_trigger_oauth_popup_loop_not_running():
    with patch.dict(os.environ, {"TEST_CLIENT_ID": "cid"}), \
         patch("onemancompany.core.oauth.threading.Thread") as mock_thread_cls:
        mock_thread = MagicMock()
        mock_thread_cls.return_value = mock_thread

        mock_loop = MagicMock()
        mock_loop.is_running.return_value = False

        with patch("asyncio.get_event_loop", return_value=mock_loop), \
             patch("asyncio.run_coroutine_threadsafe") as mock_rcts:
            result = _trigger_oauth_popup(TEST_CONFIG)

    mock_rcts.assert_not_called()
    assert result != ""


# ── ensure_oauth_token ──────────────────────────────────────

def test_ensure_oauth_token_cached():
    with patch("onemancompany.core.oauth.get_oauth_token", return_value="cached_tok"):
        result = ensure_oauth_token(TEST_CONFIG)
    assert result == "cached_tok"


def test_ensure_oauth_token_no_credentials():
    with patch("onemancompany.core.oauth.get_oauth_token", return_value=None), \
         patch.dict(os.environ, {}, clear=True):
        result = ensure_oauth_token(TEST_CONFIG)
    assert result is None


def test_ensure_oauth_token_flow_already_active():
    _active_flows[TEST_CONFIG.service_name] = time.time() + 1000

    with patch("onemancompany.core.oauth.get_oauth_token", return_value=None), \
         patch.dict(os.environ, {"TEST_CLIENT_ID": "cid", "TEST_CLIENT_SECRET": "csec"}):
        result = ensure_oauth_token(TEST_CONFIG)
    assert result is None


def test_ensure_oauth_token_triggers_popup():
    with patch("onemancompany.core.oauth.get_oauth_token", return_value=None), \
         patch.dict(os.environ, {"TEST_CLIENT_ID": "cid", "TEST_CLIENT_SECRET": "csec"}), \
         patch("onemancompany.core.oauth._trigger_oauth_popup", return_value="https://auth.url") as mock_popup:
        result = ensure_oauth_token(TEST_CONFIG)

    assert result is None
    mock_popup.assert_called_once_with(TEST_CONFIG)


def test_ensure_oauth_token_expired_flow():
    """An expired active flow should allow a new popup trigger."""
    _active_flows[TEST_CONFIG.service_name] = time.time() - 100  # expired

    with patch("onemancompany.core.oauth.get_oauth_token", return_value=None), \
         patch.dict(os.environ, {"TEST_CLIENT_ID": "cid", "TEST_CLIENT_SECRET": "csec"}), \
         patch("onemancompany.core.oauth._trigger_oauth_popup") as mock_popup:
        ensure_oauth_token(TEST_CONFIG)

    mock_popup.assert_called_once()


# ── request_credentials ─────────────────────────────────────

def test_request_credentials_already_pending():
    _pending_credentials["my_svc"] = time.time() + 1000

    result = request_credentials("my_svc", "Need API Key")
    assert result is False


def test_request_credentials_success():
    mock_loop = MagicMock()
    mock_loop.is_running.return_value = True

    with patch("asyncio.get_event_loop", return_value=mock_loop), \
         patch("asyncio.run_coroutine_threadsafe"):
        result = request_credentials("new_svc", "Need Key", "Please provide")

    assert result is True
    assert "new_svc" in _pending_credentials


def test_request_credentials_default_fields():
    mock_loop = MagicMock()
    mock_loop.is_running.return_value = True

    with patch("asyncio.get_event_loop", return_value=mock_loop), \
         patch("asyncio.run_coroutine_threadsafe") as mock_rcts:
        result = request_credentials("svc2", "Title")

    assert result is True


def test_request_credentials_custom_fields():
    mock_loop = MagicMock()
    mock_loop.is_running.return_value = True

    fields = [{"name": "token", "label": "Token", "secret": True}]
    with patch("asyncio.get_event_loop", return_value=mock_loop), \
         patch("asyncio.run_coroutine_threadsafe"):
        result = request_credentials("svc3", "Title", fields=fields)

    assert result is True


def test_request_credentials_event_publish_fails():
    with patch("asyncio.get_event_loop", side_effect=RuntimeError("no loop")):
        result = request_credentials("fail_svc", "Title")

    assert result is False


def test_request_credentials_loop_not_running():
    mock_loop = MagicMock()
    mock_loop.is_running.return_value = False

    with patch("asyncio.get_event_loop", return_value=mock_loop):
        result = request_credentials("nr_svc", "Title")

    assert result is False


def test_request_credentials_expired_pending():
    """Expired pending credential allows a new request."""
    _pending_credentials["exp_svc"] = time.time() - 100  # expired

    mock_loop = MagicMock()
    mock_loop.is_running.return_value = True

    with patch("asyncio.get_event_loop", return_value=mock_loop), \
         patch("asyncio.run_coroutine_threadsafe"):
        result = request_credentials("exp_svc", "Title")

    assert result is True


# ── _OAuthFlowsSnapshot ────────────────────────────────────

def test_snapshot_save_empty():
    result = _OAuthFlowsSnapshot.save()
    assert result == {}


def test_snapshot_save_with_data():
    future = time.time() + 1000
    _active_flows["svc_a"] = future
    _pending_credentials["svc_b"] = future

    result = _OAuthFlowsSnapshot.save()
    assert "svc_a" in result["active_flows"]
    assert "svc_b" in result["pending_credentials"]


def test_snapshot_save_expired_excluded():
    past = time.time() - 100
    _active_flows["old"] = past
    _pending_credentials["old_cred"] = past

    result = _OAuthFlowsSnapshot.save()
    assert result == {}


def test_snapshot_restore_valid():
    future = time.time() + 1000
    data = {
        "active_flows": {"restored_svc": future},
        "pending_credentials": {"restored_cred": future},
    }
    _OAuthFlowsSnapshot.restore(data)
    assert "restored_svc" in _active_flows
    assert "restored_cred" in _pending_credentials


def test_snapshot_restore_expired_excluded():
    past = time.time() - 100
    data = {
        "active_flows": {"old_svc": past},
        "pending_credentials": {"old_cred": past},
    }
    _OAuthFlowsSnapshot.restore(data)
    assert "old_svc" not in _active_flows
    assert "old_cred" not in _pending_credentials


def test_snapshot_restore_empty():
    _OAuthFlowsSnapshot.restore({})
    assert len(_active_flows) == 0
    assert len(_pending_credentials) == 0
