# Workflow Schema Validation Design

## Problem

Workflow markdown files in `company/human_resource/workflows/` have inconsistent formats. Some have full metadata and structured phases, others are plain numbered lists. There is no validation — malformed files are silently accepted. When employees (COO) write new workflows, nothing enforces correctness.

## Goals

1. Define a standard workflow markdown format with `goal` (required) and `depends_on` (optional) fields per step
2. Validate workflows on save — reject malformed files with actionable error messages
3. Inject schema documentation progressively — minimal prompt overhead, full doc loaded on demand
4. Unify all 5 existing workflow files to the new format

## Design

### 1. WorkflowStep New Fields

Add two fields to the `WorkflowStep` dataclass in `workflow_engine.py`:

```python
@dataclass
class WorkflowStep:
    index: int
    title: str
    owner: str
    instructions: list[str]
    output_description: str
    raw_text: str
    collaborators: str = ""
    goal: str = ""              # NEW: what this step must achieve
    depends_on: list[int] = []  # NEW: indices of prerequisite phases
```

Corresponding markdown format per phase:

```markdown
## Phase N: Title

- **Goal**: What this step must achieve (REQUIRED)
- **Responsible**: Who executes (REQUIRED)
- **Depends on**: Phase 1, Phase 3 (OPTIONAL, default: sequential)
- **Collaborators**: Additional participants (OPTIONAL)
- **Steps**:
  1. Concrete action
  2. Another action
- **Output**: What this step produces (OPTIONAL)
```

### 2. Validation

New function `validate_workflow(wf: WorkflowDefinition) -> list[str]` in `workflow_engine.py`.

Returns a list of error strings. Empty list = valid.

**Workflow-level checks:**
- `flow_id` must be non-empty
- `owner` must be non-empty

**Step-level checks:**
- `goal` must be non-empty (required field)
- `owner` (Responsible) must be non-empty (required field)
- `depends_on` indices must reference existing steps (no self-reference, no out-of-bounds)

**Not validated** (nice-to-have but not fatal):
- `instructions` (Steps block)
- `output_description` (Output field)
- `collaborators`

**Error class:**

```python
class WorkflowValidationError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__(f"Workflow validation failed: {'; '.join(errors)}")
```

### 3. Save Workflow Chain

Update `save_workflow()` call chain:

```
save_workflow(name, content)
  → parse_workflow(content)
  → validate_workflow(parsed)
  → if errors: raise WorkflowValidationError(errors)
  → write to disk
```

API endpoint `PUT /api/workflows/{name}` catches `WorkflowValidationError` and returns 422 with error list. COO agent receives errors and can fix the content.

### 4. Schema Document (Progressive Injection)

Define `WORKFLOW_SCHEMA_DOC` constant in `workflow_engine.py` — the full format specification with a brief example.

**Injection strategy:**
- Register as a company-level SOP document (same pattern as skills catalog with `autoload: false`)
- All employees see one line in their prompt: `workflow_schema — standard workflow format spec, must read before writing/editing workflows`
- Full content loaded on demand when employee needs to write/edit a workflow

**Prompt cost:** ~1 line per employee (catalog entry), ~400-600 tokens only when actively loaded.

### 5. Parse Updates

Update `_parse_step_section()` to extract:
- `**Goal**:` line → `step.goal`
- `**Depends on**:` line → parse "Phase N" references into `step.depends_on` as list of ints

`depends_on` parsing: extract integers from patterns like "Phase 1", "Phase 3" → `[0, 2]` (0-indexed).

### 6. Existing Workflow Migration

| File | Action |
|------|--------|
| `project_retrospective_workflow.md` | Add **Goal** to each phase, keep existing structure |
| `hiring_workflow.md` | Add **Goal** to each phase, keep existing structure |
| `project_intake_workflow.md` | Rewrite: add header metadata + convert to `##` phases with Goal/Responsible/Steps |
| `onboarding_workflow.md` | Rewrite: add header metadata + rename **Owner** to **Responsible** + add Goal/Steps |
| `offboarding_workflow.md` | Rewrite: add header metadata + rename **Owner** to **Responsible** + add Goal/Steps |

Business logic unchanged — only format standardized.

## Out of Scope

- DAG-based parallel execution using `depends_on` (future enhancement)
- Generic handler using `goal` for self-checking (future enhancement)
- Workflow versioning or migration tooling

## Files Changed

- `src/onemancompany/core/workflow_engine.py` — new fields, parse updates, validate function, schema doc, error class
- `src/onemancompany/core/config.py` — update `save_workflow()` to validate before writing
- `src/onemancompany/api/routes.py` — catch `WorkflowValidationError` in PUT endpoint, return 422
- `company/human_resource/workflows/*.md` — all 5 files migrated to standard format
- SOP registration (wherever company SOPs are registered for employee prompt injection)
- Tests for validation logic and parse updates
