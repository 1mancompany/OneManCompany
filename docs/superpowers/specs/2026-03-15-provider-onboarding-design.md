# Provider Onboarding — Four-Step Auth Flow

## Goal

Replace hardcoded OpenRouter/Anthropic provider selection with a data-driven four-step onboarding flow (inspired by openclaw), supporting all 9+ LLM providers with extensible auth methods.

## Architecture

Two independent data sources, each with a single responsibility:

- **`PROVIDER_REGISTRY`** (existing, `config.py`) — connection parameters: base_url, chat_class, env_key, health_url, health_auth. No UI concerns.
- **`AUTH_CHOICE_GROUPS`** (new, `auth_choices.py`) — UI/onboarding flow: provider groups, display labels, auth method options. No connection logic.

A startup-time validator ensures every `group_id` in `AUTH_CHOICE_GROUPS` has a corresponding entry in `PROVIDER_REGISTRY` (except `custom`). Mismatch logs a warning.

### Why Two Systems

Following openclaw's separation pattern. `PROVIDER_REGISTRY` serves `make_llm()`, heartbeat, and agent execution. `AUTH_CHOICE_GROUPS` serves the onboarding UI and auth flow. Different consumers, different shapes, decoupled evolution.

## Four-Step Flow

Both company-level (Settings panel) and employee-level (detail panel) use the same flow:

| Step | User Action | Frontend | Backend |
|------|-------------|----------|---------|
| 1. Select Provider | Pick from grouped list | Render from `GET /api/auth/providers` | Returns `AUTH_CHOICE_GROUPS` |
| 2. Select Auth Method | Pick api_key or oauth | Auto-skip if only one option | — |
| 3. Enter Credentials | Paste API key / OAuth flow | Input field or OAuth redirect | — |
| 4. Verify Connection | Wait for result | Call `POST /api/auth/verify` | Send `max_tokens=1` chat request |

After verification passes, frontend calls `POST /api/auth/apply` to persist.

## Data Structures

### AUTH_CHOICE_GROUPS (`auth_choices.py`)

```python
@dataclass
class AuthChoiceOption:
    value: str          # e.g. "openai-api-key", "anthropic-oauth"
    label: str          # e.g. "API Key"
    hint: str = ""      # e.g. "Paste your sk-... key"
    provider: str = ""  # explicit provider key, e.g. "openai", "google"
    auth_method: str = "api_key"  # "api_key" | "oauth" | "setup_token" | "codex"
    available: bool = True  # False = "Coming Soon" (Phase 2 OAuth)

@dataclass
class AuthChoiceGroup:
    group_id: str       # e.g. "openai", "anthropic" — matches PROVIDER_REGISTRY key
    label: str          # e.g. "OpenAI"
    hint: str           # e.g. "Codex OAuth + API key"
    choices: list[AuthChoiceOption]

AUTH_CHOICE_GROUPS: list[AuthChoiceGroup] = [
    AuthChoiceGroup("openai", "OpenAI", "Codex OAuth + API key", [
        AuthChoiceOption("openai-codex", "Codex OAuth", provider="openai", auth_method="codex", available=False),
        AuthChoiceOption("openai-api-key", "API Key", provider="openai", auth_method="api_key"),
    ]),
    AuthChoiceGroup("anthropic", "Anthropic", "OAuth + API key", [
        AuthChoiceOption("anthropic-setup-token", "OAuth (setup-token)", provider="anthropic", auth_method="setup_token"),
        AuthChoiceOption("anthropic-api-key", "API Key", provider="anthropic", auth_method="api_key"),
    ]),
    AuthChoiceGroup("kimi", "Moonshot AI (Kimi)", "API key", [
        AuthChoiceOption("kimi-api-key", "API Key", provider="kimi", auth_method="api_key"),
    ]),
    AuthChoiceGroup("deepseek", "DeepSeek", "API key", [
        AuthChoiceOption("deepseek-api-key", "API Key", provider="deepseek", auth_method="api_key"),
    ]),
    AuthChoiceGroup("qwen", "Qwen", "OAuth + API key", [
        AuthChoiceOption("qwen-oauth", "OAuth", provider="qwen", auth_method="oauth", available=False),
        AuthChoiceOption("qwen-api-key", "API Key", provider="qwen", auth_method="api_key"),
    ]),
    AuthChoiceGroup("zhipu", "ZhiPu (GLM)", "API key", [
        AuthChoiceOption("zhipu-api-key", "API Key", provider="zhipu", auth_method="api_key"),
    ]),
    AuthChoiceGroup("groq", "Groq", "API key", [
        AuthChoiceOption("groq-api-key", "API Key", provider="groq", auth_method="api_key"),
    ]),
    AuthChoiceGroup("together", "Together AI", "API key", [
        AuthChoiceOption("together-api-key", "API Key", provider="together", auth_method="api_key"),
    ]),
    AuthChoiceGroup("openrouter", "OpenRouter", "API key", [
        AuthChoiceOption("openrouter-api-key", "API Key", provider="openrouter", auth_method="api_key"),
    ]),
    AuthChoiceGroup("google", "Google Gemini", "OAuth + API key", [
        AuthChoiceOption("google-gemini-oauth", "Gemini CLI OAuth", provider="google", auth_method="oauth", available=False),
        AuthChoiceOption("google-gemini-api-key", "API Key", provider="google", auth_method="api_key"),
    ]),
    AuthChoiceGroup("minimax", "MiniMax", "OAuth + API key", [
        AuthChoiceOption("minimax-oauth", "OAuth", provider="minimax", auth_method="oauth", available=False),
        AuthChoiceOption("minimax-api-key", "API Key", provider="minimax", auth_method="api_key"),
    ]),
    AuthChoiceGroup("custom", "Custom Provider", "Any OpenAI/Anthropic compatible endpoint", [
        AuthChoiceOption("custom-api-key", "Custom API Key", provider="custom", auth_method="api_key"),
    ]),
]
```

