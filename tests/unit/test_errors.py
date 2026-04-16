"""Unit tests for structured error classification."""

import asyncio

from onemancompany.core.errors import ErrorCode, classify_exception


class TestErrorClassification:
    def test_timeout(self):
        err = classify_exception(asyncio.TimeoutError())
        assert err.code == ErrorCode.AGENT_TIMEOUT
        assert err.recoverable is True
        assert err.severity == "error"

    def test_rate_limit(self):
        err = classify_exception(Exception("rate_limit_exceeded (429)"))
        assert err.code == ErrorCode.LLM_RATE_LIMIT
        assert err.severity == "warning"
        assert err.recoverable is True

    def test_auth_failure_401(self):
        err = classify_exception(Exception("Authentication failed (401)"))
        assert err.code == ErrorCode.LLM_AUTH_FAILURE
        assert err.recoverable is False
        assert err.severity == "critical"

    def test_auth_failure_403(self):
        err = classify_exception(Exception("Forbidden (403)"))
        assert err.code == ErrorCode.LLM_AUTH_FAILURE
        assert err.recoverable is False

    def test_context_overflow(self):
        err = classify_exception(Exception("context length exceeded"))
        assert err.code == ErrorCode.LLM_CONTEXT_OVERFLOW
        assert err.recoverable is True

    def test_file_io_error(self):
        err = classify_exception(OSError("Permission denied"))
        assert err.code == ErrorCode.FILE_IO_ERROR
        assert err.recoverable is True

    def test_provider_down(self):
        err = classify_exception(Exception("Connection refused (502)"))
        assert err.code == ErrorCode.LLM_PROVIDER_DOWN
        assert err.recoverable is True

    def test_unknown_fallback(self):
        err = classify_exception(ValueError("something weird"))
        assert err.code == ErrorCode.AGENT_TOOL_FAILURE
        assert err.recoverable is True
        assert "ValueError" in err.context["exception_type"]

    def test_graph_recursion_error(self):
        """Line 71: GraphRecursionError type name triggers AGENT_RECURSION_LIMIT."""
        # Simulate an exception whose class name contains "GraphRecursionError"
        class GraphRecursionError(Exception):
            pass

        err = classify_exception(GraphRecursionError("hit recursion limit"))
        assert err.code == ErrorCode.AGENT_RECURSION_LIMIT
        assert err.severity == "error"
        assert err.recoverable is True

    def test_recursion_in_message(self):
        """Line 70: 'recursion' in message triggers AGENT_RECURSION_LIMIT."""
        err = classify_exception(Exception("recursion limit reached"))
        assert err.code == ErrorCode.AGENT_RECURSION_LIMIT
        assert err.recoverable is True

    def test_all_errors_have_suggestion(self):
        """Every classified error should have a non-empty suggestion."""
        exceptions = [
            asyncio.TimeoutError(),
            Exception("rate_limit_exceeded"),
            Exception("401 unauthorized"),
            Exception("context length too long"),
            OSError("disk full"),
            Exception("502 bad gateway"),
            ValueError("unknown error"),
        ]
        for exc in exceptions:
            err = classify_exception(exc)
            assert err.suggestion, f"No suggestion for {err.code}"

    def test_billing_limit_branch(self):
        """Line 83: billing + limit triggers LLM_QUOTA_EXCEEDED."""
        from onemancompany.core.errors import ErrorCode
        exc = Exception("billing limit exceeded")
        err = classify_exception(exc)
        assert err.code == ErrorCode.LLM_QUOTA_EXCEEDED
