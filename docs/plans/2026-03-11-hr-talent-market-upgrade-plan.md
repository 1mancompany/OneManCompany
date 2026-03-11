# HR-Talent Market Recruitment Upgrade — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Upgrade recruitment pipeline to support team-level hiring with AI search, role-grouped multi-select candidates, batch onboarding with real-time progress.

**Architecture:** Replace MCP `search_candidates` with LLM-powered AI search that returns role-grouped results. Upgrade `submit_shortlist` to pass role structure. Redesign frontend candidate panel with role tabs, multi-select, and onboarding progress modal. Add `POST /api/candidates/batch-hire` endpoint with WebSocket progress events.

**Tech Stack:** Python/FastAPI, LangChain tools, MCP (FastMCP), Anthropic/OpenRouter LLM APIs, vanilla JS + CSS

---

### Task 1: Upgrade MCP `search_candidates` to use AI Search

**Files:**
- Modify: `/Users/yuzhengxu/projects/talentmarket/src/talentmarket/mcp_server.py`
- Reference: `/Users/yuzhengxu/projects/talentmarket/src/talentmarket/api/ai_search.py`
- Reference: `/Users/yuzhengxu/projects/talentmarket/src/talentmarket/config.py`

**Context:** The MCP server's `search_candidates` currently uses Jaccard keyword matching via `registry.search_talents()`. We need to replace it with the LLM-powered AI search logic from `ai_search.py`. The `ai_search.py` already has `_call_claude()`, `_call_openrouter()`, `_build_talent_summary()`, and `_parse_llm_json()` functions that we can reuse. The config (`settings`) has `anthropic_api_key` and `openrouter_api_key` fields.

**Step 1: Rewrite `search_candidates` in `mcp_server.py`**

Replace the existing `search_candidates` tool with an async version that:
1. Loads all talents via `list_talents()`
2. Builds talent summary text via `_build_talent_summary()` from `ai_search.py`
3. Calls LLM (prefer openrouter if key exists, else anthropic) using the functions from `ai_search.py`
4. Returns the role-grouped result dict

The new return format:
```python
{
    "type": "individual" | "team",
    "summary": "AI analysis...",
    "roles": [
        {
            "role": "Game Engineer",
            "description": "...",
            "candidates": [
                {
                    "talent_id": "xxx",
                    "name": "...",
                    "role": "Engineer",
                    "description": "...",
                    "skills": [...],
                    "hosting": "company",
                    "personality_tags": [...],
                    "hiring_fee": 0.2,
                    "salary_per_1m_tokens": 0.0,
                    "score": 0.92,
                    "reasoning": "Strong match..."
                }
            ]
        }
    ]
}
```

The implementation needs to:
- Import `_build_talent_summary`, `_call_claude`, `_call_openrouter`, `_parse_llm_json` from `talentmarket.api.ai_search`
- Import `settings` from `talentmarket.config`
- Keep the tool synchronous by running async LLM calls with `asyncio.run()` (MCP tools are sync)
- Fall back to keyword search if no API key is configured or LLM call fails
- For each matched talent_id in the LLM result, hydrate with full profile fields from the registry
- Handle the `_find_general_assistant()` fallback for missing roles (from ai_search.py)

