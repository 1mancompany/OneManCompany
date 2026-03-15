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

@dataclass
class AuthChoiceGroup:
    group_id: str       # e.g. "openai", "anthropic" — matches PROVIDER_REGISTRY key
    label: str          # e.g. "OpenAI"
    hint: str           # e.g. "Codex OAuth + API key"
    choices: list[AuthChoiceOption]

AUTH_CHOICE_GROUPS: list[AuthChoiceGroup] = [
    AuthChoiceGroup("openai", "OpenAI", "Codex OAuth + API key", [
        AuthChoiceOption("openai-codex", "Codex OAuth"),
        AuthChoiceOption("openai-api-key", "API Key"),
    ]),
    AuthChoiceGroup("anthropic", "Anthropic", "OAuth + API key", [
        AuthChoiceOption("anthropic-oauth", "OAuth (setup-token)"),
        AuthChoiceOption("anthropic-api-key", "API Key"),
    ]),
    AuthChoiceGroup("kimi", "Moonshot AI (Kimi)", "API key", [
        AuthChoiceOption("kimi-api-key", "API Key"),
    ]),
    AuthChoiceGroup("deepseek", "DeepSeek", "API key", [
        AuthChoiceOption("deepseek-api-key", "API Key"),
    ]),
    AuthChoiceGroup("qwen", "Qwen", "OAuth + API key", [
        AuthChoiceOption("qwen-oauth", "OAuth"),
        AuthChoiceOption("qwen-api-key", "API Key"),
    ]),
    AuthChoiceGroup("zhipu", "ZhiPu (GLM)", "API key", [
        AuthChoiceOption("zhipu-api-key", "API Key"),
    ]),
    AuthChoiceGroup("groq", "Groq", "API key", [
        AuthChoiceOption("groq-api-key", "API Key"),
    ]),
    AuthChoiceGroup("together", "Together AI", "API key", [
        AuthChoiceOption("together-api-key", "API Key"),
    ]),
    AuthChoiceGroup("openrouter", "OpenRouter", "API key", [
        AuthChoiceOption("openrouter-api-key", "API Key"),
    ]),
    AuthChoiceGroup("google", "Google Gemini", "OAuth + API key", [
        AuthChoiceOption("google-gemini-oauth", "Gemini CLI OAuth"),
        AuthChoiceOption("google-gemini-api-key", "API Key"),
    ]),
    AuthChoiceGroup("minimax", "MiniMax", "OAuth + API key", [
        AuthChoiceOption("minimax-oauth", "OAuth"),
        AuthChoiceOption("minimax-api-key", "API Key"),
    ]),
    AuthChoiceGroup("custom", "Custom Provider", "Any OpenAI/Anthropic compatible endpoint", [
        AuthChoiceOption("custom-api-key", "Custom API Key"),
    ]),
]
```

### resolve_auth_choice()

```python
def resolve_auth_choice(choice_value: str) -> tuple[str, str]:
    """Parse 'openai-api-key' → ('openai', 'api_key'), 'anthropic-oauth' → ('anthropic', 'oauth')."""
```

### PROVIDER_REGISTRY (unchanged)

Existing `ProviderConfig` in `config.py` — no modifications needed. New providers (google, minimax) added to the registry with their connection parameters.

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
  "model": "deepseek-chat"
}
```

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
{"scope": "company", "choice": "openai-api-key", "api_key": "sk-..."}

// Employee-level
{"scope": "employee", "employee_id": "00010", "choice": "deepseek-api-key", "api_key": "sk-..."}
```

Actions:
- Company: writes key to Settings (env_key from PROVIDER_REGISTRY), sets as default provider
- Employee: writes to employee profile.yaml (api_provider, api_key, auth_method), rebuilds agent

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

```python
# auth_apply/__init__.py
APPLY_HANDLERS: dict[str, Callable] = {
    # Phase 1
    "api_key": apply_api_key,
    # Phase 2
    "openai-codex": apply_openai_codex,
    "google-gemini-oauth": apply_google_gemini_oauth,
    "qwen-oauth": apply_qwen_oauth,
    "minimax-oauth": apply_minimax_oauth,
}
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
  config.py                # UNCHANGED: PROVIDER_REGISTRY stays as-is
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

- Unit tests for `auth_choices.py`: resolve function, startup validation
- Unit tests for `auth_verify.py`: mock chat responses for success/failure/timeout
- Unit tests for `auth_apply/api_key.py`: company-level and employee-level apply
- Integration tests for new API endpoints
- Frontend: manual testing of four-step flow
