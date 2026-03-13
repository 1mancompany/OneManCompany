# Talent Market API Integration Design

## Goal

Upgrade the HR recruitment flow to use the new Talent Market MCP API (SSE at `https://api.carbonkites.com/mcp/`). When API key is set, use AI-powered search and batch purchase. When not set, fall back to local `talent_market/talents/` directory. Add a talent pool viewer modal on HR's detail page.

## Architecture

### 1. TalentMarketClient (`agents/recruitment.py`)

Replace the bare `_boss_session` / `_call_boss_online()` with a `TalentMarketClient` class that wraps all 7 MCP tools.

```python
class TalentMarketClient:
    """Persistent SSE MCP client for Talent Market API."""

    def __init__(self):
        self._session: ClientSession | None = None
        self._stack: AsyncExitStack | None = None
        self._api_key: str = ""

    async def connect(self, url: str, api_key: str) -> None:
        """Establish SSE connection. No-op if already connected."""
        ...

    async def disconnect(self) -> None:
        """Tear down SSE connection."""
        ...

    @property
    def connected(self) -> bool:
        return self._session is not None

    async def _call(self, tool_name: str, **kwargs) -> dict:
        """Call an MCP tool, auto-injecting api_key."""
        if not self._session:
            raise RuntimeError("Not connected to Talent Market")
        kwargs["api_key"] = self._api_key
        result = await self._session.call_tool(tool_name, arguments=kwargs)
        # Parse JSON from result.content
        ...

    # --- Public methods (1:1 with MCP tools) ---

    async def search(self, job_description: str) -> dict:
        """search_candidates — AI-powered semantic search."""
        return await self._call("search_candidates", job_description=job_description)

    async def list_available(self, role: str = "", skills: str = "",
                             page: int = 1, page_size: int = 20) -> dict:
        """list_available_talents — browse with filters."""
        return await self._call("list_available_talents",
                                role=role, skills=skills, page=page, page_size=page_size)

    async def list_my_talents(self) -> dict:
        """list_my_talents — purchased talents."""
        return await self._call("list_my_talents")

    async def get_info(self, talent_id: str) -> dict:
        """get_talent_info — full profile."""
        return await self._call("get_talent_info", talent_id=talent_id)

    async def get_cv(self, talent_id: str) -> dict:
        """get_talent_cv — structured CV for onboarding."""
        return await self._call("get_talent_cv", talent_id=talent_id)

    async def hire(self, talent_ids: list[str], session_id: str = "") -> dict:
        """hire_talents — batch purchase."""
        args = {"talent_ids": talent_ids, "api_key": self._api_key}
        if session_id:
            args["session_id"] = session_id
        return await self._call("hire_talents", **args)

    async def onboard(self, talent_id: str) -> dict:
        """onboard_talent — get git repo URL."""
        return await self._call("onboard_talent", talent_id=talent_id)
```

**Module-level singleton:**
```python
talent_market = TalentMarketClient()
```

**Lifecycle:**
- `start_boss_online()` → renamed to `start_talent_market()`, calls `talent_market.connect(url, api_key)`
- `stop_boss_online()` → renamed to `stop_talent_market()`, calls `talent_market.disconnect()`
- Called from app lifespan (main.py) as before

**Backward compat:** Delete `_boss_session`, `_boss_cleanup`, `_call_boss_online()`. Update `search_candidates` tool to use `talent_market.search()`.

### 2. search_candidates Tool Update

The existing `search_candidates` LangChain tool stays but uses the new client:

```python
@tool
async def search_candidates(job_description: str) -> dict:
    if talent_market.connected:
        grouped = await talent_market.search(job_description)
        # Stash results in _last_search_results for submit_shortlist
        ...
        return grouped
    else:
        # Fallback: local talent packages
        from onemancompany.core.config import list_available_talents, load_talent_profile
        ...
```

The `session_id` from the search response is stashed alongside the batch for later use in `hire_talents`.

### 3. Batch Hire Flow with Purchase + Clone

