# Talent Market API Integration — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the bare `_boss_session` MCP client with a `TalentMarketClient` class wrapping all 7 Talent Market API tools, add purchase+clone to batch hire, add talent pool API+modal, and delete `boss_online.py` entirely (SSOT).

**Architecture:** `TalentMarketClient` singleton in `recruitment.py` manages SSE connection and wraps all 7 MCP tools. `search_candidates` tool uses client when connected, falls back to `config.py` local talent functions. Batch hire calls `hire_talents` API → `onboard_talent` → `git clone` before `execute_hire`. Interview models (`HireRequest`, `InterviewRequest`, `InterviewResponse`, `CandidateProfile`, `CandidateSkill`) migrate from `boss_online.py` to `recruitment.py`.

**Tech Stack:** Python 3.12, MCP SDK (SSE client), FastAPI, LangChain, vanilla JS frontend

**Spec:** `docs/superpowers/specs/2026-03-13-talent-market-api-design.md`

---

## Chunk 1: Backend — TalentMarketClient + Model Migration + Cleanup

### Task 1: Migrate Pydantic Models from `boss_online.py` to `recruitment.py`

Move models that `routes.py` actually uses. Delete all others.

**Files:**
- Modify: `src/onemancompany/agents/recruitment.py:1-18` (add models + imports)
- Modify: `src/onemancompany/api/routes.py:29` (update import)
- Delete: `src/onemancompany/talent_market/boss_online.py`
- Delete: `tests/unit/talent_market/test_boss_online.py`

**Context:** `routes.py` line 29 imports `HireRequest, InterviewRequest, InterviewResponse` from `boss_online`. The `interview_candidate` endpoint (line 3958) uses `InterviewRequest` which has a nested `CandidateProfile` with `.skill_set` containing `CandidateSkill` objects (accessed via `s.name`). So `CandidateProfile`, `CandidateSkill`, and `CandidateTool` must also be migrated as Pydantic models.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/agents/test_recruitment_models.py`:

```python
"""Tests for Pydantic models migrated from boss_online.py."""
from onemancompany.agents.recruitment import (
    CandidateSkill,
    CandidateProfile,
    HireRequest,
    InterviewRequest,
    InterviewResponse,
)


class TestCandidateSkill:
    def test_create(self):
        s = CandidateSkill(name="python", description="Python skill")
        assert s.name == "python"
        assert s.code == ""


class TestCandidateProfile:
    def test_create_minimal(self):
        p = CandidateProfile(
            id="test",
            name="Test",
            role="Engineer",
            experience_years=3,
            personality_tags=["creative"],
            system_prompt="You are a dev",
            skill_set=[CandidateSkill(name="python", description="Python")],
            tool_set=[],
            sprite="employee_blue",
            llm_model="test-model",
            jd_relevance=0.9,
        )
        assert p.id == "test"
        assert p.skill_set[0].name == "python"


class TestHireRequest:
    def test_create(self):
        r = HireRequest(batch_id="b1", candidate_id="c1")
        assert r.batch_id == "b1"
        assert r.nickname == ""


class TestInterviewRequest:
    def test_create(self):
        candidate = CandidateProfile(
            id="c1", name="Test", role="Engineer",
            experience_years=3, personality_tags=[],
            system_prompt="prompt", skill_set=[], tool_set=[],
            sprite="employee_blue", llm_model="m", jd_relevance=0.8,
        )
        req = InterviewRequest(question="Tell me about yourself", candidate=candidate)
        assert req.question == "Tell me about yourself"
        assert req.images == []


class TestInterviewResponse:
    def test_create(self):
        r = InterviewResponse(candidate_id="c1", question="Q", answer="A")
        assert r.answer == "A"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/agents/test_recruitment_models.py -v`
Expected: FAIL with `ImportError: cannot import name 'CandidateSkill'`

- [ ] **Step 3: Add Pydantic models to `recruitment.py`**

Add these models at the top of `src/onemancompany/agents/recruitment.py`, after the existing imports (line 18). Copy from `boss_online.py` lines 32-119, keeping only the models used by `routes.py`:

```python
from pydantic import BaseModel, Field
from typing import Literal

# --- Pydantic models (migrated from talent_market/boss_online.py) ---
# Used by routes.py for hire_candidate, interview_candidate endpoints.

RoleType = Literal["Engineer", "Designer", "Analyst", "DevOps", "QA", "Marketing"]
SpriteType = Literal[
    "employee_blue", "employee_red", "employee_green",
    "employee_purple", "employee_orange",
]


class CandidateSkill(BaseModel):
    """A skill the candidate possesses."""
    name: str = Field(description="Skill identifier, e.g. 'python', 'figma'")
    description: str = Field(description="Human-readable skill description")
    code: str = Field(default="", description="Example code snippet showing proficiency")


class CandidateTool(BaseModel):
    """A tool the candidate can operate."""
    name: str = Field(description="Tool identifier, e.g. 'code_review', 'debugger'")
    description: str = Field(description="What the tool does")
    code: str = Field(default="", description="Example code snippet showing tool usage")


class CandidateProfile(BaseModel):
    """Full candidate profile — used by interview endpoint."""
    id: str = Field(description="Talent package ID")
    name: str = Field(description="Talent name")
    role: RoleType = Field(description="Primary role")
    experience_years: int = Field(ge=0, le=30, description="Years of work experience")
    personality_tags: list[str] = Field(description="Personality traits")
    system_prompt: str = Field(description="LLM persona prompt")
    skill_set: list[CandidateSkill] = Field(description="Skills")
    tool_set: list[CandidateTool] = Field(description="Tools")
    sprite: SpriteType = Field(description="Pixel art avatar type")
    llm_model: str = Field(description="LLM model for this candidate")
    jd_relevance: float = Field(ge=0.0, le=1.0, description="JD match score (0.0-1.0)")
    remote: bool = Field(default=False, description="Whether this is a remote worker")
    talent_id: str = Field(default="", description="Source talent package ID")
    cost_per_1m_tokens: float = Field(default=0.0, description="USD per 1M tokens")
    hiring_fee: float = Field(default=0.0, description="One-time hiring fee in USD")
    api_provider: str = Field(default="openrouter", description="API provider")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0, description="LLM temperature")
    hosting: str = Field(default="company", description="company | self")
    auth_method: str = Field(default="api_key", description="api_key | oauth")


