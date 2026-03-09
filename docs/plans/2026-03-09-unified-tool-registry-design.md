# Unified Tool Registry Design

**Date:** 2026-03-09
**Status:** Approved

## Problem

Two separate tool systems exist:
1. **Internal tools** (`common_tools.py`) — hardcoded `BASE_TOOLS / GATED_TOOLS / COMMON_TOOLS` lists
2. **Asset tools** (`company/assets/tools/`) — folder+`tool.yaml`+`.py`, loaded via `manifest.yaml`

Two permission systems, two loading paths, two registration methods. Company-hosted (LangChain) and self-hosted (MCP) have different loading logic.

## Solution: Unified Registry (Option B)

Internal tools stay in code, asset tools in `company/assets/tools/`. Both register into a single `ToolRegistry` with unified permission filtering.

### Core: `src/onemancompany/core/tool_registry.py`

```python
@dataclass
class ToolMeta:
    name: str
    category: str          # "base" | "gated" | "role" | "asset"
    allowed_roles: list[str] | None   # None = all roles
    allowed_users: list[str] | None   # None = all users (asset tools)
    requires_permission: bool         # True = needs tool_permissions
    source: str            # "internal" | "asset"

class ToolRegistry:  # singleton
    def register_internal(tool, meta) -> None
    def load_asset_tools() -> None           # scans company/assets/tools/
    def get_tools_for(employee_id) -> list[StructuredTool]  # single entry point
```

### Permission Model

| Category | Rule | Examples |
|----------|------|----------|
| `base` | All employees | read, write, ls, dispatch_task, report_to_ceo |
| `gated` | Needs `tool_permissions` | bash, sandbox_* |
| `role` | Auto by employee role | HR→search_candidates, COO→register_asset, EA→ea_review_project |
| `asset` | `allowed_users` in tool.yaml (None=all) | gmail(all), roblox(00007 only) |

### Agent Class Unification

All agents use the same pattern:
```python
class EAAgent(BaseAgentRunner):
    def __init__(self):
        tools = tool_registry.get_tools_for(self.employee_id)
        self._agent = create_react_agent(model=make_llm(self.employee_id), tools=tools)
```

Delete: `COMMON_TOOLS`, `BASE_TOOLS`, `GATED_TOOLS`, `HIRING_TOOLS`, hardcoded tool lists in COO/CSO agents, `load_employee_custom_tools()`, `manifest.yaml` custom_tools mechanism.

### MCP Server Rewrite

From ~200 lines of hand-written tool definitions to a generic registry→MCP bridge (~50 lines):
```python
for tool in tool_registry.get_tools_for(employee_id):
    mcp.add_tool(name=tool.name, description=tool.description,
                 fn=_make_bridge(tool), input_schema=tool.args_schema.schema())
```

Remove `OMC_TOOLS` env var. MCP server queries registry directly.

## Files Changed

| File | Action |
|------|--------|
| `src/onemancompany/core/tool_registry.py` | **New** — ToolRegistry + ToolMeta |
| `src/onemancompany/agents/common_tools.py` | **Edit** — remove hardcoded lists, register via registry |
| `src/onemancompany/agents/ea_agent.py` | **Edit** — use registry.get_tools_for() |
| `src/onemancompany/agents/hr_agent.py` | **Edit** — same + register HR tools |
| `src/onemancompany/agents/coo_agent.py` | **Edit** — same + register COO tools |
| `src/onemancompany/agents/cso_agent.py` | **Edit** — same + register CSO tools |
| `src/onemancompany/agents/base.py` | **Edit** — GenericAgentRunner uses registry |
| `src/onemancompany/tools/mcp/server.py` | **Rewrite** — generic bridge |
| `src/onemancompany/tools/mcp/config_builder.py` | **Edit** — remove OMC_TOOLS |
| `src/onemancompany/core/config.py` | **Edit** — remove load_employee_custom_tools() |
| `src/onemancompany/agents/onboarding.py` | **Edit** — use registry API |
| `employees/*/tools/manifest.yaml` | **Delete** |

## Unchanged

- `@tool` function implementations stay in their current modules
- `tool.yaml` `allowed_users` field
- `profile.yaml` `tool_permissions` field
- Frontend tool management UI