```python
import asyncio
import json
from mcp.server.fastmcp import FastMCP
from talentmarket.api.ai_search import (
    _build_talent_summary, _call_claude, _call_openrouter, _find_general_assistant
)
from talentmarket.config import settings
from talentmarket.registry import (
    ensure_local_talents_registered, get_talent,
    list_talents as _list_talents, search_talents as _search_talents,
)

mcp = FastMCP("TalentMarket")


def _hydrate_candidate(talent_entry, score: float = 0.5, reasoning: str = "") -> dict:
    """Build a full candidate dict from a TalentEntry + AI match metadata."""
    p = talent_entry.profile
    return {
        "talent_id": p.id,
        "name": p.name,
        "role": p.role,
        "description": p.description,
        "skills": p.skills,
        "hosting": p.hosting,
        "personality_tags": p.personality_tags,
        "hiring_fee": p.hiring_fee,
        "salary_per_1m_tokens": p.salary_per_1m_tokens,
        "score": score,
        "reasoning": reasoning,
        "relevance": score,  # backward compat
    }


@mcp.tool()
def search_candidates(job_description: str, count: int = 10) -> dict:
    """Search the Talent Market for candidates matching a job description.

    Uses AI (LLM) to semantically analyze the JD and match against available
    talents. Automatically detects individual vs team hiring needs and returns
    candidates grouped by role.

    Args:
        job_description: The job description / requirements to search for.
        count: Maximum candidates per role (default 10).

    Returns:
        A dict with type ("individual"/"team"), summary, and roles list.
        Each role has a list of candidates sorted by match score.
    """
    ensure_local_talents_registered()
    all_talents = _list_talents()
    talent_dicts = [t.model_dump() for t in all_talents]
    talent_text = _build_talent_summary(talent_dicts)

    # Determine API key
    api_key = settings.openrouter_api_key or settings.anthropic_api_key
    provider = "openrouter" if settings.openrouter_api_key else "claude"

    if not api_key:
        # Fallback: keyword search, return as single-role "individual"
        results = _search_talents(query=job_description, count=count)
        candidates = [_hydrate_candidate(r.talent, score=r.relevance) for r in results]
        return {
            "type": "individual",
            "summary": "Keyword-matched results (no AI API key configured)",
            "roles": [{"role": "Match", "description": job_description[:100], "candidates": candidates}],
        }

    try:
        if provider == "openrouter":
            llm_result = asyncio.run(_call_openrouter(api_key, job_description, talent_text))
        else:
            llm_result = asyncio.run(_call_claude(api_key, job_description, talent_text))
    except Exception as e:
        # Fallback to keyword search on LLM failure
        results = _search_talents(query=job_description, count=count)
        candidates = [_hydrate_candidate(r.talent, score=r.relevance) for r in results]
        return {
            "type": "individual",
            "summary": f"Keyword fallback (AI search failed: {e})",
            "roles": [{"role": "Match", "description": job_description[:100], "candidates": candidates}],
        }

    # Hydrate LLM results with full talent data
    fallback = _find_general_assistant()
    roles = []
    for role_data in llm_result.get("roles", []):
        talent_ids = role_data.get("talent_ids", [])
        scores = role_data.get("scores", [])
        reasonings = role_data.get("reasonings", [])
        candidates = []
        for i, tid in enumerate(talent_ids):
            talent = get_talent(tid)
            if talent:
                candidates.append(_hydrate_candidate(
                    talent,
                    score=scores[i] if i < len(scores) else 0.5,
                    reasoning=reasonings[i] if i < len(reasonings) else "",
                ))
        if not candidates and fallback:
            candidates.append(_hydrate_candidate(fallback, score=0.3, reasoning="General assistant fallback"))
        roles.append({
            "role": role_data.get("role", "Unknown"),
            "description": role_data.get("description", ""),
            "candidates": candidates[:count],
        })

    return {
        "type": llm_result.get("type", "individual"),
        "summary": llm_result.get("summary", ""),
        "roles": roles,
    }
```

Also keep `get_talent_info` and `list_available_talents` tools unchanged.

**Step 2: Verify MCP server starts**

Run: `cd /Users/yuzhengxu/projects/talentmarket && uv run python -c "from talentmarket.mcp_server import mcp; print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
cd /Users/yuzhengxu/projects/talentmarket
git add src/talentmarket/mcp_server.py
git commit -m "feat: replace keyword search with AI-powered search in MCP server"
```

---

### Task 2: Upgrade `recruitment.py` — role-grouped shortlist

**Files:**
- Modify: `/Users/yuzhengxu/projects/OneManCompany/src/onemancompany/agents/recruitment.py`

