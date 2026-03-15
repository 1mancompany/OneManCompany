"""Tests for probe_chat() shared verifier."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock


class TestProbeChat:
    async def test_openai_compatible_success(self):
        from onemancompany.core.auth_verify import probe_chat

        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="hi"))]

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        with patch("onemancompany.core.auth_verify._make_openai_client", return_value=mock_client):
            ok, error = await probe_chat("deepseek", "sk-test", "deepseek-chat")

        assert ok is True
        assert error == ""

    async def test_openai_compatible_invalid_key(self):
        from onemancompany.core.auth_verify import probe_chat

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=Exception("Incorrect API key provided")
        )

        with patch("onemancompany.core.auth_verify._make_openai_client", return_value=mock_client):
            ok, error = await probe_chat("deepseek", "bad-key", "deepseek-chat")

        assert ok is False
        assert "Incorrect API key" in error

    async def test_anthropic_success(self):
        from onemancompany.core.auth_verify import probe_chat

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="hi")]

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with patch("onemancompany.core.auth_verify._make_anthropic_client", return_value=mock_client):
            ok, error = await probe_chat("anthropic", "sk-ant-test", "claude-3-haiku-20240307")

        assert ok is True
        assert error == ""

    async def test_timeout(self):
        import asyncio
        from onemancompany.core.auth_verify import probe_chat

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=asyncio.TimeoutError()
        )

        with patch("onemancompany.core.auth_verify._make_openai_client", return_value=mock_client):
            ok, error = await probe_chat("openai", "sk-test", "gpt-4o", timeout=1.0)

        assert ok is False
        assert "timeout" in error.lower() or "Timeout" in error

    async def test_custom_provider_with_base_url(self):
        from onemancompany.core.auth_verify import probe_chat

        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="hi"))]

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        with patch("onemancompany.core.auth_verify._make_openai_client", return_value=mock_client) as mock_make:
            ok, error = await probe_chat(
                "custom", "sk-test", "my-model",
                base_url="https://api.example.com/v1",
            )

        assert ok is True
        mock_make.assert_called_once()
        call_kwargs = mock_make.call_args
        assert "https://api.example.com/v1" in str(call_kwargs)