**New function in `onboarding.py`:**
```python
async def clone_talent_repo(repo_url: str, talent_id: str) -> Path:
    """Clone a talent repo into talent_market/talents/{talent_id}/.

    If the directory already exists, does a git pull instead.
    Returns the local talent directory path.
    """
    target = TALENTS_DIR / talent_id
    if target.exists():
        # git pull to update
        subprocess.run(["git", "-C", str(target), "pull"], check=True)
    else:
        subprocess.run(["git", "clone", repo_url, str(target)], check=True)
    return target
```

**Updated `batch_hire_candidates` in `routes.py`:**

```
POST /api/hire/batch
{
  "batch_id": "abc123",
  "candidate_ids": ["alex-frontend", "bob-backend"],
  "session_id": "ses_xxx"  // from search_candidates response
}
```

Flow:
1. Look up candidates from `pending_candidates[batch_id]`
2. If `talent_market.connected`:
   a. Call `talent_market.hire(talent_ids, session_id)` → purchase
   b. If error (insufficient balance), return error to CEO
   c. For each hired talent: `talent_market.onboard(talent_id)` → get `repo_url`
   d. `clone_talent_repo(repo_url, talent_id)` → local path
3. For each talent (whether cloned or already local):
   a. `resolve_talent_dir(talent_id)` → local path
   b. `execute_hire(talent_dir=path, ...)` → create employee

**Error handling:**
- `hire_talents` insufficient balance → return `{error, balance, required, shortfall}` to frontend
- `git clone` failure → skip that talent, report partial failure
- `onboard_talent` failure → skip that talent, report partial failure

### 4. resolve_talent_dir Update (`onboarding.py`)

No change needed — `_LEGACY_TALENTS_DIR` already points to `talent_market/talents/`. After `clone_talent_repo` puts the repo there, `resolve_talent_dir(talent_id)` will find it.

### 5. Talent Pool API Endpoint

```
GET /api/talent-pool
```

Response:
```json
{
  "source": "api" | "local",
  "talents": [
    {
      "talent_id": "alex-frontend",
      "name": "Alex Frontend",
      "role": "Senior Frontend Developer",
      "skills": ["react", "typescript"],
      "status": "purchased" | "local",
      "purchased_at": "2026-03-10T12:00:00Z"  // only for API source
    }
  ]
}
```

Logic:
- If `talent_market.connected`: call `talent_market.list_my_talents()`, map to response format, `source: "api"`
- Else: scan local `TALENTS_DIR`, read each `profile.yaml`, `source: "local"`

### 6. Frontend: Talent Pool Modal

**HR detail page button:**
- Add "人才库" button in the HR employee modal (alongside existing action buttons)
- Click → `GET /api/talent-pool` → open talent pool modal

**Talent pool modal:**
- Independent modal (like candidate shortlist modal)
- Header: "人才库" + source badge ("API" green or "本地" gray)
- List of talent cards:
  - Name, role, skill tags (pixel-art styled badges)
  - Status indicator (purchased / local)
- Close button (X)
- No hire action from this modal (hiring is through the normal search → shortlist → batch hire flow)

### 7. Session ID Tracking

The `search_candidates` response includes a `session_id` when AI-generated talents are returned. This must be passed to `hire_talents` for those talents. Track it in `_pending_project_ctx`:

```python
# In submit_shortlist:
_pending_project_ctx[batch_id] = {
    "project_id": ...,
    "session_id": grouped.get("session_id", ""),  # NEW
}
```

### 8. Settings Integration

The existing Settings panel already handles `talent_market.api_key` via `POST /api/settings` with `provider: "talent_market"`. On key change:
- Disconnect old session
- Reconnect with new key (or skip if key removed)

This is already handled by the lifespan + settings endpoint. No change needed except ensuring `stop_talent_market()` / `start_talent_market()` are called on key update.

**New: reconnect on API key change:**
In the `/api/settings` handler, after updating `talent_market.api_key`:
```python
if provider == "talent_market":
    await stop_talent_market()
    if new_api_key:
        await start_talent_market()
```