**Context:** Currently `_call_boss_online` returns a flat list of candidates. With the new MCP return format, it returns `{"type": ..., "roles": [...]}`. We need to:
1. Update `_call_boss_online` to return the new dict structure
2. Update `search_candidates` tool to stash results and return them
3. Update `submit_shortlist` to accept role-grouped structure and pass it to `candidates_ready` event

**Step 1: Update `_call_boss_online` to parse new format**

The MCP `search_candidates` now returns a single dict (not a list). Update the parser:

```python
async def _call_boss_online(job_description: str, count: int = 10) -> dict:
    """Call the persistent Boss Online MCP session. Returns role-grouped result."""
    if _boss_session is None:
        raise RuntimeError("Boss Online MCP server is not running")

    result = await _boss_session.call_tool(
        "search_candidates",
        arguments={"job_description": job_description, "count": count},
    )
    # MCP returns content blocks — parse the first one as the full result dict
    for item in result.content:
        try:
            parsed = json.loads(item.text)
            if isinstance(parsed, dict) and "roles" in parsed:
                return parsed
        except (json.JSONDecodeError, AttributeError) as _e:
            logger.debug("Skipping unparseable content block: {}", _e)
            continue
    return {"type": "individual", "summary": "", "roles": []}
```

**Step 2: Update `search_candidates` tool**

Change from returning flat list to role-grouped dict. Stash candidates for shortlist lookup:

```python
@tool
async def search_candidates(job_description: str) -> dict:
    """Search the Boss Online recruitment platform for candidates matching a job description.

    Returns role-grouped candidates with AI match scores.

    Args:
        job_description: The job requirements / description text.

    Returns:
        A dict with type (individual/team), summary, and roles list.
    """
    try:
        result = await _call_boss_online(job_description)
        total = sum(len(r.get("candidates", [])) for r in result.get("roles", []))
        logger.info("Boss Online returned %d candidates in %d roles for JD: %s",
                     total, len(result.get("roles", [])), job_description[:80])
    except Exception as e:
        logger.error("Boss Online MCP call failed: %s", e)
        # Fallback to local talent packages
        from onemancompany.core.config import list_available_talents, load_talent_profile
        talents = list_available_talents()
        candidates = []
        for t in talents:
            profile = load_talent_profile(t["id"])
            if profile:
                candidates.append(_talent_to_candidate(profile))
        result = {
            "type": "individual",
            "summary": f"Local fallback ({e})",
            "roles": [{"role": "Match", "description": "", "candidates": candidates}],
        }

    # Stash all candidates for shortlist lookup
    _last_search_results.clear()
    for role in result.get("roles", []):
        for c in role.get("candidates", []):
            cid = c.get("talent_id") or c.get("id", "")
            if cid:
                _last_search_results[cid] = c
    return result
```

**Step 3: Update `submit_shortlist` tool**

Accept role-grouped IDs and pass the structure through:

```python
@tool
async def submit_shortlist(jd: str, candidate_ids: list[str], roles: list[dict] | None = None) -> str:
    """Submit a shortlist of candidates to CEO for selection and interview.

    After calling search_candidates(), submit candidate IDs here.
    If roles is provided (from AI search), they will be shown grouped by role.
    Otherwise, candidates are shown as a flat list.

    Args:
        jd: The job description used for the search.
        candidate_ids: List of all candidate IDs to include in the shortlist.
        roles: Optional role grouping from search results. Each dict has
            "role", "description", and "candidates" (list of candidate dicts).

    Returns:
        Confirmation message with batch_id.
    """
    import uuid as _uuid
    from onemancompany.core.events import CompanyEvent, event_bus

    # Hydrate candidates from stashed search results
    all_candidates = []
    for cid in candidate_ids:
        full = _last_search_results.get(cid)
        if full:
            all_candidates.append(full)
        else:
            logger.warning("submit_shortlist: candidate %s not found in search results", cid)

    if not all_candidates:
        return "ERROR: No valid candidates found. Call search_candidates() first."

    batch_id = str(_uuid.uuid4())[:8]
    pending_candidates[batch_id] = all_candidates

    # Build role-grouped payload if roles provided
    if roles:
        # Re-hydrate role groups with full candidate data
        hydrated_roles = []
        for role_info in roles:
            role_candidates = []
            for c in role_info.get("candidates", []):
                cid = c.get("talent_id") or c.get("id", "")
                full = _last_search_results.get(cid)
                if full:
                    role_candidates.append(full)
            if role_candidates:
                hydrated_roles.append({
                    "role": role_info.get("role", "Unknown"),
                    "description": role_info.get("description", ""),
                    "candidates": role_candidates,
                })
        payload_roles = hydrated_roles
    else:
        # Flat list fallback — wrap in single role group
        payload_roles = [{"role": "Candidates", "description": "", "candidates": all_candidates}]

    await event_bus.publish(CompanyEvent(
        type="candidates_ready",
        payload={
            "batch_id": batch_id,
            "jd": jd,
            "roles": payload_roles,
            # Keep flat list for backward compat
            "candidates": all_candidates,
        },
        agent="HR",
    ))
    logger.info("Shortlist submitted: batch=%s, %d candidates in %d roles",
                batch_id, len(all_candidates), len(payload_roles))
    return (
        f"Shortlist submitted (batch_id={batch_id}). "
        f"{len(all_candidates)} candidates in {len(payload_roles)} role(s) sent to CEO. "
        "Wait for CEO to choose — do NOT hire directly."
    )
```

**Step 4: Verify compilation**

Run: `.venv/bin/python -c "from onemancompany.agents.recruitment import search_candidates, submit_shortlist; print('OK')"`
Expected: `OK`

**Step 5: Commit**

```bash
git add src/onemancompany/agents/recruitment.py
git commit -m "feat: upgrade recruitment to role-grouped AI search results"
```

---

### Task 3: Add batch-hire API endpoint with progress events

**Files:**
- Modify: `/Users/yuzhengxu/projects/OneManCompany/src/onemancompany/api/routes.py`
- Modify: `/Users/yuzhengxu/projects/OneManCompany/src/onemancompany/agents/onboarding.py`

**Context:** Need `POST /api/candidates/batch-hire` that accepts multiple selections, runs `execute_hire()` sequentially, and pushes `onboarding_progress` WebSocket events at each step. The existing `hire_candidate` endpoint (single hire) should remain functional.

**Step 1: Add progress publishing helper to `onboarding.py`**

Add a helper function and modify `execute_hire()` to accept an optional progress callback:

At the top of `execute_hire()`, after imports, add a progress callback parameter:

```python
async def execute_hire(
    name: str,
    nickname: str,
    role: str,
    skills: list[str],
    *,
    talent_id: str = "",
    talent_dir: Path | None = None,
    llm_model: str = "",
    temperature: float = 0.7,
    image_model: str = "",
    api_provider: str = "openrouter",
    hosting: str = "company",
    auth_method: str = "api_key",
    sprite: str = "employee_default",
    remote: bool = False,
    department: str = "",
    progress_callback = None,  # async callable(step, message)
) -> Employee:
```

Insert progress callback calls at key points in execute_hire():

After `emp_num = company_state.next_employee_number()` (line ~722):
```python
    if progress_callback:
        await progress_callback("assigning_id", f"Assigned #{emp_num}")
```

After `save_employee_profile(emp_num, config)` and before talent asset copy (line ~782):
```python
    if progress_callback:
        await progress_callback("copying_skills", "Copying skill packages...")
```

After all file copying is done and before agent registration (line ~883):
```python
    if progress_callback:
        await progress_callback("registering_agent", "Registering agent...")
```