class HireRequest(BaseModel):
    batch_id: str = Field(description="Batch ID from the shortlist")
    candidate_id: str = Field(description="ID of the selected candidate")
    nickname: str = Field(default="", description="Optional 花名; auto-generated if empty")


class InterviewRequest(BaseModel):
    question: str = Field(description="The interview question text")
    candidate: CandidateProfile = Field(description="Full candidate profile for context")
    images: list[str] = Field(default_factory=list, description="Optional base64-encoded images (max 3)")


class InterviewResponse(BaseModel):
    candidate_id: str = Field(description="ID of the interviewed candidate")
    question: str = Field(description="The original question")
    answer: str = Field(description="Candidate's LLM-generated answer")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/agents/test_recruitment_models.py -v`
Expected: PASS

- [ ] **Step 5: Update `routes.py` import**

In `src/onemancompany/api/routes.py` line 29, change:
```python
from onemancompany.talent_market.boss_online import HireRequest, InterviewRequest, InterviewResponse
```
to:
```python
from onemancompany.agents.recruitment import HireRequest, InterviewRequest, InterviewResponse
```

- [ ] **Step 6: Delete `boss_online.py` and its tests**

```bash
rm src/onemancompany/talent_market/boss_online.py
rm tests/unit/talent_market/test_boss_online.py
```

- [ ] **Step 7: Run full test suite to verify nothing breaks**

Run: `.venv/bin/python -m pytest tests/ -x -q`
Expected: All tests pass (minus deleted `test_boss_online.py` tests)

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "refactor: migrate Pydantic models from boss_online.py to recruitment.py, delete boss_online.py"
```

---

### Task 2: Replace `_boss_session` with `TalentMarketClient`

Replace the module-level `_boss_session`, `_boss_cleanup`, `start_boss_online()`, `stop_boss_online()`, `_call_boss_online()` with the `TalentMarketClient` class and `talent_market` singleton.

**Files:**
- Modify: `src/onemancompany/agents/recruitment.py:120-203` (replace globals + functions with class)
- Modify: `src/onemancompany/main.py:423-426,546` (rename imports)
- Modify: `src/onemancompany/api/routes.py:1978-1984,2051-2055` (update references)
- Modify: `tests/unit/agents/test_recruitment.py:158-281` (update tests)

**Context:** The current code has `_boss_session` (line 124), `start_boss_online()` (line 128), `stop_boss_online()` (line 171), `_call_boss_online()` (line 182). These all get replaced by `TalentMarketClient` with `connect()`/`disconnect()` and method wrappers for all 7 MCP tools. The `search_candidates` LangChain tool (line 205) changes its try/except from calling `_call_boss_online()` to checking `talent_market.connected` first.

- [ ] **Step 1: Write the failing tests**

Replace `TestBossOnlineLifecycle` and `TestCallBossOnline` in `tests/unit/agents/test_recruitment.py` with:

```python
class TestTalentMarketClient:
    @pytest.mark.asyncio
    async def test_connect(self, monkeypatch):
        """TalentMarketClient.connect establishes SSE session."""
        from onemancompany.agents import recruitment

        client = recruitment.TalentMarketClient()
        assert not client.connected

        mock_session = AsyncMock()
        mock_session.initialize = AsyncMock()
        mock_stack = AsyncMock()

        call_count = 0
        async def mock_enter(cm):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (AsyncMock(), AsyncMock())  # read, write
            return mock_session

        mock_stack.enter_async_context = mock_enter
        mock_stack.aclose = AsyncMock()

        with patch("onemancompany.agents.recruitment.AsyncExitStack", return_value=mock_stack):
            with patch("onemancompany.agents.recruitment.sse_client", return_value=AsyncMock()):
                with patch("onemancompany.agents.recruitment.ClientSession", return_value=AsyncMock()):
                    # Override the mock_enter to return proper session
                    call_count = 0
                    async def mock_enter2(cm):
                        nonlocal call_count
                        call_count += 1
                        if call_count == 1:
                            return (AsyncMock(), AsyncMock())
                        return mock_session
                    mock_stack.enter_async_context = mock_enter2

                    await client.connect("http://test/sse", "test-key")

        assert client.connected
        assert client._api_key == "test-key"
        mock_session.initialize.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_disconnect(self):
        """TalentMarketClient.disconnect tears down connection."""
        from onemancompany.agents import recruitment

        client = recruitment.TalentMarketClient()
        mock_stack = AsyncMock()
        mock_stack.aclose = AsyncMock()
        client._stack = mock_stack
        client._session = MagicMock()

        await client.disconnect()

        assert not client.connected
        assert client._stack is None
        mock_stack.aclose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_disconnect_no_session(self):
        """disconnect is a no-op when not connected."""
        from onemancompany.agents import recruitment

        client = recruitment.TalentMarketClient()
        await client.disconnect()  # Should not raise
        assert not client.connected

    @pytest.mark.asyncio
    async def test_call_not_connected(self):
        """_call raises RuntimeError when not connected."""
        from onemancompany.agents import recruitment

        client = recruitment.TalentMarketClient()
        with pytest.raises(RuntimeError, match="Not connected"):
            await client._call("search_candidates", job_description="test")

    @pytest.mark.asyncio
    async def test_call_parses_json(self):
        """_call parses JSON from MCP result content."""
        import json
        from onemancompany.agents import recruitment

        client = recruitment.TalentMarketClient()
        client._api_key = "test-key"

        mock_item = MagicMock()
        mock_item.text = json.dumps({"status": "ok", "data": [1, 2, 3]})
        mock_result = MagicMock()
        mock_result.content = [mock_item]

        mock_session = AsyncMock()
        mock_session.call_tool = AsyncMock(return_value=mock_result)
        client._session = mock_session

        result = await client._call("some_tool", param="value")
        assert result == {"status": "ok", "data": [1, 2, 3]}
        mock_session.call_tool.assert_awaited_once_with(
            "some_tool", arguments={"param": "value", "api_key": "test-key"}
        )

    @pytest.mark.asyncio
    async def test_search(self):
        """search() delegates to _call with correct tool name."""
        from onemancompany.agents import recruitment

        client = recruitment.TalentMarketClient()
        client._call = AsyncMock(return_value={"roles": []})

        result = await client.search("python dev")
        client._call.assert_awaited_once_with("search_candidates", job_description="python dev")
        assert result == {"roles": []}

    @pytest.mark.asyncio
    async def test_hire(self):
        """hire() passes talent_ids and session_id. api_key injected by _call."""
        from onemancompany.agents import recruitment

        client = recruitment.TalentMarketClient()
        client._api_key = "key"
        client._call = AsyncMock(return_value={"status": "ok"})

        await client.hire(["t1", "t2"], session_id="ses_123")
        client._call.assert_awaited_once_with(
            "hire_talents", talent_ids=["t1", "t2"], session_id="ses_123"
        )

    @pytest.mark.asyncio
    async def test_onboard(self):
        """onboard() returns repo URL."""
        from onemancompany.agents import recruitment

        client = recruitment.TalentMarketClient()
        client._call = AsyncMock(return_value={"repo_url": "https://git/repo"})

        result = await client.onboard("t1")
        client._call.assert_awaited_once_with("onboard_talent", talent_id="t1")
        assert result["repo_url"] == "https://git/repo"

    @pytest.mark.asyncio
    async def test_list_my_talents(self):
        """list_my_talents() calls correct tool."""
        from onemancompany.agents import recruitment

        client = recruitment.TalentMarketClient()
        client._call = AsyncMock(return_value={"talents": []})

        result = await client.list_my_talents()
        client._call.assert_awaited_once_with("list_my_talents")


class TestStartStopTalentMarket:
    @pytest.mark.asyncio
    async def test_start_talent_market_no_key(self, monkeypatch):
        """start_talent_market skips when no API key configured."""
        from onemancompany.agents import recruitment

        fake_config = {"talent_market": {"url": "http://test", "api_key": ""}}
        with patch("onemancompany.core.config.load_app_config", return_value=fake_config):
            await recruitment.start_talent_market()

        assert not recruitment.talent_market.connected

    @pytest.mark.asyncio
    async def test_stop_talent_market(self):
        """stop_talent_market delegates to client.disconnect."""
        from onemancompany.agents import recruitment

        recruitment.talent_market.disconnect = AsyncMock()
        await recruitment.stop_talent_market()
        recruitment.talent_market.disconnect.assert_awaited_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/agents/test_recruitment.py::TestTalentMarketClient -v`
Expected: FAIL with `AttributeError: module 'recruitment' has no attribute 'TalentMarketClient'`

- [ ] **Step 3: Implement `TalentMarketClient` class**

In `src/onemancompany/agents/recruitment.py`, replace lines 120-203 (everything from `# ---------------------------------------------------------------------------` / `# Persistent Boss Online MCP client` through `_call_boss_online` function) with the code below. Note: `json` is already imported at line 13; add `AsyncExitStack` and `sse_client` imports:

```python
# ---------------------------------------------------------------------------
# Persistent Talent Market MCP client (SSE)
# ---------------------------------------------------------------------------

from contextlib import AsyncExitStack
from mcp.client.sse import sse_client


class TalentMarketClient:
    """Persistent SSE MCP client for Talent Market API."""

    def __init__(self):
        self._session: ClientSession | None = None
        self._stack: AsyncExitStack | None = None
        self._api_key: str = ""

    async def connect(self, url: str, api_key: str) -> None:
        """Establish SSE connection. No-op if already connected."""
        if self._session is not None:
            return

        stack = AsyncExitStack()
        headers = {"Authorization": f"Bearer {api_key}"}
        read, write = await stack.enter_async_context(
            sse_client(url=url, headers=headers)
        )
        logger.info("Connecting to Talent Market at {}", url)

        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()

        self._session = session
        self._stack = stack
        self._api_key = api_key
        logger.info("Talent Market MCP client ready")

    async def disconnect(self) -> None:
        """Tear down SSE connection."""
        if self._session is None:
            return
        stack = self._stack
        self._session = None
        self._stack = None
        self._api_key = ""
        if stack:
            await stack.aclose()
        logger.info("Talent Market MCP client disconnected")

    @property
    def connected(self) -> bool:
        return self._session is not None

    async def _call(self, tool_name: str, **kwargs) -> dict:
        """Call an MCP tool, auto-injecting api_key."""
        if not self._session:
            raise RuntimeError("Not connected to Talent Market")
        kwargs["api_key"] = self._api_key
        result = await self._session.call_tool(tool_name, arguments=kwargs)
        # Parse JSON from first parseable content block
        for item in result.content:
            try:
                parsed = json.loads(item.text)
            except (json.JSONDecodeError, AttributeError):
                continue
            if isinstance(parsed, dict):
                return parsed
        return {}

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
        args: dict = {"talent_ids": talent_ids}
        if session_id:
            args["session_id"] = session_id
        return await self._call("hire_talents", **args)

    async def onboard(self, talent_id: str) -> dict:
        """onboard_talent — get git repo URL."""
        return await self._call("onboard_talent", talent_id=talent_id)


# Module-level singleton
talent_market = TalentMarketClient()


async def start_talent_market() -> None:
    """Start the Talent Market MCP connection. Called during app lifespan."""
    from onemancompany.core.config import load_app_config
    tm_config = load_app_config().get("talent_market", {})
    url = tm_config.get("url", "https://api.carbonkites.com/mcp/sse")
    api_key = tm_config.get("api_key", "")

    if not api_key:
        logger.warning("Talent Market API key not configured — skipping connection")
        return

    await talent_market.connect(url, api_key)


async def stop_talent_market() -> None:
    """Shut down the Talent Market MCP connection."""
    await talent_market.disconnect()
```