### 9. Files Affected

| File | Change |
|------|--------|
| `agents/recruitment.py` | Replace `_boss_session` with `TalentMarketClient` class. Update `search_candidates` tool. Track `session_id`. Absorb `HireRequest`, `InterviewRequest`, `InterviewResponse` Pydantic models from deleted `boss_online.py`. |
| `agents/onboarding.py` | Add `clone_talent_repo()`. No other changes. |
| `api/routes.py` | Update `batch_hire_candidates` with purchase+clone. Add `GET /api/talent-pool`. Reconnect on key change. Update imports from `boss_online` → `recruitment`. |
| `frontend/app.js` | HR modal "人才库" button. Talent pool modal render+open/close. |
| `frontend/style.css` | Talent pool modal styles. |
| `frontend/index.html` | Talent pool modal HTML skeleton. |
| `main.py` | Rename `start_boss_online` → `start_talent_market` in lifespan. |
| `talent_market/boss_online.py` | **Delete entirely.** |
| `tests/unit/talent_market/test_boss_online.py` | **Delete entirely.** |

### 10. What Gets Deleted

- `_boss_session`, `_boss_cleanup` module globals in `recruitment.py`
- `_call_boss_online()` function
- `start_boss_online()` / `stop_boss_online()` (replaced by `start_talent_market()` / `stop_talent_market()`)
- `talent_market/boss_online.py` — **entire file deleted**. The local MCP server, keyword search, and `_talent_to_candidate()` are replaced by `TalentMarketClient` (API) and `recruitment.py`'s `_talent_to_candidate()` (local fallback)
- `tests/unit/talent_market/test_boss_online.py` — tests for deleted file
- `recruitment.py`'s `_talent_to_candidate()` — consolidated: the single local-talent-to-candidate conversion lives here (already exists, stays)

### 11. Pydantic Models Migration (SSOT)

`boss_online.py` currently defines Pydantic models used by `routes.py`. These move to `agents/recruitment.py` (co-located with the tools that produce/consume them):

| Model | Used by | Action |
|-------|---------|--------|
| `HireRequest` | `routes.py` `hire_candidate()` | Move to `recruitment.py` |
| `InterviewRequest` | `routes.py` `interview_candidate()` | Move to `recruitment.py` |
| `InterviewResponse` | `routes.py` `interview_candidate()` | Move to `recruitment.py` |
| `CandidateProfile` | `recruitment.py` (local fallback) | Already partially duplicated in `_talent_to_candidate()` dict output — keep as dict, no Pydantic model needed |
| `CandidateSearchRequest/Response` | `boss_online.py` only | Delete (unused outside deleted file) |
| `CandidateShortlist` | `boss_online.py` only | Delete (unused outside deleted file) |
| `HireResponse` | `boss_online.py` only | Delete (unused outside deleted file) |
| `CandidateSkill`, `CandidateTool` | `CandidateProfile` | Delete (API returns its own format; local fallback uses plain dicts) |
| `RoleType`, `SpriteType`, `SPRITES` | `boss_online.py` only | Delete |

### 12. Local Talent Listing — Single Code Path

`config.py` `list_available_talents()` and `load_talent_profile()` remain as the **sole** local talent reading functions. The `talent_market/boss_online.py` equivalents (`_load_all_talents()`, `_build_search_text()`, `_compute_relevance()`, `_tokenize()`) are deleted — they duplicate `config.py` functionality.

The `search_candidates` tool's local fallback branch already uses `config.py`'s `list_available_talents()` + `load_talent_profile()`. This becomes the only local talent path.

`GET /api/talent-pool` local fallback also uses `config.py` functions — no second scanning implementation.

### 13. What Does NOT Change

- `onboarding.py` `execute_hire()` — unchanged, still takes `talent_dir` parameter
- `config.py` `TALENTS_DIR`, `list_available_talents()`, `load_talent_profile()` — unchanged, single source of truth for local talent reading
- The candidate shortlist → CEO selection UI — unchanged
- The interview flow — unchanged
