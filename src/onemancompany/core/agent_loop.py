"""Backward-compatibility shim — all logic moved to vessel.py.

All symbols previously defined here are now re-exported from vessel.py.
Existing imports like ``from onemancompany.core.agent_loop import ...`` continue to work.
"""

from onemancompany.core.vessel import *  # noqa: F401,F403
from onemancompany.core.vessel import (  # noqa: F401 — explicit re-exports for type checkers
    _current_vessel as _current_loop,
    _current_task_id,
    AgentTask,
    AgentTaskBoard,
    LaunchResult,
    TaskContext,
    Launcher,
    ExecutionHarness,
    LangChainLauncher,
    LangChainExecutor,
    ClaudeSessionLauncher,
    ClaudeSessionExecutor,
    ScriptLauncher,
    ScriptExecutor,
    _AgentRef,
    _VesselRef,
    EmployeeHandle,
    Vessel,
    EmployeeManager,
    employee_manager,
    agent_loops,
    register_agent,
    register_self_hosted,
    get_agent_loop,
    start_all_loops,
    stop_all_loops,
    register_and_start_agent,
    _append_progress,
    _load_progress,
    PROGRESS_LOG_MAX_LINES,
    MAX_SUBTASK_ITERATIONS,
    MAX_SUBTASK_DEPTH,
    MAX_RETRIES,
    RETRY_DELAYS,
    MAX_HISTORY_ENTRIES,
    MAX_HISTORY_CHARS,
    RESULT_SNIPPET_LEN,
)
