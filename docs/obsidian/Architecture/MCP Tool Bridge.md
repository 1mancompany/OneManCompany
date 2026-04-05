---
tags: [architecture, mcp, self-hosted]
source: src/onemancompany/mcp/, MEMORY.md
---

# MCP Tool Bridge

Exposes company tools to self-hosted (Claude CLI) employees via MCP stdio protocol.

## Architecture

One MCP stdio subprocess per Claude CLI session. All tool calls proxied via `POST /api/internal/tool-call`.

## How It Works

1. `build_mcp_config()` generates per-session MCP config with env vars
2. `write_mcp_config()` writes config to disk
3. `claude --print --mcp-config {path}` starts Claude with MCP bridge
4. Tool calls go: Claude CLI → MCP stdio → HTTP → backend `ainvoke()`
5. Backend sets context vars (`_current_vessel`, `_current_task_id`) before execution

## Tool Permissions

| Employee Type | Access |
|--------------|--------|
| Founding (00002-00005) | All tools |
| Regular | `BASE_TOOLS` + `GATED_TOOLS` filtered by `tool_permissions` |

## Key Points

- Self-hosted employees do NOT need OAuth or API keys — Claude CLI manages its own auth
- Generic endpoint: one route handles all tool types via LangChain `StructuredTool.ainvoke()`
- Env vars injected: `OMC_EMPLOYEE_ID`, `OMC_TASK_ID`, `OMC_SERVER_URL`, `OMC_PROJECT_DIR`

## Related
- [[Vessel System]] — Self-hosted hosting mode
- [[Agent Loop]] — ClaudeSessionExecutor