After `event_bus.publish(CompanyEvent(type="employee_hired"...))` (line ~881):
```python
    if progress_callback:
        await progress_callback("completed", f"{name} ({nickname}) onboarded as #{emp_num}")
```

**Step 2: Add `POST /api/candidates/batch-hire` endpoint to `routes.py`**

Add after the existing `hire_candidate` endpoint:

```python
@router.post("/api/candidates/batch-hire")
async def batch_hire_candidates(body: dict) -> dict:
    """Batch hire multiple candidates from a role-grouped shortlist.

    Request body:
        batch_id: str
        selections: list of {candidate_id: str, role: str}
    """
    import traceback
    from onemancompany.agents.hr_agent import pending_candidates, _pending_project_ctx
    from onemancompany.agents.onboarding import execute_hire, generate_nickname
    from onemancompany.core.config import load_talent_profile

    batch_id = body.get("batch_id", "")
    selections = body.get("selections", [])

    if not selections:
        return {"error": "No candidates selected"}

    all_candidates = pending_candidates.get(batch_id, [])
    if not all_candidates:
        return {"error": "Batch not found"}

    total = len(selections)
    results = []

    for idx, sel in enumerate(selections):
        candidate_id = sel.get("candidate_id", "")
        hire_role = sel.get("role", "")

        candidate = next((c for c in all_candidates if (c.get("id") or c.get("talent_id")) == candidate_id), None)
        if not candidate:
            await event_bus.publish(CompanyEvent(
                type="onboarding_progress",
                payload={"batch_id": batch_id, "candidate_id": candidate_id,
                         "name": candidate_id, "step": "failed",
                         "step_index": 0, "total_steps": 4, "current": idx + 1, "total": total,
                         "message": "Candidate not found"},
                agent="HR",
            ))
            results.append({"candidate_id": candidate_id, "status": "error", "error": "Not found"})
            continue

        cand_name = candidate.get("name", candidate_id)
        talent_id = candidate.get("talent_id", "") or candidate.get("id", "")

        # Read authoritative fields from talent profile
        talent_data = {}
        if talent_id:
            talent_data = load_talent_profile(talent_id)

        skill_names = [s["name"] if isinstance(s, dict) else s for s in candidate.get("skill_set", candidate.get("skills", []))]

        # Apply COO context if available
        coo_ctx = {}
        if _pending_coo_hire_queue:
            coo_ctx = _pending_coo_hire_queue.pop(0)

        final_role = coo_ctx.get("role") or hire_role or candidate.get("role", "Engineer")
        final_dept = coo_ctx.get("department", "")

        # Register custom roles
        if coo_ctx.get("role"):
            from onemancompany.core.config import ROLE_DEPARTMENT_MAP
            from onemancompany.core.state import ROLE_TITLES
            if coo_ctx["role"] not in ROLE_TITLES:
                ROLE_TITLES[coo_ctx["role"]] = coo_ctx["role"]
            if coo_ctx["role"] not in ROLE_DEPARTMENT_MAP and final_dept:
                ROLE_DEPARTMENT_MAP[coo_ctx["role"]] = final_dept

        # Progress callback — publishes WebSocket events
        async def make_progress_cb(cid, name, idx_val):
            async def cb(step, message):
                await event_bus.publish(CompanyEvent(
                    type="onboarding_progress",
                    payload={"batch_id": batch_id, "candidate_id": cid,
                             "name": name, "step": step,
                             "step_index": ["assigning_id", "copying_skills", "registering_agent", "completed"].index(step) if step != "failed" else -1,
                             "total_steps": 4, "current": idx_val + 1, "total": total,
                             "message": message},
                    agent="HR",
                ))
            return cb

        progress_cb = await make_progress_cb(candidate_id, cand_name, idx)

        try:
            nickname = await generate_nickname(cand_name, final_role, is_founding=False)
            emp = await execute_hire(
                name=cand_name,
                nickname=nickname,
                role=final_role,
                skills=skill_names,
                talent_id=talent_id,
                llm_model=talent_data.get("llm_model", "") or candidate.get("llm_model", ""),
                temperature=float(talent_data.get("temperature", 0.7)),
                image_model=candidate.get("image_model", ""),
                api_provider=talent_data.get("api_provider", "openrouter") or candidate.get("api_provider", "openrouter"),
                hosting=talent_data.get("hosting", "company"),
                auth_method=talent_data.get("auth_method", "api_key"),
                sprite=candidate.get("sprite", "employee_default"),
                remote=candidate.get("remote", False),
                department=final_dept,
                progress_callback=progress_cb,
            )
            results.append({"candidate_id": candidate_id, "status": "hired", "employee_id": emp.id, "name": cand_name, "nickname": nickname})

            # Handle COO notification
            if coo_ctx.get("project_id"):
                auth_method = talent_data.get("auth_method", "api_key")
                if auth_method == "oauth":
                    _pending_oauth_hire[emp.id] = coo_ctx
                else:
                    _notify_coo_hire_ready(emp.id, coo_ctx)

        except Exception as e:
            traceback.print_exc()
            await event_bus.publish(CompanyEvent(
                type="onboarding_progress",
                payload={"batch_id": batch_id, "candidate_id": candidate_id,
                         "name": cand_name, "step": "failed",
                         "step_index": -1, "total_steps": 4, "current": idx + 1, "total": total,
                         "message": str(e)},
                agent="HR",
            ))
            results.append({"candidate_id": candidate_id, "status": "error", "error": str(e)})

    # Resume project lifecycle
    from onemancompany.core.project_archive import append_action, complete_project
    ctx = _pending_project_ctx.pop(batch_id, {})
    pid = ctx.get("project_id", "")
    hired_names = [r["name"] for r in results if r["status"] == "hired"]
    if pid and hired_names:
        append_action(pid, HR_ID, "批量入职完成", f"{', '.join(hired_names)} 已入职")
        complete_project(pid, f"Batch hired: {', '.join(hired_names)}")

    pending_candidates.pop(batch_id, None)

    # Resume HR HOLDING task
    from onemancompany.core.agent_loop import get_agent_loop
    hr_loop = get_agent_loop(HR_ID)
    if hr_loop:
        for t in hr_loop.board.tasks:
            if t.status == "holding" and t.result and f"batch_id={batch_id}" in t.result:
                await hr_loop.resume_held_task(HR_ID, t.id, f"Batch hired: {', '.join(hired_names)}")
                break

    await event_bus.publish(CompanyEvent(type="state_snapshot", payload={}, agent="CEO"))

    return {"status": "ok", "count": len(hired_names), "results": results, "state": company_state.to_json()}
```

