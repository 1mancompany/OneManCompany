---
tags: [operations, hiring, hr]
source: company/human_resource/workflows/hiring_workflow.md, agents/recruitment.py
---

# Hiring Workflow

## Talent Sources

| Mode | Source | Config |
|------|--------|--------|
| **Local** | `talents/` folder on disk | `talent_market.mode: local` |
| **Remote** | Cloud Talent Market API (MCP SSE) | `talent_market.mode: remote` + API key |

## Search Flow

1. CEO gives hiring instruction to HR
2. HR calls `search_candidates(job_description)`
3. If remote + AI search enabled → AI-powered matching (better quality)
4. If remote + AI fails (no credits) → fallback to standard search → fallback to local
5. Results auto-submitted as shortlist to CEO

## Shortlist & Interview

1. CEO sees candidates grouped by role in frontend
2. CEO can interview candidates (LLM simulates candidate responses)
3. CEO selects candidates to hire
4. `execute_hire()` runs [[Onboarding Workflow]]

## Talent Market Settings

| Setting | Where | Default |
|---------|-------|---------|
| `mode` | config.yaml, Settings UI | `local` (no key) / `remote` (with key) |
| `use_ai_search` | config.yaml, Settings UI | `false` |
| `api_key` | config.yaml, Settings UI | `""` |

## Error Handling

When AI search fails (e.g. insufficient credits):
1. Warning passed to HR agent: `"AI search unavailable ({reason}). Showing standard results."`
2. Automatic fallback to non-AI search
3. If that also fails → fallback to local talent pool

## Related
- [[Onboarding Workflow]] — What happens after hire
- [[Vessel System]] — Talent → Employee conversion
- [[Task Lifecycle]] — Task created for each hire
