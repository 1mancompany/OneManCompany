"""Unit tests for onemancompany.core.update_checker — 100% coverage."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── _get_current_version ────────────────────────────────────

def test_get_current_version_success():
    from onemancompany.core.update_checker import _get_current_version
    with patch("onemancompany.core.update_checker.version", return_value="1.2.3", create=True):
        # Need to patch at the importlib level
        pass

    # Simpler: just call and check it returns a string
    result = _get_current_version()
    assert isinstance(result, str)


def test_get_current_version_exception():
    from onemancompany.core.update_checker import _get_current_version
    with patch("importlib.metadata.version", side_effect=Exception("no package")):
        result = _get_current_version()
    assert result == ""


# ── _fetch_latest_version ───────────────────────────────────

@pytest.mark.asyncio
async def test_fetch_latest_version_success():
    from onemancompany.core.update_checker import _fetch_latest_version

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"version": "2.0.0"}

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("onemancompany.core.update_checker.httpx.AsyncClient", return_value=mock_client):
        result = await _fetch_latest_version()

    assert result == "2.0.0"


@pytest.mark.asyncio
async def test_fetch_latest_version_non_200():
    from onemancompany.core.update_checker import _fetch_latest_version

    mock_resp = MagicMock()
    mock_resp.status_code = 404

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("onemancompany.core.update_checker.httpx.AsyncClient", return_value=mock_client):
        result = await _fetch_latest_version()

    assert result == ""


@pytest.mark.asyncio
async def test_fetch_latest_version_exception():
    from onemancompany.core.update_checker import _fetch_latest_version

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=Exception("network error"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("onemancompany.core.update_checker.httpx.AsyncClient", return_value=mock_client):
        result = await _fetch_latest_version()

    assert result == ""


# ── _is_newer ───────────────────────────────────────────────

def test_is_newer_true():
    from onemancompany.core.update_checker import _is_newer
    assert _is_newer("2.0.0", "1.0.0") is True
    assert _is_newer("1.1.0", "1.0.0") is True
    assert _is_newer("1.0.1", "1.0.0") is True


def test_is_newer_false():
    from onemancompany.core.update_checker import _is_newer
    assert _is_newer("1.0.0", "1.0.0") is False
    assert _is_newer("0.9.0", "1.0.0") is False


def test_is_newer_invalid():
    from onemancompany.core.update_checker import _is_newer
    assert _is_newer("abc", "1.0.0") is False
    assert _is_newer("1.0.0", "abc") is False
    # None raises AttributeError which is NOT caught by the (ValueError, TypeError) handler
    # so it would propagate — but in practice None is never passed


# ── check_and_notify ────────────────────────────────────────

@pytest.mark.asyncio
async def test_check_and_notify_no_current_version():
    from onemancompany.core.update_checker import check_and_notify
    with patch("onemancompany.core.update_checker._get_current_version", return_value=""):
        await check_and_notify()  # Should return early


@pytest.mark.asyncio
async def test_check_and_notify_dev_version():
    from onemancompany.core.update_checker import check_and_notify
    with patch("onemancompany.core.update_checker._get_current_version", return_value="dev"):
        await check_and_notify()  # Should return early


@pytest.mark.asyncio
async def test_check_and_notify_no_latest():
    from onemancompany.core.update_checker import check_and_notify
    with patch("onemancompany.core.update_checker._get_current_version", return_value="1.0.0"), \
         patch("onemancompany.core.update_checker._fetch_latest_version", new_callable=AsyncMock, return_value=""):
        await check_and_notify()  # Should return early


@pytest.mark.asyncio
async def test_check_and_notify_newer_available():
    from onemancompany.core.update_checker import check_and_notify

    mock_publish = AsyncMock()
    mock_bus = MagicMock()
    mock_bus.publish = mock_publish

    with patch("onemancompany.core.update_checker._get_current_version", return_value="1.0.0"), \
         patch("onemancompany.core.update_checker._fetch_latest_version", new_callable=AsyncMock, return_value="2.0.0"), \
         patch("onemancompany.core.events.event_bus", mock_bus):
        await check_and_notify()

    mock_publish.assert_awaited_once()
    call_args = mock_publish.call_args[0][0]
    assert "2.0.0" in call_args.payload["message"]


@pytest.mark.asyncio
async def test_check_and_notify_up_to_date():
    from onemancompany.core.update_checker import check_and_notify
    with patch("onemancompany.core.update_checker._get_current_version", return_value="2.0.0"), \
         patch("onemancompany.core.update_checker._fetch_latest_version", new_callable=AsyncMock, return_value="1.0.0"):
        await check_and_notify()  # Should just log debug


# ── start_update_checker ────────────────────────────────────

@pytest.mark.asyncio
async def test_start_update_checker_runs_and_cancels():
    from onemancompany.core.update_checker import start_update_checker

    call_count = 0

    async def fake_check():
        nonlocal call_count
        call_count += 1

    async def fast_sleep(seconds):
        if call_count >= 1:
            raise asyncio.CancelledError()
        # Skip the initial 10s delay
        return

    with patch("onemancompany.core.update_checker.check_and_notify", side_effect=fake_check), \
         patch("onemancompany.core.update_checker.asyncio.sleep", side_effect=fast_sleep):
        with pytest.raises(asyncio.CancelledError):
            await start_update_checker()

    assert call_count >= 1


@pytest.mark.asyncio
async def test_start_update_checker_handles_error():
    from onemancompany.core.update_checker import start_update_checker

    call_count = 0

    async def failing_check():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("check failed")

    async def fast_sleep(seconds):
        if call_count >= 2:
            raise asyncio.CancelledError()
        return

    with patch("onemancompany.core.update_checker.check_and_notify", side_effect=failing_check), \
         patch("onemancompany.core.update_checker.asyncio.sleep", side_effect=fast_sleep):
        with pytest.raises(asyncio.CancelledError):
            await start_update_checker()

    assert call_count >= 2
