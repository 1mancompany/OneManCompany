# 1-on-1 Coaching

One of the most powerful features in OneManCompany: CEO guidance sessions that permanently shape how an employee works.

## How It Works

When you coach an employee through a 1-on-1 session:

1. You select an employee and start a guidance session
2. You provide feedback, direction, or new principles
3. The system distills your guidance into the employee's **work principles** (`work_principles.md`)
4. From that point forward, the employee's behavior reflects your coaching

This isn't a temporary instruction — it's a permanent behavioral change, just like how a real mentor shapes a junior employee over time.

## What You Can Coach On

- **Work style** — "Be more concise in your reports", "Always include test cases"
- **Technical approach** — "Prefer functional patterns over OOP", "Use TypeScript strictly"
- **Communication** — "Summarize decisions at the end of every meeting"
- **Quality standards** — "Never ship without unit tests", "Always consider edge cases"
- **Domain knowledge** — "Our users are non-technical, keep UX simple"

## How It Persists

Your guidance is written into the employee's `work_principles.md` file:

```
employees/
└── 00006/
    ├── profile.yaml
    ├── work_principles.md    ← Your coaching lives here
    └── guidance.yaml
```

Every time the employee works on a task, their work principles are injected into their reasoning context. The employee literally thinks differently after your coaching.

## Tips for Effective Coaching

!!! tip "Be Specific"
    Instead of "write better code", say "always add error handling for network calls and log the error details before retrying."

!!! tip "Coach After Reviewing Work"
    The best time to coach is after reviewing a task deliverable. Concrete feedback tied to real output is more impactful than abstract principles.

!!! tip "Cumulative Effect"
    Each coaching session builds on previous ones. Over time, your employees develop a sophisticated understanding of your standards and preferences.

## Organizational Evolution

Coaching isn't just about individual improvement. The cumulative effect across your team creates organizational evolution:

- Employee-level insights become **work principles**
- Project-level lessons become **company knowledge base**
- Combined, your AI company gets better with every project

This is why OneManCompany is an operating system, not just a tool — the organization itself learns and improves.