### resolve_auth_choice()

```python
def resolve_auth_choice(choice_value: str) -> AuthChoiceOption | None:
    """Look up an AuthChoiceOption by its value string.

    Uses the explicit `provider` and `auth_method` fields on the option —
    no string parsing needed.
    """
    for group in AUTH_CHOICE_GROUPS:
        for option in group.choices:
            if option.value == choice_value:
                return option
    return None
```

### PROVIDER_REGISTRY (extended)

Existing `ProviderConfig` in `config.py` stays as-is. New entries added for `google` and `minimax`:

```python
# New entries in PROVIDER_REGISTRY
"google": ProviderConfig(
    base_url="https://generativelanguage.googleapis.com/v1beta/openai",
    chat_class="openai",
    env_key="google_api_key",
    health_url="https://generativelanguage.googleapis.com/v1beta/models",
),
"minimax": ProviderConfig(
    base_url="https://api.minimax.chat/v1",
    chat_class="openai",
    env_key="minimax_api_key",
    health_url="https://api.minimax.chat/v1/models",
),
```

New `Settings` fields: `google_api_key: str = ""`, `minimax_api_key: str = ""`.

### Custom Provider

The `custom` group requires additional fields in the verify/apply requests:

- `base_url` (required) — user-provided endpoint URL
- `chat_class` — auto-detected ("openai" or "anthropic") by probing both endpoints, or user-selected

Custom providers are stored as dynamic entries in `PROVIDER_REGISTRY` at runtime (keyed by normalized URL, e.g. `custom-api-example-com`), following openclaw's `resolveCustomProviderId()` pattern.

## API Endpoints

### `GET /api/auth/providers`

Returns the full `AUTH_CHOICE_GROUPS` as JSON. Frontend renders from this.

```json
[
  {
    "group_id": "openai",
    "label": "OpenAI",
    "hint": "Codex OAuth + API key",
    "choices": [
      {"value": "openai-codex", "label": "Codex OAuth", "hint": "", "available": false},
      {"value": "openai-api-key", "label": "API Key", "hint": "", "available": true}
    ]
  }
]
```

OAuth choices have `"available": false` in Phase 1 (Coming Soon).

### `POST /api/auth/verify`

Request:
```json
{
  "provider": "deepseek",
  "auth_method": "api_key",
  "api_key": "sk-...",
  "model": "deepseek-chat",
  "base_url": ""
}
```

`base_url` is optional — only used for `custom` provider. For known providers, looked up from `PROVIDER_REGISTRY`.

Response:
```json
{"ok": true}
// or
{"ok": false, "error": "Invalid API key"}
```

Implementation: calls shared `_probe_chat()` function — sends `messages=[{"role":"user","content":"hi"}]` with `max_tokens=1`. Timeout 30s.

### `POST /api/auth/apply`

Request:
```json
// Company-level
{"scope": "company", "choice": "openai-api-key", "api_key": "sk-...", "model": "gpt-4o"}

// Employee-level
{"scope": "employee", "employee_id": "00010", "choice": "deepseek-api-key", "api_key": "sk-...", "model": "deepseek-chat"}

// Custom provider
{"scope": "company", "choice": "custom-api-key", "api_key": "sk-...", "model": "my-model", "base_url": "https://api.example.com/v1", "chat_class": "openai"}
```

`model` is optional — if omitted, keeps existing model setting.
`base_url` and `chat_class` are required only for `custom` provider.

Actions:
- Company: writes key to Settings (env_key from PROVIDER_REGISTRY), optionally sets default provider/model
- Employee: writes to employee profile.yaml (api_provider, api_key, auth_method, llm_model), rebuilds agent

Error responses:
```json
{"error": "Employee not found", "code": "not_found"}
{"error": "Unknown provider", "code": "invalid_provider"}
{"error": "Choice not available (OAuth coming soon)", "code": "not_available"}
```