**Step 3: Verify compilation**

Run: `.venv/bin/python -c "from onemancompany.api.routes import router; print('OK')"`
Expected: `OK`

**Step 4: Commit**

```bash
git add src/onemancompany/agents/onboarding.py src/onemancompany/api/routes.py
git commit -m "feat: add batch-hire endpoint with onboarding progress events"
```

---

### Task 4: Frontend — Role-Grouped Candidate Panel + Multi-Select

**Files:**
- Modify: `/Users/yuzhengxu/projects/OneManCompany/frontend/index.html`
- Modify: `/Users/yuzhengxu/projects/OneManCompany/frontend/app.js`
- Modify: `/Users/yuzhengxu/projects/OneManCompany/frontend/style.css`

**Context:** Use `/frontend-design` skill for this task. The current candidate modal (`candidate-modal`) shows a flat grid of flip cards with single "Hire" buttons. We need to redesign it to:
1. Show candidates grouped by role (tabs or accordion sections)
2. Each candidate card has a checkbox for multi-select (0 to N per group)
3. Cards display AI match score + reasoning
4. Bottom sticky bar shows selected count + "Batch Hire (N)" button
5. Keep interview functionality

**Step 1: Add onboarding progress modal to `index.html`**

Add after the interview modal (after line ~429):