- [ ] **Step 4: Update `search_candidates` tool**

Replace the `search_candidates` LangChain tool in `recruitment.py` (lines 205-247) with:

```python
@tool
async def search_candidates(job_description: str) -> dict:
    """Search for candidates matching a job description.

    Uses Talent Market API when connected, falls back to local talent packages.

    Args:
        job_description: The job requirements / description text.

    Returns:
        A role-grouped dict: {type, summary, roles: [{role, description, candidates}]}.
    """
    if talent_market.connected:
        try:
            grouped = await talent_market.search(job_description)
            total = sum(len(r.get("candidates", [])) for r in grouped.get("roles", []))
            logger.info("Talent Market returned {} candidates in {} roles for JD: {}",
                        total, len(grouped.get("roles", [])), job_description[:80])
        except Exception as e:
            logger.error("Talent Market search failed: {}", e)
            grouped = _local_fallback_search(job_description)
    else:
        grouped = _local_fallback_search(job_description)

    # Stash ALL candidates from ALL roles so shortlist can look up by ID
    _last_search_results.clear()
    for role_group in grouped.get("roles", []):
        for c in role_group.get("candidates", []):
            cid = c.get("id") or c.get("talent_id", "")
            if cid:
                _last_search_results[cid] = c
    return grouped


def _local_fallback_search(job_description: str) -> dict:
    """Build role-grouped result from local talent packages."""
    from onemancompany.core.config import list_available_talents, load_talent_profile
    talents = list_available_talents()
    candidates = []
    for t in talents:
        profile = load_talent_profile(t["id"])
        if profile:
            candidates.append(_talent_to_candidate(profile))
    return {
        "type": "individual",
        "summary": "Local talent packages",
        "roles": [{"role": "Available Talents", "description": job_description, "candidates": candidates}],
    }
```

- [ ] **Step 5: Update `main.py` imports**

In `src/onemancompany/main.py`, line 423:
```python
# OLD:
from onemancompany.agents.recruitment import start_boss_online, stop_boss_online
# NEW:
from onemancompany.agents.recruitment import start_talent_market, stop_talent_market
```

Line 425: `await start_boss_online()` → `await start_talent_market()`
Line 546: `await stop_boss_online()` → `await stop_talent_market()`

- [ ] **Step 6: Update `routes.py` references**

In `src/onemancompany/api/routes.py`:

Line 1978-1984 — replace `_get_talent_market_connected()`:
```python
def _get_talent_market_connected() -> bool:
    """Check if the cloud Talent Market MCP session is active."""
    try:
        from onemancompany.agents.recruitment import talent_market
        return talent_market.connected
    except ImportError:
        return False
```

Lines 2051-2055 — replace reconnect block:
```python
        try:
            from onemancompany.agents.recruitment import stop_talent_market, start_talent_market
            await stop_talent_market()
            await start_talent_market()
        except Exception as e:
            logger.error("Failed to reconnect Talent Market: {}", e)
```

- [ ] **Step 7: Update `test_recruitment.py` search tests**

In `tests/unit/agents/test_recruitment.py`, update `TestSearchCandidates`:

```python
class TestSearchCandidates:
    @pytest.mark.asyncio
    async def test_returns_candidates_from_api(self, monkeypatch):
        from onemancompany.agents import recruitment

        fake_result = {
            "type": "individual",
            "summary": "Test",
            "roles": [
                {
                    "role": "Engineer",
                    "description": "python dev",
                    "candidates": [
                        {"id": "c1", "name": "Candidate 1", "talent_id": "c1"},
                        {"id": "c2", "name": "Candidate 2", "talent_id": "c2"},
                    ],
                }
            ],
        }
        # Mock talent_market as connected
        monkeypatch.setattr(recruitment.talent_market, "_session", MagicMock())
        recruitment.talent_market.search = AsyncMock(return_value=fake_result)

        result = await recruitment.search_candidates.ainvoke({"job_description": "python dev"})

        assert isinstance(result, dict)
        assert len(result["roles"]) == 1
        assert len(result["roles"][0]["candidates"]) == 2
        assert "c1" in recruitment._last_search_results

    @pytest.mark.asyncio
    async def test_fallback_to_local_talents(self, monkeypatch):
        from onemancompany.agents import recruitment
        from onemancompany.core import config as config_mod

        # talent_market not connected (default state)
        monkeypatch.setattr(recruitment.talent_market, "_session", None)

        monkeypatch.setattr(config_mod, "list_available_talents", lambda: [{"id": "local1"}])
        monkeypatch.setattr(
            config_mod, "load_talent_profile",
            lambda tid: {"id": "local1", "name": "Local Dev", "skills": [], "api_provider": "openrouter"},
        )
        monkeypatch.setattr(config_mod, "load_talent_skills", lambda tid: [])
        monkeypatch.setattr(config_mod, "load_talent_tools", lambda tid: [])

        result = await recruitment.search_candidates.ainvoke({"job_description": "any dev"})

        assert isinstance(result, dict)
        assert len(result["roles"]) >= 1
        assert result["roles"][0]["candidates"][0]["id"] == "local1"
```