### Deleted Endpoints

- `PUT /api/employee/{id}/provider` — removed
- `PUT /api/employee/{id}/api-key` — removed

## Verification Function (`auth_verify.py`)

```python
async def _probe_chat(
    provider: str,
    api_key: str,
    model: str,
    timeout: float = 30.0,
) -> tuple[bool, str]:
    """Send minimal chat request to verify provider connectivity.

    Returns (ok, error_message).
    """
```

Shared by:
- `POST /api/auth/verify` — onboarding verification
- `heartbeat.py` — periodic health checks (replaces old `/models` endpoint checks)

### Heartbeat Migration

Old functions deleted:
- `_check_openrouter_key()`
- `_check_anthropic_key()`
- `_check_provider_key()`

Replaced by `_probe_chat()` calls. Cost: ~2-3 input tokens + 1 output token per check.

## OAuth Implementation (Phase 2)

Phase 1 delivers all api_key flows. OAuth options appear in UI as "Coming Soon" (disabled).

Phase 2 implements OAuth flows in `src/onemancompany/core/auth_apply/`:

| Module | Provider | OAuth Flow |
|--------|----------|------------|
| `openai_codex.py` | OpenAI | Device Code Flow (GitHub login) |
| `google_gemini.py` | Google Gemini | OAuth2 authorization code |
| `qwen_portal.py` | Qwen | Alibaba Cloud portal OAuth |
| `minimax_oauth.py` | MiniMax | MiniMax platform OAuth |

Anthropic "OAuth" is actually a setup-token paste (not true OAuth), can be implemented in Phase 1 if needed.

### auth_apply dispatch

Dispatch uses the `auth_method` field from `AuthChoiceOption`, not the `value` string:

```python
# auth_apply/__init__.py
APPLY_HANDLERS: dict[str, Callable] = {
    # Phase 1
    "api_key": apply_api_key,         # all *-api-key choices route here
    "setup_token": apply_setup_token, # anthropic setup-token
    # Phase 2
    "codex": apply_openai_codex,
    "oauth": apply_oauth,             # google, qwen, minimax OAuth flows
}

async def apply_auth_choice(choice_value: str, scope: str, **kwargs):
    option = resolve_auth_choice(choice_value)
    handler = APPLY_HANDLERS.get(option.auth_method)
    return await handler(option.provider, scope, **kwargs)
```

## API Key Priority (Employee Level)

1. Employee has own key → use it
2. No employee key → fallback to company-level key (looked up via `PROVIDER_REGISTRY[provider].env_key` → `Settings` field)

## Frontend Changes

### Settings Panel (Company-Level)

Current: hardcoded OpenRouter / Anthropic cards.
New: dynamic provider list from `/api/auth/providers`.

- Each provider shows as a card
- Click → expand auth method selection → credential input → verify → save
- Configured providers show green checkmark + masked key (`sk-...****`)

### Employee Detail Panel

Current: hardcoded `<select>` with two `<option>` values.
New: dynamic provider list, same four-step inline flow.

- Shows "Use company key" option when company has key for that provider
- Can also input independent key

## File Structure

```
src/onemancompany/core/
  auth_choices.py          # NEW: AUTH_CHOICE_GROUPS + resolve functions
  auth_verify.py           # NEW: _probe_chat() shared verifier
  auth_apply/              # NEW: auth flow apply handlers
    __init__.py             # dispatch: choice_value → apply function
    api_key.py              # generic api_key apply (all providers)
    openai_codex.py         # Phase 2
    google_gemini.py        # Phase 2
    qwen_portal.py          # Phase 2
    minimax_oauth.py        # Phase 2
  config.py                # MODIFIED: add google/minimax to PROVIDER_REGISTRY + Settings fields
  heartbeat.py             # MODIFIED: replace old checks with _probe_chat()

src/onemancompany/api/
  routes.py                # MODIFIED: delete old endpoints, add 3 new /api/auth/* endpoints

frontend/
  app.js                   # MODIFIED: dynamic provider UI for Settings + employee detail
```

## Deleted Code

- `routes.py`: `update_employee_provider()`, `update_employee_api_key()`
- `heartbeat.py`: `_check_openrouter_key()`, `_check_anthropic_key()`, `_check_provider_key()`
- `app.js`: hardcoded provider cards, hardcoded provider `<select>`
- No backward compatibility shims — clean removal

## Testing Strategy

- Unit tests for `auth_choices.py`: resolve function, startup validator (group_id ↔ PROVIDER_REGISTRY consistency)
- Unit tests for `auth_verify.py`: mock chat responses for success/failure/timeout
- Unit tests for `auth_apply/api_key.py`: company-level and employee-level apply
- Unit tests for `auth_apply/__init__.py`: dispatch routing (auth_method → handler)
- Integration tests for new API endpoints (verify, apply, providers list)
- Integration tests for heartbeat with new `_probe_chat()`
- Frontend: manual testing of four-step flow
