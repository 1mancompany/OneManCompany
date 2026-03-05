"""Unit tests for core/vessel_harness.py — Protocol definitions."""

from __future__ import annotations

from onemancompany.core.vessel_harness import (
    ExecutionHarness,
    TaskHarness,
    EventHarness,
    StorageHarness,
    ContextHarness,
    LifecycleHarness,
)


class TestProtocolsImportable:
    """Verify all harness protocols can be imported and are runtime-checkable."""

    def test_execution_harness_is_protocol(self):
        assert hasattr(ExecutionHarness, '__protocol_attrs__') or hasattr(ExecutionHarness, '_is_protocol')

    def test_task_harness_is_protocol(self):
        assert hasattr(TaskHarness, '__protocol_attrs__') or hasattr(TaskHarness, '_is_protocol')

    def test_event_harness_is_protocol(self):
        assert hasattr(EventHarness, '__protocol_attrs__') or hasattr(EventHarness, '_is_protocol')

    def test_storage_harness_is_protocol(self):
        assert hasattr(StorageHarness, '__protocol_attrs__') or hasattr(StorageHarness, '_is_protocol')

    def test_context_harness_is_protocol(self):
        assert hasattr(ContextHarness, '__protocol_attrs__') or hasattr(ContextHarness, '_is_protocol')

    def test_lifecycle_harness_is_protocol(self):
        assert hasattr(LifecycleHarness, '__protocol_attrs__') or hasattr(LifecycleHarness, '_is_protocol')


class TestExecutionHarnessConformance:
    """Verify LangChainExecutor conforms to the ExecutionHarness protocol."""

    def test_langchain_executor_is_execution_harness(self):
        from unittest.mock import MagicMock
        from onemancompany.core.vessel import LangChainExecutor

        mock_runner = MagicMock()
        executor = LangChainExecutor(mock_runner)
        assert isinstance(executor, ExecutionHarness)

    def test_claude_session_executor_is_execution_harness(self):
        from onemancompany.core.vessel import ClaudeSessionExecutor

        executor = ClaudeSessionExecutor("00010")
        assert isinstance(executor, ExecutionHarness)

    def test_script_executor_is_execution_harness(self):
        from onemancompany.core.vessel import ScriptExecutor

        executor = ScriptExecutor("00010")
        assert isinstance(executor, ExecutionHarness)
