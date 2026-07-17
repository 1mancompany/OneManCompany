"""ACP SDK adapter — single import point for all Agent Client Protocol types.

Every OMC module that needs ACP types MUST import from here, never from ``acp.*``
directly.  If the ACP SDK has a breaking change, only this file needs updating.

SDK version: agent-client-protocol==0.9.0 (schema ref: v0.11.2)
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Core protocols
# ---------------------------------------------------------------------------
from acp.interfaces import Agent, Client

# ---------------------------------------------------------------------------
# Connection / runner helpers
# ---------------------------------------------------------------------------
from acp.core import connect_to_agent, run_agent
from acp.client.connection import ClientSideConnection
from acp.agent.connection import AgentSideConnection

# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------
from acp.transports import spawn_stdio_transport
from acp.stdio import stdio_streams, spawn_stdio_connection, spawn_agent_process, spawn_client_process

# ---------------------------------------------------------------------------
# Protocol version & method tables
# ---------------------------------------------------------------------------
from acp.meta import PROTOCOL_VERSION, AGENT_METHODS, CLIENT_METHODS

# ---------------------------------------------------------------------------
# Request / Response types
# ---------------------------------------------------------------------------
from acp.schema import (
    # Lifecycle
    InitializeRequest,
    InitializeResponse,
    NewSessionRequest,
    NewSessionResponse,
    LoadSessionRequest,
    LoadSessionResponse,
    ResumeSessionRequest,
    ResumeSessionResponse,
    ForkSessionRequest,
    ForkSessionResponse,
    CloseSessionRequest,
    CloseSessionResponse,
    # Prompt
    PromptRequest,
    PromptResponse,
    # Permissions
    RequestPermissionRequest,
    RequestPermissionResponse,
    # Session metadata
    AgentCapabilities,
    ClientCapabilities,
    Implementation,
    # Session management
    ListSessionsRequest,
    ListSessionsResponse,
    SetSessionModeRequest,
    SetSessionModeResponse,
    SetSessionModelRequest,
    SetSessionModelResponse,
    SetSessionConfigOptionSelectRequest,
    SetSessionConfigOptionBooleanRequest,
    SetSessionConfigOptionResponse,
    # Auth
    AuthenticateRequest,
    AuthenticateResponse,
    # Notifications
    CancelNotification,
    SessionNotification,
    # File I/O
    ReadTextFileRequest,
    ReadTextFileResponse,
    WriteTextFileRequest,
    WriteTextFileResponse,
    # Terminal
    CreateTerminalRequest,
    CreateTerminalResponse,
    TerminalOutputRequest,
    TerminalOutputResponse,
    WaitForTerminalExitRequest,
    WaitForTerminalExitResponse,
    KillTerminalRequest,
    KillTerminalResponse,
    ReleaseTerminalRequest,
    ReleaseTerminalResponse,
    # Session update types (streamed from agent → client)
    AgentMessageChunk,
    AgentThoughtChunk,
    ToolCallStart,
    ToolCallProgress,
    AgentPlanUpdate,
    UsageUpdate,
    AvailableCommandsUpdate,
    CurrentModeUpdate,
    ConfigOptionUpdate,
    SessionInfoUpdate,
    UserMessageChunk,
    # Plan
    PlanEntry,
    # Content blocks
    TextContentBlock,
    ImageContentBlock,
    AudioContentBlock,
    ResourceContentBlock,
    EmbeddedResourceContentBlock,
    # Tool call content
    ContentToolCallContent,
    FileEditToolCallContent,
    TerminalToolCallContent,
    ToolCallLocation,
    # Misc
    EnvVariable,
    HttpMcpServer,
    SseMcpServer,
    McpServerStdio,
    PermissionOption,
    Usage,
    Cost,
    ToolCallStatus,
    ToolKind,
    PlanEntryPriority,
    PlanEntryStatus,
    StopReason,
    # Session capabilities
    SessionCapabilities,
    SessionCloseCapabilities,
    SessionForkCapabilities,
    SessionListCapabilities,
    SessionResumeCapabilities,
    # Session mode/model state
    SessionMode,
    SessionModeState,
    SessionModelState,
)

# ---------------------------------------------------------------------------
# Content / update builder helpers
# ---------------------------------------------------------------------------
from acp.helpers import (
    text_block,
    image_block,
    audio_block,
    resource_link_block,
    embedded_text_resource,
    embedded_blob_resource,
    resource_block,
    tool_content,
    tool_diff_content,
    tool_terminal_ref,
    plan_entry,
    update_plan,
    update_user_message,
    update_user_message_text,
    update_agent_message,
    update_agent_message_text,
    update_agent_thought,
    update_agent_thought_text,
    session_notification,
    start_tool_call,
    start_read_tool_call,
    start_edit_tool_call,
    update_tool_call,
)

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------
from acp.exceptions import RequestError

__all__ = [
    # Protocols
    "Agent",
    "Client",
    # Connection
    "connect_to_agent",
    "run_agent",
    "ClientSideConnection",
    "AgentSideConnection",
    # Transport
    "spawn_stdio_transport",
    "stdio_streams",
    "spawn_stdio_connection",
    "spawn_agent_process",
    "spawn_client_process",
    # Protocol metadata
    "PROTOCOL_VERSION",
    "AGENT_METHODS",
    "CLIENT_METHODS",
    # Request/Response
    "InitializeRequest",
    "InitializeResponse",
    "NewSessionRequest",
    "NewSessionResponse",
    "LoadSessionRequest",
    "LoadSessionResponse",
    "ResumeSessionRequest",
    "ResumeSessionResponse",
    "ForkSessionRequest",
    "ForkSessionResponse",
    "CloseSessionRequest",
    "CloseSessionResponse",
    "PromptRequest",
    "PromptResponse",
    "RequestPermissionRequest",
    "RequestPermissionResponse",
    "AgentCapabilities",
    "ClientCapabilities",
    "Implementation",
    "ListSessionsRequest",
    "ListSessionsResponse",
    "SetSessionModeRequest",
    "SetSessionModeResponse",
    "SetSessionModelRequest",
    "SetSessionModelResponse",
    "SetSessionConfigOptionSelectRequest",
    "SetSessionConfigOptionBooleanRequest",
    "SetSessionConfigOptionResponse",
    "AuthenticateRequest",
    "AuthenticateResponse",
    "CancelNotification",
    "SessionNotification",
    "ReadTextFileRequest",
    "ReadTextFileResponse",
    "WriteTextFileRequest",
    "WriteTextFileResponse",
    "CreateTerminalRequest",
    "CreateTerminalResponse",
    "TerminalOutputRequest",
    "TerminalOutputResponse",
    "WaitForTerminalExitRequest",
    "WaitForTerminalExitResponse",
    "KillTerminalRequest",
    "KillTerminalResponse",
    "ReleaseTerminalRequest",
    "ReleaseTerminalResponse",
    # Session updates
    "AgentMessageChunk",
    "AgentThoughtChunk",
    "ToolCallStart",
    "ToolCallProgress",
    "AgentPlanUpdate",
    "UsageUpdate",
    "AvailableCommandsUpdate",
    "CurrentModeUpdate",
    "ConfigOptionUpdate",
    "SessionInfoUpdate",
    "UserMessageChunk",
    # Plan
    "PlanEntry",
    # Content blocks
    "TextContentBlock",
    "ImageContentBlock",
    "AudioContentBlock",
    "ResourceContentBlock",
    "EmbeddedResourceContentBlock",
    # Tool call content
    "ContentToolCallContent",
    "FileEditToolCallContent",
    "TerminalToolCallContent",
    "ToolCallLocation",
    # Misc schema types
    "EnvVariable",
    "HttpMcpServer",
    "SseMcpServer",
    "McpServerStdio",
    "PermissionOption",
    "Usage",
    "Cost",
    "ToolCallStatus",
    "ToolKind",
    "PlanEntryPriority",
    "PlanEntryStatus",
    "StopReason",
    # Builder helpers
    "text_block",
    "image_block",
    "audio_block",
    "resource_link_block",
    "embedded_text_resource",
    "embedded_blob_resource",
    "resource_block",
    "tool_content",
    "tool_diff_content",
    "tool_terminal_ref",
    "plan_entry",
    "update_plan",
    "update_user_message",
    "update_user_message_text",
    "update_agent_message",
    "update_agent_message_text",
    "update_agent_thought",
    "update_agent_thought_text",
    "session_notification",
    "start_tool_call",
    "start_read_tool_call",
    "start_edit_tool_call",
    "update_tool_call",
    # Session capabilities
    "SessionCapabilities",
    "SessionCloseCapabilities",
    "SessionForkCapabilities",
    "SessionListCapabilities",
    "SessionResumeCapabilities",
    # Session mode/model state
    "SessionMode",
    "SessionModeState",
    "SessionModelState",
    # Exceptions
    "RequestError",
]