- [ ] **Step 8: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -x -q`
Expected: All tests pass

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "refactor: replace _boss_session with TalentMarketClient singleton"
```

---

### Task 3: Add `session_id` Tracking to `submit_shortlist`

**Files:**
- Modify: `src/onemancompany/agents/recruitment.py` (stash `session_id` in search results, pass in submit_shortlist)
- Modify: `src/onemancompany/agents/hr_agent.py:162` (merge instead of overwrite `_pending_project_ctx`)
- Modify: `tests/unit/agents/test_recruitment.py` (add session_id test)

**Context:** The Talent Market API `search_candidates` response includes a `session_id` field for AI-generated talents. This must be stashed during search and stored in `_pending_project_ctx` when shortlist is submitted, so `batch_hire_candidates` can pass it to `hire_talents`.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/agents/test_recruitment.py`:

```python
class TestSessionIdTracking:
    @pytest.mark.asyncio
    async def test_session_id_stashed_from_search(self, monkeypatch):
        """search_candidates stashes session_id from API response."""
        from onemancompany.agents import recruitment

        fake_result = {
            "type": "individual",
            "summary": "Test",
            "session_id": "ses_abc123",
            "roles": [{"role": "Dev", "description": "test", "candidates": [{"id": "c1"}]}],
        }
        monkeypatch.setattr(recruitment.talent_market, "_session", MagicMock())
        recruitment.talent_market.search = AsyncMock(return_value=fake_result)

        await recruitment.search_candidates.ainvoke({"job_description": "test"})
        assert recruitment._last_session_id == "ses_abc123"

    @pytest.mark.asyncio
    async def test_session_id_cleared_on_local_fallback(self, monkeypatch):
        """Local fallback clears session_id."""
        from onemancompany.agents import recruitment
        from onemancompany.core import config as config_mod

        recruitment._last_session_id = "old_session"
        monkeypatch.setattr(recruitment.talent_market, "_session", None)
        monkeypatch.setattr(config_mod, "list_available_talents", lambda: [])

        await recruitment.search_candidates.ainvoke({"job_description": "test"})
        assert recruitment._last_session_id == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/agents/test_recruitment.py::TestSessionIdTracking -v`
Expected: FAIL with `AttributeError: module has no attribute '_last_session_id'`

- [ ] **Step 3: Implement session_id tracking**

In `recruitment.py`, add module-level variable near `_last_search_results` (line 61):

```python
_last_session_id: str = ""
```

In `search_candidates` tool, after getting `grouped` and before stashing `_last_search_results`, add:

```python
    # Stash session_id for hire_talents
    global _last_session_id
    _last_session_id = grouped.get("session_id", "")
```

In `submit_shortlist` tool, after `pending_candidates[batch_id] = all_candidates` (line 324) and before `_persist_candidates()`, add:

```python
    # Pre-populate project context with session_id from last search.
    # hr_agent.py will later merge in project_id/project_dir on top of this.
    _pending_project_ctx[batch_id] = {"session_id": _last_session_id}
