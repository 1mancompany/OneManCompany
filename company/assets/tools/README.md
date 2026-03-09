# Tool Development Guide

This guide explains how to create a custom tool for OneManCompany's equipment room. Tools are LangChain `@tool` functions that company-hosted employees can use during task execution.

## Directory Structure

```
company/assets/tools/
  └── your_tool/
      ├── tool.yaml          # Required — tool manifest
      ├── your_tool.py       # Required — LangChain tool functions (filename must match folder name)
      └── icon.png            # Optional — 32x32 pixel art icon
```

## Step 1: Create tool.yaml

The manifest declares metadata and UI sections for the tool detail page.

```yaml
# Required fields
id: your_tool                    # Unique identifier (must match folder name)
name: Your Tool Name             # Display name
description: >
  What this tool does. This text is shown in the tool list
  and injected into the employee's prompt.
added_by: CEO
type: langchain_module           # Always "langchain_module" for Python tools
sprite: desk_equipment           # Pixel art sprite name for the office view

# Access control (pick one):
# Option A: Open to all employees (omit allowed_users entirely)
# Option B: Restrict to specific employees
allowed_users:
  - "00007"

# Optional: OAuth configuration (auto-generates login UI)
oauth:
  service_name: your_service     # Token cache key
  authorize_url: https://...     # OAuth authorize endpoint
  token_url: https://...         # OAuth token endpoint
  scopes: scope1 scope2          # Space-separated scopes
  client_id_env: YOUR_CLIENT_ID  # Env var name for client ID
  client_secret_env: YOUR_SECRET # Env var name for client secret

# Optional: Environment variables (auto-generates config UI)
env_vars:
  - name: YOUR_API_KEY
    label: API Key
    secret: true                 # Masks input field
    placeholder: sk-...
  - name: YOUR_ENDPOINT
    label: Endpoint URL
    secret: false
```

### UI Sections (auto-generated from tool.yaml)

The tool detail page dynamically renders sections based on what's declared:

| tool.yaml key | UI Section | Description |
|---|---|---|
| `oauth:` | OAuth login/disconnect button | Handles full OAuth flow |
| `env_vars:` | Credential input forms | Save env vars to `.env` |
| `allowed_users:` | Access control display | Shows who can use the tool |
| _(always)_ | Files listing | Shows source files |
| _(always)_ | Definition | Shows raw tool.yaml |

To add a new section type: add a backend section builder in `routes.py:get_tool_definition()` and a frontend renderer in `app.js:_toolSectionRenderers`.

## Step 2: Write Tool Functions

Create `your_tool/your_tool.py` with LangChain `@tool` decorated functions.

```python
"""Your Tool — brief description.

All @tool functions in this file are auto-discovered and loaded
as LangChain tools for employees who have access.
"""
from __future__ import annotations

from langchain_core.tools import tool


@tool
def your_action(param1: str, param2: int = 10) -> dict:
    """One-line description shown to the LLM.

    Args:
        param1: Description of param1.
        param2: Description of param2 (default: 10).
    """
    # Your implementation here
    return {"status": "ok", "result": "..."}
```

### Key Rules

- **Filename must match folder name**: `tools/gmail/gmail.py`, not `tools/gmail/main.py`
- **Use `@tool` decorator** from `langchain_core.tools`
- **Return dicts**, not strings — the agent sees the dict as tool output
- **All `BaseTool` instances** in the module are auto-collected (not just the eponymous one)
- **No heavy imports at module level** — use lazy imports inside functions
- **No external SDK dependencies** if possible — prefer `urllib.request` for HTTP APIs

### With OAuth

If your tool uses OAuth, integrate with `core/oauth.py`:

```python
from langchain_core.tools import tool


def _your_oauth_config():
    try:
        from onemancompany.core.oauth import OAuthServiceConfig
        return OAuthServiceConfig(
            service_name="your_service",          # Must match tool.yaml
            authorize_url="https://...",
            token_url="https://...",
            scopes="read write",
            client_id_env="YOUR_CLIENT_ID",       # Must match tool.yaml
            client_secret_env="YOUR_SECRET",
        )
    except ImportError:
        return None


def _get_auth_header() -> tuple[dict, str | None]:
    """Returns (headers_dict, error_message_or_None)."""
    config = _your_oauth_config()
    if not config:
        return {}, "OAuth module not available"
    from onemancompany.core.oauth import ensure_oauth_token
    token = ensure_oauth_token(config)
    if token is None:
        return {}, "Authorization required. A popup has been sent to CEO."
    return {"Authorization": f"Bearer {token}"}, None


@tool
def your_api_call(query: str) -> dict:
    """Call the API."""
    auth, err = _get_auth_header()
    if err:
        return {"status": "error", "message": err}
    # Use auth headers in your request...
```

When `ensure_oauth_token()` returns `None`, it automatically sends an OAuth popup to the CEO's browser. On next tool invocation, the token will be available.

### With Environment Variables

For tools that need API keys (no OAuth):

```python
import os
from langchain_core.tools import tool

@tool
def your_action(query: str) -> dict:
    """Do something."""
    api_key = os.environ.get("YOUR_API_KEY", "")
    if not api_key:
        return {"status": "error", "message": "YOUR_API_KEY not configured"}
    # Use api_key...
```

The CEO configures the key through the tool detail UI (if `env_vars:` is declared in tool.yaml) or by setting the env var directly.

## Step 3: Assign to Employees

Company-hosted employees need the tool in their `manifest.yaml`:

```yaml
# employees/{id}/tools/manifest.yaml
custom_tools:
  - gmail
  - your_tool
```

For founding employees (HR, COO, EA, CSO), tools with open access are automatically available through the equipment room. Regular employees need explicit assignment via `manifest.yaml` or the `manage_tool_access` tool.

## Step 4: Test

```bash
# Verify the tool module loads without errors
.venv/bin/python -c "from company.assets.tools.your_tool.your_tool import *; print('OK')"

# Verify it appears in the equipment room
curl -s http://localhost:8000/api/tools/your_tool/definition | python -m json.tool

# Check sections render correctly
curl -s http://localhost:8000/api/tools/your_tool/definition | \
  python -c "import sys,json; d=json.load(sys.stdin); print([s['type'] for s in d['sections']])"
```

## Reference: Existing Tools

| Tool | OAuth | Env Vars | Description |
|---|---|---|---|
| `gmail` | Google OAuth | — | Gmail search, read, send, draft |
| `roblox_cloud` | Roblox OAuth | — | Roblox Open Cloud API |
| `opensandbox` | — | — | Container-based code execution |
| `pm_tools` | — | — | Project management utilities |
