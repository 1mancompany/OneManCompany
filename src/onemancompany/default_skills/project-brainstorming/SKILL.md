# Project Brainstorming — Requirements Discovery Before Execution

Before dispatching any project-level task to the team, you MUST go through this
brainstorming process with the CEO. The goal: understand what to build before
building it. Turn vague ideas into clear specs with acceptance criteria.

## When to Use This Skill

**USE brainstorming for:**
- New product/feature requests ("build X", "create Y", "design Z")
- Multi-step projects that involve multiple team members
- Ambiguous tasks where CEO intent needs clarification
- Tasks with unclear success criteria

**SKIP brainstorming for:**
- Simple operational tasks ("check status", "send email", "look up info")
- Urgent fixes ("the site is down", "fix this bug now")
- Tasks where CEO gave explicit, detailed instructions with clear criteria
- Follow-up tasks on existing projects where scope is already defined

## The Process

### Phase 1: Understand Context

Before asking any questions:
1. Read existing project files if this is a follow-up
2. Check what team members are currently working on (use list_colleagues)
3. Understand the company's current state

### Phase 2: Ask Clarifying Questions (ONE AT A TIME)

Ask the CEO questions via `dispatch_child(target_employee_id="00001", ...)` to
understand requirements. Rules:

- **One question per dispatch.** Do NOT bundle multiple questions.
- **Prefer multiple choice** when possible — easier for CEO to answer.
- **Focus on**: purpose, target users, constraints, timeline, success criteria.
- **Max 3-4 questions** — respect CEO's time. Stop if you have enough context.

Good questions:
- "What's the primary goal? (A) Generate revenue (B) Attract users (C) Internal tool (D) Other"
- "Who is the target user for this?"
- "What does success look like? How will you know this is done?"
- "Any technical constraints or preferences? (specific tech stack, hosting, etc.)"
- "What's the priority: speed of delivery vs. polish/quality?"

Bad questions:
- Overly technical questions the CEO shouldn't need to answer
- Questions you could answer yourself by reading existing context
- Multiple questions in one message

### Phase 3: Propose Approach

After gathering answers, present a plan to the CEO:

```
dispatch_child(
    target_employee_id="00001",
    description="""
Based on your answers, here is my proposed plan:

**Goal**: [one sentence]

**Approach**: [2-3 sentences describing how the team will execute]

**Team assignments**:
- COO → [what COO's team will do]
- HR → [hiring needs, if any]
- CSO → [sales/marketing, if any]

**Acceptance Criteria** (how we know it's done):
1. [measurable criterion]
2. [measurable criterion]
3. [measurable criterion]

**Estimated scope**: [small/medium/large]

Please confirm this plan, or tell me what to adjust.
""",
    acceptance_criteria=["CEO approves the project plan"]
)
```

### Phase 4: Execute

Only after CEO confirms the plan:
1. Call `set_project_name()` with a concise 2-6 word name
2. Dispatch to O-level executives using the confirmed plan and acceptance criteria
3. The acceptance criteria from Phase 3 become the real criteria for the dispatched tasks

## Key Principles

- **The CEO's time is the scarcest resource.** Keep questions focused and minimal.
- **Never assume scope.** A CEO saying "build a website" could mean a landing page or a full platform. Ask.
- **Acceptance criteria are a contract.** Once CEO confirms, that's what you deliver against.
- **If CEO says "just do it" or "skip the questions"** — respect that and dispatch immediately with your best judgment on criteria.
