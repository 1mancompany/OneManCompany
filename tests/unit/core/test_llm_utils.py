"""Tests for core/llm_utils.py — LLM retry logic edge cases."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from onemancompany.core.errors import ErrorCode, StructuredError


class TestLlmInvokeRetryBranches:
    @pytest.mark.asyncio
    async def test_non_recoverable_breaks_immediately(self):
        """Line 44: non-recoverable error breaks without retry."""
        from onemancompany.core.llm_utils import llm_invoke_with_retry

        exc = Exception("401 unauthorized bad key")
        with patch("onemancompany.core.llm_utils.tracked_ainvoke", new_callable=AsyncMock, side_effect=exc), \
             patch("onemancompany.core.llm_utils.classify_exception") as mock_classify:
            mock_classify.return_value = StructuredError(
                code=ErrorCode.LLM_AUTH_FAILURE,
                severity="error",
                message="auth error",
                suggestion="check key",
                recoverable=False,
            )
            with pytest.raises(Exception):
                await llm_invoke_with_retry(MagicMock(), [MagicMock()], max_retries=3)

    @pytest.mark.asyncio
    async def test_quota_error_uses_quota_delay(self):
        """Line 46: quota errors use quota_base_delay."""
        from onemancompany.core.llm_utils import llm_invoke_with_retry

        call_count = 0

        async def fail_then_succeed(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("quota exceeded")
            return MagicMock(content="ok")

        with patch("onemancompany.core.llm_utils.tracked_ainvoke", side_effect=fail_then_succeed), \
             patch("onemancompany.core.llm_utils.classify_exception") as mock_classify, \
             patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            mock_classify.return_value = StructuredError(
                code=ErrorCode.LLM_QUOTA_EXCEEDED,
                severity="error",
                message="quota exceeded",
                suggestion="wait",
                recoverable=True,
            )
            result = await llm_invoke_with_retry(
                MagicMock(), [MagicMock()],
                quota_base_delay=10.0, quota_max_retries=3,
            )
            # Quota delay = quota_base_delay * (attempt + 1) = 10 * 1 = 10
            mock_sleep.assert_called_once_with(10.0)

    @pytest.mark.asyncio
    async def test_rate_limit_uses_exponential_delay(self):
        """Line 48: rate limit errors use exponential backoff."""
        from onemancompany.core.llm_utils import llm_invoke_with_retry

        call_count = 0

        async def fail_then_succeed(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("429 rate limit")
            return MagicMock(content="ok")

        with patch("onemancompany.core.llm_utils.tracked_ainvoke", side_effect=fail_then_succeed), \
             patch("onemancompany.core.llm_utils.classify_exception") as mock_classify, \
             patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            mock_classify.return_value = StructuredError(
                code=ErrorCode.LLM_RATE_LIMIT,
                severity="warning",
                message="rate limited",
                suggestion="wait",
                recoverable=True,
            )
            result = await llm_invoke_with_retry(
                MagicMock(), [MagicMock()],
                base_delay=2.0, max_retries=3,
            )
            # Rate limit delay = base_delay * 2^attempt = 2 * 1 = 2
            mock_sleep.assert_called_once_with(2.0)