```html
<!-- Onboarding Progress Modal -->
<div id="onboarding-progress-modal" class="modal-overlay hidden">
  <div class="modal-content onboarding-progress-content">
    <div class="modal-header">
      <h3 class="pixel-title">&#128640; Onboarding Progress</h3>
    </div>
    <div class="modal-body">
      <div id="onboarding-progress-list"></div>
      <div id="onboarding-progress-summary" class="hidden"></div>
    </div>
    <div id="onboarding-progress-actions" class="hidden" style="display:flex;justify-content:flex-end;padding:6px 8px;">
      <button id="onboarding-done-btn" class="pixel-btn" style="font-size:7px;padding:4px 12px;">Close</button>
    </div>
  </div>
</div>
```

**Step 2: Rewrite `showCandidateSelection()` in `app.js`**

Replace the existing function (lines ~2327-2414) with the role-grouped version. The function should:
- Accept `payload.roles` (new format) or fall back to `payload.candidates` (old format)
- Render role sections with headers
- Each candidate card has a checkbox
- Bottom bar with selected count and "Batch Hire" button
- Track selections in `this._batchSelections = new Map()` (candidate_id → {role, candidate})

**Step 3: Add `batchHireCandidates()` function in `app.js`**

New function that:
1. Collects all checked candidates from `_batchSelections`
2. Calls `POST /api/candidates/batch-hire`
3. Opens the onboarding progress modal
4. Listens for `onboarding_progress` WebSocket events to update the modal

**Step 4: Add `handleOnboardingProgress(payload)` function in `app.js`**

Handles `onboarding_progress` WebSocket events:
1. Find the row for `payload.candidate_id` in the progress modal
2. Update the step indicator and message
3. When all candidates reach "completed" or "failed", show summary and Close button

**Step 5: Register `onboarding_progress` event in the WebSocket message handler**

In the event type handlers (around line 138), add:
```javascript
'onboarding_progress': (p) => {
    this.handleOnboardingProgress(p);
    return null;  // no activity log entry
},
```

**Step 6: Add CSS styles for new components**

- `.role-group-section` — role section container with header
- `.role-group-header` — role title + description
- `.candidate-checkbox` — checkbox overlay on card
- `.batch-hire-bar` — sticky bottom bar with count + button
- `.onboarding-progress-content` — progress modal styling
- `.onboarding-progress-item` — each candidate's progress row
- `.progress-step` — step indicator dots/labels
- `.progress-step.active` / `.progress-step.done` / `.progress-step.failed`

**Step 7: Use `/frontend-design` skill**

Apply the frontend-design skill for the visual design of:
- Role-grouped candidate sections with distinctive headers
- Multi-select cards with score/reasoning display
- Onboarding progress modal with step indicators
- Sticky batch action bar

**Step 8: Commit**

```bash
git add frontend/index.html frontend/app.js frontend/style.css
git commit -m "feat: role-grouped candidate panel with multi-select and onboarding progress"
```

---

### Task 5: Integration testing

**Step 1: Verify full flow manually**

1. Start Talent Market: `cd /Users/yuzhengxu/projects/talentmarket && uv run python -m talentmarket.main`
2. Start OneManCompany: `cd /Users/yuzhengxu/projects/OneManCompany && .venv/bin/python -m onemancompany.main`
3. Trigger a hiring request (via COO or direct API)
4. Verify AI search returns role-grouped results
5. Verify frontend shows role groups with multi-select
6. Select multiple candidates and batch hire
7. Verify onboarding progress modal shows real-time updates
8. Verify all employees are created successfully

**Step 2: Run unit tests**

Run: `.venv/bin/python -m pytest tests/unit/ -x --tb=short`
Expected: All tests pass

**Step 3: Commit any test fixes**

```bash
git add -A
git commit -m "fix: integration fixes for batch hire flow"
```
