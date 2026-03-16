"""Unit tests for core/safe_execute.py — 3-tier safe execution."""

from __future__ import annotations

import asyncio

import pytest

from onemancompany.core.errors import ErrorCode
from onemancompany.core.safe_execute import safe_agent_execute


# ---------------------------------------------------------------------------
# Tier 1: Normal execution
# ---------------------------------------------------------------------------

class TestTier1Success:
    async def test_successful_execution(self):
        async def execute(desc: str) -> str:
            return "done"

        result, diags = await safe_agent_execute(execute, "test task")
        assert result.success is True
        assert result.output == "done"
        assert result.attempt == 1
        assert diags == []

    async def test_empty_output_returns_empty_string(self):
        async def execute(desc: str) -> str:
            return ""

        result, diags = await safe_agent_execute(execute, "test")
        assert result.success is True
        assert result.output == ""

    async def test_none_output_returns_empty_string(self):
        async def execute(desc: str):
            return None

        result, diags = await safe_agent_execute(execute, "test")
        assert result.success is True
        assert result.output == ""


# ---------------------------------------------------------------------------
# Tier 1 fails, Tier 2 succeeds
# ---------------------------------------------------------------------------

class TestTier2Fallback:
    async def test_tier2_succeeds_after_tier1_recoverable_failure(self):
        call_count = 0

        async def execute(desc: str) -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("some recoverable error")
            return "tier2 result"

        result, diags = await safe_agent_execute(execute, "test task")
        assert result.success is True
        assert result.output == "tier2 result"
        assert result.attempt == 2
        assert len(diags) == 2  # tier1 error + tier2 warning
        assert diags[0].code == ErrorCode.AGENT_TOOL_FAILURE

    async def test_tier2_receives_simplified_description(self):
        received_descs = []

        async def execute(desc: str) -> str:
            received_descs.append(desc)
            if len(received_descs) == 1:
                raise RuntimeError("fail first time")
            return "ok"

        await safe_agent_execute(execute, "original task")
        assert len(received_descs) == 2
        assert received_descs[1].startswith("[Simplified]")

    async def test_tier2_truncates_long_description(self):
        received_descs = []

        async def execute(desc: str) -> str:
            received_descs.append(desc)
            if len(received_descs) == 1:
                raise RuntimeError("fail")
            return "ok"

        long_desc = "x" * 1000
        await safe_agent_execute(execute, long_desc)
        # Simplified desc should truncate to 500 chars + prefix
        assert len(received_descs[1]) < 600


# ---------------------------------------------------------------------------
# Tier 1 non-recoverable → skip Tier 2, return immediately
# ---------------------------------------------------------------------------

class TestNonRecoverableError:
    async def test_auth_failure_skips_tier2(self):
        async def execute(desc: str) -> str:
            raise RuntimeError("401 unauthorized error")

        result, diags = await safe_agent_execute(execute, "test")
        assert result.success is False
        assert result.attempt == 1
        assert len(diags) == 1
        assert diags[0].code == ErrorCode.LLM_AUTH_FAILURE
        assert diags[0].recoverable is False


# ---------------------------------------------------------------------------
# All tiers fail
# ---------------------------------------------------------------------------

class TestAllTiersFail:
    async def test_all_tiers_fail(self):
        async def execute(desc: str) -> str:
            raise RuntimeError("generic failure")

        result, diags = await safe_agent_execute(execute, "test")
        assert result.success is False
        assert result.attempt == 3
        assert "All 3 tiers failed" in result.error
        assert len(diags) == 2  # tier1 + tier2 errors


# ---------------------------------------------------------------------------
# Timeout handling
# ---------------------------------------------------------------------------

class TestTimeout:
    async def test_tier1_timeout(self):
        call_count = 0

        async def execute(desc: str) -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                await asyncio.sleep(10)  # will be cancelled by timeout
            return "recovered"

        result, diags = await safe_agent_execute(execute, "test", timeout=0.1)
        assert result.success is True
        assert result.attempt == 2
        # First diagnostic should be a timeout error
        assert diags[0].code == ErrorCode.AGENT_TIMEOUT

    async def test_tier2_has_half_timeout(self):
        """Tier 2 uses timeout/2. If both tiers time out, all fail."""
        async def execute(desc: str) -> str:
            await asyncio.sleep(10)
            return "never"

        result, diags = await safe_agent_execute(execute, "test", timeout=0.1)
        assert result.success is False
        assert result.attempt == 3
        assert len(diags) == 2
