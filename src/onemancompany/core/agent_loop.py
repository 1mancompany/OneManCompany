"""Re-export shim — all logic lives in vessel.py.

All symbols previously defined here are re-exported from vessel.py.
Existing imports like ``from onemancompany.core.agent_loop import ...`` continue to work.
"""

from onemancompany.core.vessel import *  # noqa: F401,F403
from onemancompany.core.vessel import (  # noqa: F401 — explicit re-exports for type checkers
    _current_vessel,
    _current_task_id,
    ScheduleEntry,
    LaunchResult,
    TaskContext,
    Launcher,
    LangChainExecutor,
    ClaudeSessionExecutor,
    ScriptExecutor,
    _VesselRef,
    Vessel,
    EmployeeManager,
    employee_manager,
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
    # Backward-compat aliases
    EmployeeHandle,
    _AgentRef,
    _current_loop,
    LangChainLauncher,
    ClaudeSessionLauncher,
    ScriptLauncher,
    agent_loops,
)
