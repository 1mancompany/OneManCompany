# Company Configuration

Three configuration layers let you shape your AI company's identity and behavior — making the same OS run completely different companies.

## Company Direction

Your vision statement, injected into every employee's reasoning. Change the direction, and the entire company pivots.

**What it controls:**

- What products to build
- Which market to target
- Strategic priorities
- Quality vs speed trade-offs

**Example:**

> "We are a mobile-first game studio targeting casual gamers aged 18-35. We prioritize polished UX and rapid iteration over feature completeness."

Every employee — from COO breaking down tasks to engineers writing code — factors this direction into their decisions.

## Company Culture

Behavioral principles that govern how every employee works. Same Talents, same Vessels, completely different company personality.

**What it controls:**

- Communication style (formal vs casual)
- Decision-making approach (move fast vs be thorough)
- Collaboration norms
- Quality standards

**Example:**

> - Ship fast, iterate faster. MVP over perfection.
> - Write code that a junior dev can read.
> - Every PR must include tests. No exceptions.
> - Default to async communication. Meetings only when blocking.

## Workflows

Workflow definitions in `company_rules/` control how the company operates at a process level.

**What they define:**

- How tasks flow through the organization
- Approval chains and quality gates
- Standard operating procedures
- Automation triggers

Workflows are defined as Markdown files, parsed by the workflow engine, and dispatched to the appropriate handlers.

## Configuration Files

| File | What It Controls |
| --- | --- |
| Company Direction docs | Strategic vision and priorities |
| Company Culture docs | Behavioral norms and standards |
| `company_rules/*.md` | Workflow definitions and SOPs |
| `employees/{id}/profile.yaml` | Individual employee configuration |
| `employees/{id}/guidance.yaml` | Employee-specific guidance notes |

## The OS Analogy

This is what makes OneManCompany an operating system:

- **Same iOS** can be a work phone or a kid's gaming device — depends on apps and settings
- **Same OneManCompany** can be a game studio or a dev agency — depends on Direction, Culture, and Talents

Swap the configuration, swap the company.
