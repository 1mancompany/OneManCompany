# Project Brainstorming — Requirements Discovery Before Execution

Turn vague ideas into clear specs with acceptance criteria through collaborative
dialogue with the CEO. Understand what to build before building it.

<HARD-GATE>
Do NOT dispatch any project work to the team until the CEO has approved your
proposed plan. This applies to EVERY project regardless of perceived simplicity.
"Simple" projects are where unexamined assumptions cause the most wasted work.
</HARD-GATE>

## Anti-Pattern: "This Is Too Simple To Need A Design"

Every project goes through this process. A landing page, a single API, a config
change — all of them. The design can be SHORT (a few sentences for truly simple
projects), but you MUST present it and get CEO approval before dispatching.

## When to Use This Skill

**USE brainstorming for:**
- New product/feature requests ("build X", "create Y", "design Z")
- Multi-step projects that involve multiple team members
- Ambiguous tasks where CEO intent needs clarification
- Tasks with unclear success criteria

**SKIP brainstorming for:**
- Simple operational tasks ("check status", "send email", "look up info")
- Urgent fixes ("the site is down", "fix this bug now")
- Follow-up tasks on existing projects where scope is already defined
- Tasks where CEO says "just do it" or "skip the questions"

---

## Phase 1: Understand Context

Before asking any questions:
1. Read existing project files if this is a follow-up
2. Check what team members are currently working on (use list_colleagues)
3. Understand the company's current state and capabilities

Output to yourself (do not send to CEO): "Here is what I understand about the
context: [summary]. Here is what I still need to know: [gaps]."

---

## Phase 2: Ask Clarifying Questions (ONE AT A TIME)

Ask the CEO questions via `dispatch_child(target_employee_id="00001", ...)`.

### Rules:
- **One question per dispatch.** Do NOT bundle multiple questions.
- **Prefer multiple choice** when possible — easier for CEO to answer.
- **Max 3-5 questions** — respect CEO's time. Stop when you have enough context.
- **Each question should unlock a decision.** Don't ask just to ask.

### What to ask about:
1. **Purpose & Users**: Who is this for? What problem does it solve?
2. **Scope**: What's the minimum viable version? What can wait for later?
3. **Constraints**: Tech stack, timeline, budget, hosting preferences?
4. **Success criteria**: How will the CEO know this is done and successful?
5. **Priority**: Speed vs. quality? MVP vs. polished?

### Good question examples:
- "What's the primary goal? (A) Generate revenue (B) Attract users (C) Internal tool (D) Research/learning"
- "Who is the target user? (A) End consumers (B) Businesses (C) Internal team (D) Developers"
- "What's the priority? (A) Ship fast with basic quality (B) Take time for polish (C) Somewhere in between"
- "Any technical constraints? (A) Use specific tech stack [which?] (B) Must deploy to [where?] (C) No constraints, you decide"

### Bad questions (never ask these):
- Questions you could answer by reading existing context
- Overly technical questions the CEO shouldn't need to answer
- Multiple questions bundled in one message
- Questions with obvious answers ("should this work correctly?")

---

## Phase 3: Challenge Premises

Before proposing solutions, question your own assumptions:

- **Is the stated problem the real problem?** Sometimes "build a website" really
  means "get more customers." The solution might not be a website.
- **Does this need to be built at all?** Is there an existing tool, service, or
  simpler approach that achieves the same goal?
- **Is the scope right?** If the project touches multiple independent systems,
  flag it: "This looks like 2-3 separate projects. Should we tackle them one at
  a time?"

If you identify a premise worth challenging, raise it with the CEO:
```
dispatch_child(
    target_employee_id="00001",
    description="Before I plan execution, I want to check one assumption: [premise]. Is that correct, or should we think about this differently?",
    acceptance_criteria=["CEO confirms or adjusts the premise"]
)
```

---

## Phase 4: Propose 2-3 Approaches (MANDATORY)

You MUST present at least 2 approaches, each with trade-offs. Do NOT present a
single "recommended plan" without alternatives.

```
dispatch_child(
    target_employee_id="00001",
    description="""
Based on our discussion, here are the approaches I see:

## Approach A: [Name] (Recommended)
**What**: [2-3 sentences]
**Pros**: [bullet points]
**Cons**: [bullet points]
**Scope**: [small/medium/large]
**Team**: [who does what]

## Approach B: [Name]
**What**: [2-3 sentences]
**Pros**: [bullet points]
**Cons**: [bullet points]
**Scope**: [small/medium/large]
**Team**: [who does what]

## (Optional) Approach C: [Name]
[same format]

---

**My recommendation**: Approach [X] because [one-line reason].

**Proposed Acceptance Criteria** (how we know it's done):
1. [measurable, verifiable criterion]
2. [measurable, verifiable criterion]
3. [measurable, verifiable criterion]
4. [measurable, verifiable criterion]

Please choose an approach (or mix elements), and confirm/adjust the acceptance criteria.
""",
    acceptance_criteria=["CEO selects approach and approves acceptance criteria"]
)
```

### Acceptance Criteria Rules:
- Every CEO requirement must map to at least one criterion
- Criteria must be **verifiable** — pass/fail against actual deliverables
- Include both functional criteria ("feature X works") and quality criteria ("deployed and accessible")
- If CEO asked to review something, include "CEO approves [what]" as a criterion

---

## Phase 5: Write Design Summary

After CEO approves, save a brief design doc to the project workspace:

```
write(file_path="[project_workspace]/design.md", content="""
# [Project Name] — Design Summary

## Problem
[What we're solving and why]

## Approach
[Selected approach from Phase 4]

## Acceptance Criteria
1. [criterion 1]
2. [criterion 2]
3. [criterion 3]

## Team Plan
- [who does what]

## Constraints & Decisions
- [key decisions made during brainstorming]

## Out of Scope (deferred)
- [what we explicitly decided NOT to do now]
""")
```

---

## Phase 6: Execute

Only after CEO confirms the plan:
1. Call `set_project_name()` with a concise 2-6 word name
2. Dispatch to O-level executives using the confirmed approach and acceptance criteria
3. The acceptance criteria from Phase 4 become the real criteria for the dispatched tasks

---

## Key Principles

- **CEO's time is the scarcest resource.** Keep questions focused and minimal.
- **Never assume scope.** "Build a website" could mean a landing page or a full platform.
- **Always present alternatives.** Even if one approach is obviously better, show why by contrasting.
- **Acceptance criteria are a contract.** Once CEO confirms, that's what you deliver against.
- **Challenge premises respectfully.** "Is X the real goal?" is valuable; "X is wrong" is not.
- **If CEO says "just do it"** — respect that and dispatch immediately with your best judgment. Not every task needs 5 phases.
- **Scope decomposition.** If a project is too large for one team to deliver in a reasonable time, propose breaking it into phases. Each phase gets its own brainstorming cycle.
