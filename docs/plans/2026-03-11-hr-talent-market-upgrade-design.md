# HR-Talent Market Recruitment Upgrade Design

**Goal:** Upgrade the HR↔Talent Market pipeline to support team-level hiring with AI search, multi-select onboarding, and real-time progress tracking.

**Architecture:** Replace keyword matching in MCP server with LLM-powered AI search. HR enriches team requests into multi-role JDs. Frontend shows role-grouped candidates with multi-select and batch onboarding with progress modal.

## 1. MCP Server (Talent Market)

Replace `search_candidates` in `mcp_server.py` with AI search logic (from `ai_search.py`).

**Input:** `job_description: str, count: int = 10`

**Output format (unified for individual and team):**
```json
{
  "type": "individual|team",
  "summary": "AI analysis summary",
  "roles": [
    {
      "role": "Game Engineer",
      "description": "Roblox game development",
      "candidates": [
        {
          "talent_id": "xxx",
          "name": "...",
          "role": "Engineer",
          "description": "...",
          "skills": [],
          "hosting": "company",
          "personality_tags": [],
          "hiring_fee": 0.2,
          "salary_per_1m_tokens": 0.0,
          "score": 0.92,
          "reasoning": "Strong match because..."
        }
      ]
    }
  ]
}
```

Single-role JDs return `type: "individual"` with one role group.

## 2. HR Agent (recruitment.py)

### submit_shortlist upgrade

Pass role-grouped structure through `candidates_ready` event:
```python
payload = {
    "batch_id": "abc123",
    "type": "team|individual",
    "jd": "enriched JD text",
    "roles": [
        {
            "role": "Engineer",
            "description": "...",
            "candidates": [full_candidate_profiles...]
        }
    ]
}
```

### HR enrichment

When HR receives a team-level request (e.g., "build a 3-person game team"), it enriches into a detailed multi-role JD before calling `search_candidates`. This happens naturally via HR's LLM — no code change needed, just prompt awareness.

## 3. Frontend — Candidate Selection Panel

### Layout change

- **Left sidebar:** JD description (unchanged)
- **Right area:** Role-grouped sections (accordion or vertical sections)
  - Section header: role name + description
  - Candidate cards with checkbox (multi-select, 0 to N per group)
  - Cards show AI match score + reasoning
  - Interview button preserved
- **Bottom sticky bar:** Selected count + "Batch Hire (N)" button

### Backward compatibility

If `payload.roles` is absent (old-style flat candidates), render flat list with single-select as before.

## 4. Onboarding Progress Modal

New modal, triggered by "Batch Hire" button.

### Content

- List of selected candidates, each showing:
  - Avatar emoji + name + target role
  - Progress indicator with current step label
  - Status: waiting | assigning_id | copying_skills | registering_agent | completed | failed
- Bottom: "All onboarding complete" message + Close button when done

### WebSocket events

Backend pushes `onboarding_progress` events:
```json
{
  "type": "onboarding_progress",
  "batch_id": "abc123",
  "candidate_id": "talent_a",
  "name": "Coding Talent",
  "step": "copying_skills",
  "step_index": 2,
  "total_steps": 4,
  "message": "Copying skill packages..."
}
```

Steps: `assigning_id` → `copying_skills` → `registering_agent` → `completed` (or `failed`)

## 5. Backend — Batch Hire API

### `POST /api/candidates/batch-hire`

Request:
```json
{
  "batch_id": "abc123",
  "selections": [
    {"candidate_id": "talent_a", "role": "Engineer"},
    {"candidate_id": "talent_b", "role": "Designer"}
  ]
}
```

Response (immediate):
```json
{"status": "ok", "count": 2, "message": "Batch onboarding started"}
```

Backend iterates selections, calls `execute_hire()` for each, publishing WebSocket progress events at each step. All hires run sequentially (to avoid race conditions on employee numbering).

## 6. execute_hire() progress hooks

Add WebSocket publish calls at key points in `execute_hire()`:
- Before: `assigning_id`
- After desk/profile setup: `copying_skills`
- After skill/tool copy: `registering_agent`
- After completion: `completed`
- On error: `failed`

## 7. Files to modify

| File | Change |
|------|--------|
| `talentmarket/src/.../mcp_server.py` | Replace `search_candidates` with AI search |
| `talentmarket/src/.../ai_search.py` | Extract reusable core for MCP |
| `src/.../agents/recruitment.py` | Upgrade shortlist to role-grouped format |
| `src/.../agents/onboarding.py` | Add progress WebSocket hooks to `execute_hire()` |
| `src/.../api/routes.py` | Add `POST /api/candidates/batch-hire` endpoint |
| `frontend/index.html` | New onboarding progress modal HTML |
| `frontend/app.js` | Role-grouped candidate UI + multi-select + batch hire + progress modal |
| `frontend/style.css` | Styles for new components |