```

Note: `hr_agent.py` line 162 later overwrites `_pending_project_ctx[batch_id]` with `{project_id, project_dir}`. We need to **merge** instead of replace. In `hr_agent.py` line 162, change:

```python
# OLD (line 162):
_pending_project_ctx[bid] = {
    "project_id": task_obj.project_id,
    "project_dir": task_obj.project_dir,
}
# NEW:
_pending_project_ctx[bid] = {
    **_pending_project_ctx.get(bid, {}),  # preserve session_id
    "project_id": task_obj.project_id,
    "project_dir": task_obj.project_dir,
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/agents/test_recruitment.py::TestSessionIdTracking -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: track session_id from Talent Market search for hire_talents"
```

---

### Task 4: Add `clone_talent_repo()` to `onboarding.py`

**Files:**
- Modify: `src/onemancompany/agents/onboarding.py:521-538` (add `clone_talent_repo` function)
- Create: `tests/unit/agents/test_onboarding_clone.py`

**Context:** `_LEGACY_TALENTS_DIR` at line 524 points to `talent_market/talents/`. The new `clone_talent_repo()` clones a git repo URL into that directory. `resolve_talent_dir()` at line 527 already looks there, so after cloning, `execute_hire` will find it automatically.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/agents/test_onboarding_clone.py`:

```python
"""Tests for clone_talent_repo in onboarding.py."""
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from onemancompany.agents.onboarding import clone_talent_repo


class TestCloneTalentRepo:
    @pytest.mark.asyncio
    async def test_clone_new_repo(self, tmp_path, monkeypatch):
        """Clones repo when directory doesn't exist."""
        import onemancompany.agents.onboarding as onboarding_mod
        monkeypatch.setattr(onboarding_mod, "_LEGACY_TALENTS_DIR", tmp_path)

        with patch("onemancompany.agents.onboarding.subprocess") as mock_sub:
            mock_sub.run = MagicMock()
            result = await clone_talent_repo("https://git.example.com/repo.git", "test-talent")

        assert result == tmp_path / "test-talent"
        mock_sub.run.assert_called_once()
        args = mock_sub.run.call_args
        assert args[0][0][0] == "git"
        assert args[0][0][1] == "clone"

    @pytest.mark.asyncio
    async def test_pull_existing_repo(self, tmp_path, monkeypatch):
        """Does git pull when directory already exists."""
        import onemancompany.agents.onboarding as onboarding_mod
        monkeypatch.setattr(onboarding_mod, "_LEGACY_TALENTS_DIR", tmp_path)

        # Create existing dir
        (tmp_path / "existing-talent").mkdir()

        with patch("onemancompany.agents.onboarding.subprocess") as mock_sub:
            mock_sub.run = MagicMock()
            result = await clone_talent_repo("https://git.example.com/repo.git", "existing-talent")

        assert result == tmp_path / "existing-talent"
        mock_sub.run.assert_called_once()
        args = mock_sub.run.call_args
        assert args[0][0][1] == "-C"  # git -C <path> pull
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/agents/test_onboarding_clone.py -v`
Expected: FAIL with `ImportError: cannot import name 'clone_talent_repo'`

- [ ] **Step 3: Implement `clone_talent_repo`**

In `src/onemancompany/agents/onboarding.py`, add `import subprocess` to the imports section at the top (it's NOT currently imported). Then add after `resolve_talent_dir()` (around line 538):

```python
async def clone_talent_repo(repo_url: str, talent_id: str) -> Path:
    """Clone a talent repo into talent_market/talents/{talent_id}/.

    If the directory already exists, does a git pull instead.
    Returns the local talent directory path.
    """
    target = _LEGACY_TALENTS_DIR / talent_id
    if target.exists():
        subprocess.run(["git", "-C", str(target), "pull"], check=True)
    else:
        subprocess.run(["git", "clone", repo_url, str(target)], check=True)
    return target
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/agents/test_onboarding_clone.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: add clone_talent_repo for downloading talent packages from API"
```

---

### Task 5: Update `batch_hire_candidates` with Purchase + Clone Flow

**Files:**
- Modify: `src/onemancompany/api/routes.py:3798-3955` (add purchase + clone before hire loop)

**Context:** Currently `batch_hire_candidates` (line 3798) looks up candidates from `pending_candidates[batch_id]`, then directly calls `execute_hire`. We need to add: (1) if `talent_market.connected`, call `talent_market.hire()` to purchase, then `talent_market.onboard()` to get repo URL, then `clone_talent_repo()` to clone locally; (2) pass `session_id` from `_pending_project_ctx`. The existing loop logic for `execute_hire` stays the same.

- [ ] **Step 1: Write the failing test**

Add to the appropriate test file (or create `tests/unit/api/test_batch_hire_purchase.py`):

```python
"""Tests for batch hire with Talent Market purchase+clone flow."""
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

import pytest


class TestBatchHirePurchase:
    @pytest.mark.asyncio
    async def test_batch_hire_calls_purchase_when_connected(self):
        """When talent_market is connected, hire_talents is called before execute_hire."""
        from onemancompany.agents import recruitment

        # Set up connected state
        recruitment.talent_market._session = MagicMock()
        recruitment.talent_market.hire = AsyncMock(return_value={"status": "ok", "hired": ["t1"]})
        recruitment.talent_market.onboard = AsyncMock(return_value={"repo_url": "https://git/t1.git"})

        recruitment.pending_candidates["batch_purchase"] = [
            {"id": "t1", "talent_id": "t1", "name": "Test Dev", "role": "Engineer",
             "skill_set": [], "sprite": "employee_blue"}
        ]
        recruitment._pending_project_ctx["batch_purchase"] = {"session_id": "ses_test"}

        # Mock at importing module level (routes.py imports these)
        with patch("onemancompany.api.routes.clone_talent_repo", new_callable=AsyncMock, return_value=Path("/tmp/t1"), create=True) as mock_clone, \
             patch("onemancompany.api.routes.execute_hire", new_callable=AsyncMock, create=True) as mock_hire, \
             patch("onemancompany.api.routes.generate_nickname", new_callable=AsyncMock, return_value="测试", create=True), \
             patch("onemancompany.api.routes.load_talent_profile", return_value={}, create=True), \
             patch("onemancompany.api.routes.event_bus", MagicMock(publish=AsyncMock())), \
             patch("onemancompany.api.routes._pending_coo_hire_queue", []):

            mock_hire.return_value = MagicMock(id="00099")

            from onemancompany.api.routes import batch_hire_candidates
            result = await batch_hire_candidates({
                "batch_id": "batch_purchase",
                "selections": [{"candidate_id": "t1", "role": "Engineer"}],
            })

            recruitment.talent_market.hire.assert_awaited_once()
            recruitment.talent_market.onboard.assert_awaited_once_with("t1")
            mock_clone.assert_awaited_once()

        # Cleanup
        recruitment.talent_market._session = None
        recruitment.pending_candidates.pop("batch_purchase", None)
        recruitment._pending_project_ctx.pop("batch_purchase", None)
```

Note: `batch_hire_candidates` uses lazy `from ... import` inside the function body, so `create=True` is needed on patches since those names don't exist at module level. Alternatively, if imports are already at module level, drop `create=True`. Verify which pattern `routes.py` uses during implementation.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/api/test_batch_hire_purchase.py -v`
Expected: FAIL (purchase flow not implemented yet)

- [ ] **Step 3: Implement purchase + clone in `batch_hire_candidates`**

In `src/onemancompany/api/routes.py`, modify `batch_hire_candidates` (line 3798). Add the purchase+clone block after looking up `all_candidates` (line 3817) and before the per-candidate loop (line 3824):

```python
    # --- Talent Market purchase + clone (when connected) ---
    from onemancompany.agents.recruitment import talent_market, _pending_project_ctx

    session_id = _pending_project_ctx.get(batch_id, {}).get("session_id", "")
    talent_ids = [s.get("candidate_id", "") for s in selections]

    if talent_market.connected and talent_ids:
        try:
            purchase_result = await talent_market.hire(talent_ids, session_id=session_id)
            if purchase_result.get("error"):
                return {
                    "error": purchase_result.get("error", "Purchase failed"),
                    "balance": purchase_result.get("balance"),
                    "required": purchase_result.get("required"),
                    "shortfall": purchase_result.get("shortfall"),
                }
        except Exception as e:
            logger.error("Talent Market purchase failed: {}", e)
            return {"error": f"Purchase failed: {e}"}

        # Onboard + clone each purchased talent
        from onemancompany.agents.onboarding import clone_talent_repo
        for tid in talent_ids:
            try:
                onboard_result = await talent_market.onboard(tid)
                repo_url = onboard_result.get("repo_url", "")
                if repo_url:
                    await clone_talent_repo(repo_url, tid)
            except Exception as e:
                logger.error("Failed to onboard/clone talent {}: {}", tid, e)
                # Continue — talent may still have local package
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/api/test_batch_hire_purchase.py -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -x -q`
Expected: All tests pass

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: add Talent Market purchase + clone flow to batch hire"
```

---

### Task 6: Add `GET /api/talent-pool` Endpoint

**Files:**
- Modify: `src/onemancompany/api/routes.py` (add endpoint)
- Create: `tests/unit/api/test_talent_pool.py`

**Context:** New endpoint returns talent pool — from API when connected, local fallback otherwise. Uses `talent_market.list_my_talents()` (API) or `config.py` `list_available_talents()` + `load_talent_profile()` (local). Response format: `{source: "api"|"local", talents: [...]}`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/api/test_talent_pool.py`:

```python
"""Tests for GET /api/talent-pool endpoint."""
from unittest.mock import AsyncMock, MagicMock, patch
import pytest


class TestTalentPoolLocal:
    @pytest.mark.asyncio
    async def test_local_fallback(self, monkeypatch):
        """Returns local talents when talent_market not connected."""
        from onemancompany.agents import recruitment
        from onemancompany.core import config as config_mod

        monkeypatch.setattr(recruitment.talent_market, "_session", None)
        monkeypatch.setattr(config_mod, "list_available_talents", lambda: [
            {"id": "local1", "name": "Local Dev"},
        ])
        monkeypatch.setattr(config_mod, "load_talent_profile", lambda tid: {
            "id": "local1", "name": "Local Dev", "role": "Engineer", "skills": ["python"],
        })

        from onemancompany.api.routes import get_talent_pool
        result = await get_talent_pool()

        assert result["source"] == "local"
        assert len(result["talents"]) == 1
        assert result["talents"][0]["talent_id"] == "local1"


class TestTalentPoolAPI:
    @pytest.mark.asyncio
    async def test_api_source(self, monkeypatch):
        """Returns API talents when talent_market is connected."""
        from onemancompany.agents import recruitment

        monkeypatch.setattr(recruitment.talent_market, "_session", MagicMock())
        recruitment.talent_market.list_my_talents = AsyncMock(return_value={
            "talents": [
                {"talent_id": "api1", "name": "API Dev", "role": "Engineer",
                 "skills": ["react"], "purchased_at": "2026-03-10T12:00:00Z"},
            ]
        })

        from onemancompany.api.routes import get_talent_pool
        result = await get_talent_pool()

        assert result["source"] == "api"
        assert len(result["talents"]) == 1
        assert result["talents"][0]["talent_id"] == "api1"
        assert result["talents"][0]["status"] == "purchased"

        # Cleanup
        recruitment.talent_market._session = None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/api/test_talent_pool.py -v`
Expected: FAIL with `ImportError: cannot import name 'get_talent_pool'`

- [ ] **Step 3: Implement the endpoint**

Add to `src/onemancompany/api/routes.py` (near the settings section, around line 1995):

```python
@router.get("/api/talent-pool")
async def get_talent_pool() -> dict:
    """Return the talent pool — purchased talents (API) or local packages."""
    from onemancompany.agents.recruitment import talent_market

    if talent_market.connected:
        try:
            data = await talent_market.list_my_talents()
            talents = []
            for t in data.get("talents", []):
                talents.append({
                    "talent_id": t.get("talent_id", t.get("id", "")),
                    "name": t.get("name", ""),
                    "role": t.get("role", ""),
                    "skills": t.get("skills", []),
                    "status": "purchased",
                    "purchased_at": t.get("purchased_at", ""),
                })
            return {"source": "api", "talents": talents}
        except Exception as e:
            logger.error("Failed to fetch talent pool from API: {}", e)
            # Fall through to local

    from onemancompany.core.config import list_available_talents, load_talent_profile
    talents = []
    for t in list_available_talents():
        profile = load_talent_profile(t["id"])
        if profile:
            talents.append({
                "talent_id": profile.get("id", t["id"]),
                "name": profile.get("name", t["id"]),
                "role": profile.get("role", ""),
                "skills": profile.get("skills", []),
                "status": "local",
            })
    return {"source": "local", "talents": talents}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/api/test_talent_pool.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: add GET /api/talent-pool endpoint"
```

---

## Chunk 2: Frontend — Talent Pool Modal

### Task 7: Add Talent Pool Modal HTML + CSS + JS

**Files:**
- Modify: `frontend/index.html` (add modal skeleton)
- Modify: `frontend/style.css` (add modal styles)
- Modify: `frontend/app.js` (add button, open/close, fetch+render)

**Context:** Follow the existing modal pattern — see `candidate-modal` in `index.html` (line 394), its CSS in `style.css`, and `closeCandidateModal()` in `app.js` (line 2847). The talent pool modal is opened from a button on the HR employee detail page. It shows a list of talent cards with name, role, skill badges, and status indicator.

- [ ] **Step 1: Add modal HTML skeleton to `index.html`**

Add after the candidate modal (after its closing `</div>`), before the next modal:

```html
  <!-- Talent Pool Modal (人才库) -->
  <div id="talent-pool-modal" class="modal-overlay hidden">
    <div class="modal-content talent-pool-modal-content">
      <div class="modal-header">
        <h3 class="pixel-title">&#128188; 人才库</h3>
        <span id="talent-pool-source-badge" class="talent-pool-badge"></span>
        <button id="talent-pool-close-btn" class="modal-close">&#10005;</button>
      </div>
      <div class="modal-body talent-pool-modal-body">
        <div id="talent-pool-list" class="talent-pool-list"></div>
      </div>
    </div>
  </div>
```

- [ ] **Step 2: Add modal styles to `style.css`**

Add talent pool modal styles (follow existing candidate-modal patterns):

```css
/* ===== Talent Pool Modal ===== */
.talent-pool-modal-content {
  max-width: 600px;
  max-height: 70vh;
}

.talent-pool-modal-body {
  overflow-y: auto;
  max-height: 55vh;
  padding: 12px;
}

.talent-pool-badge {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 3px;
  margin-left: 8px;
  font-family: 'Press Start 2P', monospace;
  font-size: 8px;
}

.talent-pool-badge.api {
  background: #22c55e;
  color: #fff;
}

.talent-pool-badge.local {
  background: #6b7280;
  color: #fff;
}

.talent-pool-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.talent-pool-card {
  border: 2px solid var(--border);
  padding: 10px;
  background: var(--bg-card);
  image-rendering: pixelated;
}

.talent-pool-card .talent-name {
  font-family: 'Press Start 2P', monospace;
  font-size: 10px;
  color: var(--text-primary);
}

.talent-pool-card .talent-role {
  font-size: 11px;
  color: var(--text-secondary);
  margin-top: 4px;
}

.talent-pool-card .talent-skills {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 6px;
}

.talent-pool-card .skill-tag {
  font-size: 9px;
  padding: 2px 6px;
  background: var(--bg-hover);
  border: 1px solid var(--border);
  font-family: 'Press Start 2P', monospace;
}

.talent-pool-card .talent-status {
  font-size: 9px;
  margin-top: 6px;
  color: var(--text-muted);
}
```

- [ ] **Step 3: Add JS — button, fetch, render, open/close**

In `frontend/app.js`, add these methods to the app class:

**1. Add "人才库" button rendering** — find where HR employee detail buttons are rendered and add:

```javascript
// In the employee detail rendering section for HR:
if (employee.id === '00001') {  // HR_ID
    const talentPoolBtn = document.createElement('button');
    talentPoolBtn.className = 'action-btn';
    talentPoolBtn.textContent = '人才库';
    talentPoolBtn.addEventListener('click', () => this.openTalentPool());
    actionButtons.appendChild(talentPoolBtn);
}
```

**2. Add event listener** for close button (in the existing modal event setup section, near line 957):

```javascript
document.getElementById('talent-pool-close-btn').addEventListener('click', () => this.closeTalentPool());
```

**3. Add backdrop click handler** (in existing click handler block, near line 959):

```javascript
if (e.target.id === 'talent-pool-modal') this.closeTalentPool();
```

**4. Add methods:**

```javascript
  async openTalentPool() {
    try {
      const resp = await fetch('/api/talent-pool');
      const data = await resp.json();
      this._renderTalentPool(data);
      document.getElementById('talent-pool-modal').classList.remove('hidden');
    } catch (e) {
      console.error('Failed to load talent pool:', e);
    }
  }

  closeTalentPool() {
    document.getElementById('talent-pool-modal').classList.add('hidden');
  }

  _renderTalentPool(data) {
    const badge = document.getElementById('talent-pool-source-badge');
    badge.textContent = data.source === 'api' ? 'API' : '本地';
    badge.className = 'talent-pool-badge ' + (data.source === 'api' ? 'api' : 'local');

    const list = document.getElementById('talent-pool-list');
    list.innerHTML = '';

    if (!data.talents || data.talents.length === 0) {
      list.innerHTML = '<div style="text-align:center;color:var(--text-muted);padding:20px;">暂无人才</div>';
      return;
    }

    for (const t of data.talents) {
      const card = document.createElement('div');
      card.className = 'talent-pool-card';
      card.innerHTML = `
        <div class="talent-name">${t.name || t.talent_id}</div>
        <div class="talent-role">${t.role || ''}</div>
        <div class="talent-skills">
          ${(t.skills || []).map(s => `<span class="skill-tag">${s}</span>`).join('')}
        </div>
        <div class="talent-status">${t.status === 'purchased' ? '✓ 已购买' : '本地'}</div>
      `;
      list.appendChild(card);
    }
  }
```

- [ ] **Step 4: Test manually**

Start the dev server and verify:
1. Open HR employee detail modal
2. "人才库" button appears
3. Clicking it opens the talent pool modal
4. Shows local talents with "本地" badge (or API talents with "API" badge if connected)
5. Close button and backdrop click work

Run: `python scripts/with_server.py --server ".venv/bin/python -m onemancompany" --port 8000 -- python tests/e2e/test_talent_pool_modal.py` (or manual verification)

- [ ] **Step 5: Commit**

```bash
git add frontend/index.html frontend/style.css frontend/app.js
git commit -m "feat: add talent pool modal (人才库) on HR detail page"
```

---

### Task 8: Final Integration Test + Cleanup

**Files:**
- All modified files (verify no dead code, no stale imports)

- [ ] **Step 1: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -x -q`
Expected: All tests pass

- [ ] **Step 2: Verify no stale references to `boss_online`**

Run: `grep -r "boss_online\|_boss_session\|_boss_cleanup\|start_boss_online\|stop_boss_online\|_call_boss_online" src/ --include="*.py"`
Expected: No matches (all references removed)

- [ ] **Step 3: Verify no stale references to deleted functions**

Run: `grep -r "boss_online" tests/ --include="*.py"`
Expected: No matches

- [ ] **Step 4: Verify compilation**

Run: `.venv/bin/python -c "from onemancompany.agents.recruitment import talent_market, start_talent_market, stop_talent_market, TalentMarketClient; print('OK')"`
Expected: `OK`

- [ ] **Step 5: Commit if any cleanup was needed**

```bash
git add -A
git commit -m "chore: final cleanup — remove stale boss_online references"
```
