# Cost Tracking

OneManCompany tracks LLM token usage and USD cost per project, so you always know what your AI company is spending.

## What's Tracked

- **Token usage** — Input and output tokens per employee per task
- **USD cost** — Calculated from model pricing (varies by provider and model)
- **Per-project breakdown** — See exactly how much each project costs

## How It Works

Every LLM call made by an employee is logged with:

- Employee ID
- Task/project context
- Model used
- Token count (prompt + completion)
- Estimated cost

## Viewing Costs

Cost data is available in the CEO console. You can see:

- **Per-task costs** — How much each individual task consumed
- **Per-project costs** — Total cost across all subtasks in a project
- **Per-employee costs** — Which employees are most expensive

## Cost Optimization Tips

!!! tip "Choose Models Wisely"
    Each employee can be assigned a different model. Use cheaper models for simple tasks (email, summaries) and more capable models for complex work (coding, design).

!!! tip "Use Claude Code for Heavy Work"
    If you have a Claude subscription, switching founding employees to Claude Code mode for complex tasks can reduce per-token OpenRouter costs.

!!! tip "Monitor Project Costs Early"
    Check costs during a project, not just after. If a task is burning through tokens, consider providing clearer instructions or breaking it into smaller pieces.

## Cost vs Quality

Different models offer different cost/quality trade-offs:

| Tier | Example Models | Best For |
| --- | --- | --- |
| Budget | Smaller open models | Simple tasks, high volume |
| Mid-range | Claude Haiku, GPT-4o-mini | Routine work, coordination |
| Premium | Claude Opus, GPT-4o | Complex reasoning, coding |

The flexibility to assign different models per employee lets you optimize this balance across your organization.
