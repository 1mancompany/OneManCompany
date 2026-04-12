# Project Brainstorming — Requirements Discovery Before Execution

Turn vague ideas into clear specs with acceptance criteria through collaborative
dialogue with the CEO. Understand what to build before building it.

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

## Phase 1: Understand Context (no CEO interaction)

Before asking the CEO anything:
1. Read existing project files if this is a follow-up
2. Check what team members are available (use list_colleagues)
3. Form your own understanding of the task

---

## Phase 2: Ask Clarifying Questions (ONE AT A TIME)

Ask the CEO questions via `dispatch_child(target_employee_id="00001", ...)`.
The system preserves your conversation history — when CEO replies, you will
see ALL prior CEO replies in your task context under [CEO REPLY] sections.

### Rules:
- **One question per dispatch.** Do NOT bundle multiple questions.
- **Prefer multiple choice** when possible — easier for CEO to answer.
- **Max 3-4 questions** — respect CEO's time. Stop when you have enough context.
- **Check your [CEO REPLY] sections** — do NOT re-ask questions CEO already answered.

### What to ask about:
1. **Purpose & Users**: Who is this for? What problem does it solve?
2. **Scope**: What's the minimum viable version? What can wait for later?
3. **Constraints**: Tech stack, timeline, budget, hosting preferences?
4. **Success criteria**: How will the CEO know this is done and successful?
5. **Priority**: Speed vs. quality? MVP vs. polished?

### Good question examples:
- "What's the primary goal? (A) Generate revenue (B) Attract users (C) Internal tool (D) Research/learning"
- "Who is the target user? (A) End consumers (B) Businesses (C) Internal team (D) Developers"
- "What's the priority? (A) Ship fast with basic quality (B) Take time for polish"

---

## Phase 3: Challenge Premises

Before proposing solutions, question your own assumptions:

- **Is the stated problem the real problem?**
- **Does this need to be built at all?** Is there a simpler approach?
- **Is the scope right?** If too large, propose breaking into phases.

If you identify a premise worth challenging, raise it with the CEO via
dispatch_child(target_employee_id="00001").

---

## Phase 4: Propose 2-3 Approaches (MANDATORY)

You MUST present at least 2 approaches with trade-offs:

```
dispatch_child(
    target_employee_id="00001",
    description="""
Based on our discussion, here are the approaches I see:

## Approach A: [Name] (Recommended)
**What**: [2-3 sentences]
**Pros**: [bullet points]
**Cons**: [bullet points]
**Team**: [who does what]

## Approach B: [Name]
**What**: [2-3 sentences]
**Pros**: [bullet points]
**Cons**: [bullet points]
**Team**: [who does what]

**My recommendation**: Approach [X] because [one-line reason].

**Proposed Acceptance Criteria**:
1. [measurable, verifiable criterion]
2. [measurable, verifiable criterion]
3. [measurable, verifiable criterion]

Please choose an approach and confirm/adjust the acceptance criteria.
""",
    acceptance_criteria=["CEO selects approach and approves acceptance criteria"]
)
```

---

## Phase 5: Execute After CEO Confirms

After CEO approves:
1. Call `set_project_name()` with a concise 2-6 word name
2. Incorporate CEO feedback into the final plan
3. Dispatch to O-level executives with the confirmed acceptance criteria
4. Save a brief `design.md` to the project workspace for medium/large projects

---

## Key Principles

- **CEO's time is the scarcest resource.** Keep questions focused and minimal.
- **Never assume scope.** "Build a website" could mean a landing page or a full platform.
- **Always present 2+ approaches** with trade-offs.
- **Acceptance criteria are a contract.** Once CEO confirms, deliver against them.
- **Check [CEO REPLY] sections** before each turn — don't lose context or re-ask.
- **If CEO says "just do it"** — respect that and dispatch immediately.
- **Scope decomposition.** If too large, propose phases. Each phase gets its own cycle.
