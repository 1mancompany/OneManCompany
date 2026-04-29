"""Unit tests for the ACP adapter re-export surface.

All imports MUST come from ``onemancompany.acp.adapter``, never from ``acp.*``
directly.  This test file validates that every re-exported symbol is present
and is the correct type/callable.
"""
import inspect
import pytest

import onemancompany.acp.adapter as adapter


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _attr(name: str):
    """Return the attribute from adapter, fail with a clear message if missing."""
    assert hasattr(adapter, name), f"adapter is missing: {name}"
    return getattr(adapter, name)


# ---------------------------------------------------------------------------
# Protocol classes
# ---------------------------------------------------------------------------

class TestProtocols:
    def test_agent_is_protocol(self):
        Agent = _attr("Agent")
        assert inspect.isclass(Agent)

    def test_client_is_protocol(self):
        Client = _attr("Client")
        assert inspect.isclass(Client)


# ---------------------------------------------------------------------------
# Connection / runner callables
# ---------------------------------------------------------------------------

class TestConnectionHelpers:
    def test_connect_to_agent_callable(self):
        assert callable(_attr("connect_to_agent"))

    def test_run_agent_callable(self):
        assert callable(_attr("run_agent"))

    def test_client_side_connection_is_class(self):
        assert inspect.isclass(_attr("ClientSideConnection"))

    def test_agent_side_connection_is_class(self):
        assert inspect.isclass(_attr("AgentSideConnection"))


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------

class TestTransport:
    def test_spawn_stdio_transport_callable(self):
        assert callable(_attr("spawn_stdio_transport"))

    def test_stdio_streams_callable(self):
        assert callable(_attr("stdio_streams"))

    def test_spawn_stdio_connection_callable(self):
        assert callable(_attr("spawn_stdio_connection"))

    def test_spawn_agent_process_callable(self):
        assert callable(_attr("spawn_agent_process"))

    def test_spawn_client_process_callable(self):
        assert callable(_attr("spawn_client_process"))


# ---------------------------------------------------------------------------
# Protocol metadata
# ---------------------------------------------------------------------------

class TestMeta:
    def test_protocol_version_is_int(self):
        v = _attr("PROTOCOL_VERSION")
        assert isinstance(v, int)

    def test_agent_methods_is_dict(self):
        assert isinstance(_attr("AGENT_METHODS"), dict)

    def test_client_methods_is_dict(self):
        assert isinstance(_attr("CLIENT_METHODS"), dict)


# ---------------------------------------------------------------------------
# Request / Response schema classes
# ---------------------------------------------------------------------------

SCHEMA_CLASSES = [
    "InitializeRequest", "InitializeResponse",
    "NewSessionRequest", "NewSessionResponse",
    "LoadSessionRequest", "LoadSessionResponse",
    "ResumeSessionRequest", "ResumeSessionResponse",
    "ForkSessionRequest", "ForkSessionResponse",
    "CloseSessionRequest", "CloseSessionResponse",
    "PromptRequest", "PromptResponse",
    "RequestPermissionRequest", "RequestPermissionResponse",
    "AgentCapabilities",
    "ClientCapabilities",
    "Implementation",
    "ListSessionsRequest", "ListSessionsResponse",
    "SetSessionModeRequest", "SetSessionModeResponse",
    "SetSessionModelRequest", "SetSessionModelResponse",
    "SetSessionConfigOptionSelectRequest",
    "SetSessionConfigOptionBooleanRequest",
    "SetSessionConfigOptionResponse",
    "AuthenticateRequest", "AuthenticateResponse",
    "CancelNotification",
    "SessionNotification",
    "ReadTextFileRequest", "ReadTextFileResponse",
    "WriteTextFileRequest", "WriteTextFileResponse",
    "CreateTerminalRequest", "CreateTerminalResponse",
    "TerminalOutputRequest", "TerminalOutputResponse",
    "WaitForTerminalExitRequest", "WaitForTerminalExitResponse",
    "KillTerminalRequest", "KillTerminalResponse",
    "ReleaseTerminalRequest", "ReleaseTerminalResponse",
    # Session update types
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
    # Misc
    "EnvVariable",
    "HttpMcpServer",
    "SseMcpServer",
    "McpServerStdio",
    "PermissionOption",
    "Usage",
    "Cost",
]


@pytest.mark.parametrize("class_name", SCHEMA_CLASSES)
def test_schema_class_is_exported(class_name: str):
    """Each schema class must be importable from the adapter and be a class."""
    obj = _attr(class_name)
    assert inspect.isclass(obj), f"{class_name} should be a class, got {type(obj)}"


# ---------------------------------------------------------------------------
# Literal / enum re-exports
# ---------------------------------------------------------------------------

LITERAL_ATTRS = ["ToolCallStatus", "ToolKind", "PlanEntryPriority", "PlanEntryStatus", "StopReason"]


@pytest.mark.parametrize("name", LITERAL_ATTRS)
def test_literal_attr_is_exported(name: str):
    """Literal type aliases or enums must be accessible from the adapter."""
    _attr(name)  # just assert presence


# ---------------------------------------------------------------------------
# Builder / helper callables
# ---------------------------------------------------------------------------

HELPER_CALLABLES = [
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
]


@pytest.mark.parametrize("name", HELPER_CALLABLES)
def test_helper_is_callable(name: str):
    fn = _attr(name)
    assert callable(fn), f"{name} should be callable"


# ---------------------------------------------------------------------------
# Spot-check: builder functions produce expected types
# ---------------------------------------------------------------------------

class TestBuilderSmoke:
    def test_text_block_returns_text_content_block(self):
        TextContentBlock = _attr("TextContentBlock")
        text_block = _attr("text_block")
        result = text_block("hello")
        assert isinstance(result, TextContentBlock)
        assert result.text == "hello"

    def test_plan_entry_returns_plan_entry(self):
        PlanEntry = _attr("PlanEntry")
        plan_entry = _attr("plan_entry")
        result = plan_entry("do something")
        assert isinstance(result, PlanEntry)

    def test_update_plan_returns_agent_plan_update(self):
        AgentPlanUpdate = _attr("AgentPlanUpdate")
        plan_entry = _attr("plan_entry")
        update_plan = _attr("update_plan")
        result = update_plan([plan_entry("step 1")])
        assert isinstance(result, AgentPlanUpdate)

    def test_update_agent_message_text_returns_agent_message_chunk(self):
        AgentMessageChunk = _attr("AgentMessageChunk")
        update_agent_message_text = _attr("update_agent_message_text")
        result = update_agent_message_text("hello world")
        assert isinstance(result, AgentMessageChunk)

    def test_protocol_version_positive(self):
        assert adapter.PROTOCOL_VERSION > 0


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------

class TestException:
    def test_request_error_is_exception(self):
        RequestError = _attr("RequestError")
        assert issubclass(RequestError, Exception)


# ---------------------------------------------------------------------------
# __all__ completeness
# ---------------------------------------------------------------------------

class TestAllCompleteness:
    def test_all_defined(self):
        """Every name in __all__ must be importable from the adapter module."""
        for name in adapter.__all__:
            assert hasattr(adapter, name), f"__all__ lists {name!r} but it is not present in the module"
