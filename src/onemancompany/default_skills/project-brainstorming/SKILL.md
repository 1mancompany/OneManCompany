# Project Brainstorming — Requirements Discovery Before Execution

Turn vague ideas into clear specs with acceptance criteria through ONE structured
interaction with the CEO. Understand what to build before building it.

<HARD-GATE>
Do NOT dispatch any project work to the team until the CEO has approved your
proposed plan. This applies to EVERY project regardless of perceived simplicity.
</HARD-GATE>

## When to Use This Skill

**USE brainstorming for:**
- New product/feature requests ("build X", "create Y", "design Z")
- Multi-step projects that involve multiple team members
- Ambiguous tasks where CEO intent needs clarification

**SKIP brainstorming for:**
- Simple operational tasks ("check status", "send email", "look up info")
- Urgent fixes ("the site is down", "fix this bug now")
- Tasks where CEO gave explicit, detailed instructions with clear criteria
- Follow-up tasks on existing projects where scope is already defined
- CEO explicitly says "just do it" or "skip the questions"

---

## The Process (Single CEO Interaction)

IMPORTANT: The entire brainstorming happens in ONE dispatch_child to CEO.
Do NOT send multiple separate questions as separate dispatch_child calls.
Each dispatch_child creates a new task cycle — keep it to ONE round.

### Step 1: Analyze Silently (no CEO interaction)

Before asking the CEO anything:
1. Read existing project files if this is a follow-up
2. Check what team members are available (use list_colleagues)
3. Form your own understanding of the task

### Step 2: Send ONE Structured Proposal to CEO

Combine your questions AND a draft plan into a SINGLE dispatch_child:

```
dispatch_child(
    target_employee_id="00001",
    description="""
I've analyzed your request. Before I dispatch the team, I'd like to confirm a few things:

**My understanding**: [1-2 sentences of what you think CEO wants]

**Quick questions** (answer inline or skip any that are obvious):
1. [Most important clarification — prefer A/B/C multiple choice]
2. [Second question if needed]
3. [Third question if needed — max 3]

**My proposed plan**:

Approach A (Recommended): [2-3 sentences]
- Team: [who does what]
- Pros: [why this is better]

Approach B: [2-3 sentences]
- Team: [who does what]
- Pros: [alternative advantage]

**Proposed acceptance criteria**:
1. [measurable criterion]
2. [measurable criterion]
3. [measurable criterion]

Please confirm the approach and criteria, adjust anything, or just say "go" to proceed with my recommendation.
""",
    acceptance_criteria=["CEO confirms or adjusts the project plan"]
)
```

### Step 3: Execute After CEO Confirms

After CEO responds:
1. Call `set_project_name()` with a concise 2-6 word name
2. Incorporate any CEO feedback into the final plan
3. Dispatch to O-level executives with the confirmed acceptance criteria
4. Save a brief `design.md` to the project workspace if the project is medium/large scope

---

## Key Principles

- **ONE interaction with CEO, not a multi-round Q&A.** Combine questions + proposal into a single message. CEO responds once, you execute.
- **Never assume scope.** "Build a website" could mean a landing page or a full platform.
- **Always present 2+ approaches** with trade-offs, even if one is clearly better.
- **Acceptance criteria are a contract.** Once CEO confirms, deliver against them.
- **Challenge premises when warranted.** "Is X the real goal?" can save weeks of wasted work.
- **If CEO says "just do it"** — respect that and dispatch immediately with your best judgment.
- **Scope decomposition.** If a project is too large, propose breaking it into phases.
