# Workflow Schema Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `goal` (required) and `depends_on` (optional) fields to WorkflowStep, validate workflows on save, and inject schema docs progressively via the existing SOP catalog.

**Architecture:** Extend `workflow_engine.py` with new dataclass fields, parsing, validation, and a schema doc constant. Update `save_workflow()` to validate-before-write. Update the API endpoint to return 422 on validation failure. Migrate all 5 existing workflow files to the standard format.

**Tech Stack:** Python dataclasses, regex parsing, pytest

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `src/onemancompany/core/workflow_engine.py` | Modify | Add fields, parse `**Goal**`/`**Depends on**`, `validate_workflow()`, `WorkflowValidationError`, `WORKFLOW_SCHEMA_DOC` |
| `src/onemancompany/core/config.py` | Modify:770-774 | `save_workflow()` calls parse + validate before writing |
| `src/onemancompany/api/routes.py` | Modify:1226-1243 | Catch `WorkflowValidationError`, return 422 |
| `src/onemancompany/agents/coo_agent.py` | Modify:708-709 | Catch `WorkflowValidationError`, return error to agent |
| `tests/unit/core/test_workflow_engine.py` | Modify | Add tests for new fields, parsing, validation |
| `tests/unit/core/test_config.py` | Modify:322-331 | Update `save_workflow` tests for validation |
| `tests/unit/api/test_routes.py` | Modify:~1013 | Add test for 422 response |
| `company/human_resource/workflows/hiring_workflow.md` | Modify | Add `**Goal**` to each phase |
| `company/human_resource/workflows/project_retrospective_workflow.md` | Modify | Add `**Goal**` to each phase |
| `company/human_resource/workflows/project_intake_workflow.md` | Rewrite | Convert to standard `##` format with metadata + Goal |
| `company/human_resource/workflows/onboarding_workflow.md` | Rewrite | Add metadata, rename `**Owner**` to `**Responsible**`, add Goal |
| `company/human_resource/workflows/offboarding_workflow.md` | Rewrite | Same as onboarding |

---

### Task 1: Add `goal` and `depends_on` to WorkflowStep + parse them

**Files:**
- Modify: `src/onemancompany/core/workflow_engine.py:37-47` (dataclass), `src/onemancompany/core/workflow_engine.py:110-166` (parser)
- Test: `tests/unit/core/test_workflow_engine.py`

- [ ] **Step 1: Write failing tests for `goal` field parsing**

Add to `tests/unit/core/test_workflow_engine.py`:

```python
# Add to top-level sample markdowns:

WORKFLOW_WITH_GOAL_MD = """\
# Goal Workflow

- **Flow ID**: goal_test
- **Owner**: HR

## Phase 1: Prepare

- **Goal**: Ensure meeting room is booked and all participants notified
- **Responsible**: HR
- **Steps**:
  1. Book room
  2. Notify team
- **Output**: Room booked

## Phase 2: Execute

- **Goal**: Complete all action items from the meeting
- **Responsible**: COO
- **Depends on**: Phase 1
- **Steps**:
  1. Review action items
  2. Execute each item
- **Output**: Execution report
"""


class TestGoalAndDependsParsing:
    def test_goal_parsed(self):
        wf = parse_workflow("goal_test", WORKFLOW_WITH_GOAL_MD)
        assert wf.steps[0].goal == "Ensure meeting room is booked and all participants notified"
        assert wf.steps[1].goal == "Complete all action items from the meeting"

    def test_depends_on_parsed(self):
        wf = parse_workflow("goal_test", WORKFLOW_WITH_GOAL_MD)
        assert wf.steps[0].depends_on == []
        assert wf.steps[1].depends_on == [0]  # Phase 1 = index 0

    def test_goal_missing_defaults_empty(self):
        md = "## Step\n\n- **Responsible**: HR\n- **Steps**:\n  1. Do it\n"
        wf = parse_workflow("no_goal", md)
        assert wf.steps[0].goal == ""

    def test_depends_on_missing_defaults_empty(self):
        md = "## Step\n\n- **Goal**: Do stuff\n- **Responsible**: HR\n"
        wf = parse_workflow("no_deps", md)
        assert wf.steps[0].depends_on == []

    def test_depends_on_multiple_phases(self):
        md = (
            "# Multi Dep\n\n- **Flow ID**: multi\n- **Owner**: HR\n\n"
            "## Phase 1: A\n\n- **Goal**: First\n- **Responsible**: HR\n\n"
            "## Phase 2: B\n\n- **Goal**: Second\n- **Responsible**: COO\n\n"
            "## Phase 3: C\n\n- **Goal**: Third\n- **Responsible**: HR\n"
            "- **Depends on**: Phase 1, Phase 2\n"
        )
        wf = parse_workflow("multi", md)
        assert wf.steps[2].depends_on == [0, 1]

    def test_depends_on_with_half_phases(self):
        """Phases like 4.5 should parse correctly."""
        md = (
            "# Half\n\n- **Flow ID**: half\n- **Owner**: HR\n\n"
            "## Phase 1: A\n\n- **Goal**: First\n- **Responsible**: HR\n\n"
            "## Phase 1.5: B\n\n- **Goal**: Second\n- **Responsible**: HR\n\n"
            "## Phase 2: C\n\n- **Goal**: Third\n- **Responsible**: HR\n"
            "- **Depends on**: Phase 1.5\n"
        )
        wf = parse_workflow("half", md)
        assert wf.steps[2].depends_on == [1]  # Phase 1.5 = index 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/core/test_workflow_engine.py::TestGoalAndDependsParsing -v`
Expected: FAIL — `WorkflowStep` has no `goal` or `depends_on` attributes.

- [ ] **Step 3: Add fields to WorkflowStep dataclass**

In `src/onemancompany/core/workflow_engine.py`, update the `WorkflowStep` dataclass (lines 37-47):

```python
@dataclass
class WorkflowStep:
    """A single stage/phase parsed from a workflow markdown document."""

    index: int  # 0-based position in the workflow
    title: str  # e.g., "Phase 1: Review Preparation"
    owner: str  # e.g., "HR", "COO", "Each participating employee", "COO + HR"
    instructions: list[str]  # numbered sub-steps
    output_description: str  # what this step produces
    raw_text: str  # full markdown text of this section
    collaborators: str = ""  # optional collaborators at step level
    goal: str = ""  # what this step must achieve (required by validation)
    depends_on: list[int] = field(default_factory=list)  # indices of prerequisite phases
```

- [ ] **Step 4: Add parsing for `**Goal**` and `**Depends on**` in `_parse_step_section()`**

In `src/onemancompany/core/workflow_engine.py`, update `_parse_step_section()` (lines 110-166). Add after the collaborators extraction (line 129) and before the instructions loop (line 132):

```python
    # Extract goal
    goal = ""
    goal_match = re.search(r"\*\*Goal\*\*:\s*(.+)", section_text)
    if goal_match:
        goal = goal_match.group(1).strip()

    # Extract depends_on — "Phase 1", "Phase 2, Phase 3", "Phase 1.5"
    depends_on: list[int] = []
    deps_match = re.search(r"\*\*Depends on\*\*:\s*(.+)", section_text)
    if deps_match:
        deps_text = deps_match.group(1).strip()
        # Extract phase numbers (int or float like 1.5) and map to 0-based indices
        # Build a phase-number-to-index mapping from all steps parsed so far
        # NOTE: We can't resolve indices here — we only have the raw text.
        # Store raw phase references and resolve later in parse_workflow().
        phase_refs = re.findall(r"Phase\s+([\d.]+)", deps_text, re.IGNORECASE)
        depends_on = phase_refs  # Store as raw strings, resolve in parse_workflow()
```

**Wait — `_parse_step_section` doesn't know about other steps' indices.** We need a two-pass approach:

1. `_parse_step_section()` stores raw depends_on references as strings (e.g., `["1", "1.5"]`)
2. `parse_workflow()` resolves them to 0-based indices after all steps are parsed

Update the dataclass to use a helper field:

Actually, simpler: `_parse_step_section` returns raw strings in a temporary field. `parse_workflow()` builds a title→index map and resolves. Let's use a cleaner approach:

Add a module-level helper `_resolve_depends_on()` called from `parse_workflow()`:

```python
def _resolve_depends_on(steps: list[WorkflowStep]) -> None:
    """Resolve raw phase references in depends_on to 0-based step indices.

    Each step's depends_on is initially a list of phase number strings
    (e.g., ["1", "2.5"]) parsed from '**Depends on**: Phase 1, Phase 2.5'.
    This function builds a phase-number→index map from step titles and
    replaces the raw strings with integer indices.
    """
    # Build phase number → step index mapping from titles
    # Title format: "Phase N: ..." or "Phase N.N: ..."
    phase_map: dict[str, int] = {}
    for step in steps:
        m = re.match(r"Phase\s+([\d.]+)", step.title)
        if m:
            phase_map[m.group(1)] = step.index

    for step in steps:
        if step.depends_on:
            resolved = []
            for ref in step.depends_on:
                if ref in phase_map:
                    resolved.append(phase_map[ref])
            step.depends_on = resolved
```

In `_parse_step_section()`, store raw phase number strings in `depends_on`:

```python
    # Extract depends_on as raw phase number strings (resolved later)
    raw_depends_on: list[str] = []
    deps_match = re.search(r"\*\*Depends on\*\*:\s*(.+)", section_text)
    if deps_match:
        raw_depends_on = re.findall(r"Phase\s+([\d.]+)", deps_match.group(1), re.IGNORECASE)
```

And pass them to the WorkflowStep constructor. Since `depends_on` is typed `list[int]` but temporarily holds strings, we type it as `list` in the dataclass and document the two-phase resolution. OR, simpler: type it as `list[int]` and store strings in a separate temporary variable, resolving in `parse_workflow()`.

**Cleanest approach:** Store raw refs in a temporary attribute `_raw_depends_on: list[str]` and have `parse_workflow()` call `_resolve_depends_on()` which sets the real `depends_on: list[int]`.

Final implementation in `_parse_step_section()`:

```python
    # Extract depends_on — raw phase references, resolved by parse_workflow()
    _raw_depends_on: list[str] = []
    deps_match = re.search(r"\*\*Depends on\*\*:\s*(.+)", section_text)
    if deps_match:
        _raw_depends_on = re.findall(r"Phase\s+([\d.]+)", deps_match.group(1), re.IGNORECASE)

    step = WorkflowStep(
        index=index,
        title=title,
        owner=owner,
        instructions=instructions,
        output_description=output_desc,
        raw_text=full_text,
        collaborators=collaborators,
        goal=goal,
    )
    step._raw_depends_on = _raw_depends_on  # temporary, resolved by parse_workflow
    return step
```

In `parse_workflow()`, after all steps are collected (after the for loop, before return):

```python
    # Resolve depends_on references from phase numbers to step indices
    _resolve_depends_on(wf.steps)

    return wf
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/core/test_workflow_engine.py::TestGoalAndDependsParsing -v`
Expected: All PASS.

- [ ] **Step 6: Run full test suite**

Run: `.venv/bin/python -m pytest tests/unit/core/test_workflow_engine.py -v`
Expected: All existing + new tests PASS.

- [ ] **Step 7: Commit**

```bash
git add src/onemancompany/core/workflow_engine.py tests/unit/core/test_workflow_engine.py
git commit -m "feat: add goal and depends_on fields to WorkflowStep with parsing"
```

---

### Task 2: Add `validate_workflow()` and `WorkflowValidationError`

**Files:**
- Modify: `src/onemancompany/core/workflow_engine.py`
- Test: `tests/unit/core/test_workflow_engine.py`

- [ ] **Step 1: Write failing tests for validation**

Add to `tests/unit/core/test_workflow_engine.py`:

```python
from onemancompany.core.workflow_engine import (
    WorkflowDefinition,
    WorkflowStep,
    WorkflowValidationError,
    classify_step_owner,
    parse_workflow,
    validate_workflow,
    _parse_step_section,
)


class TestValidateWorkflow:
    def _make_wf(self, **overrides) -> WorkflowDefinition:
        """Helper to create a valid WorkflowDefinition with overrides."""
        defaults = dict(
            name="test", flow_id="test_flow", owner="HR",
            collaborators="", trigger="manual", raw_text="",
        )
        defaults.update(overrides)
        return WorkflowDefinition(**defaults)

    def _make_step(self, **overrides) -> WorkflowStep:
        """Helper to create a valid WorkflowStep with overrides."""
        defaults = dict(
            index=0, title="Phase 1: Test", owner="HR",
            instructions=["Do it"], output_description="Done",
            raw_text="## Phase 1: Test", goal="Achieve something",
        )
        defaults.update(overrides)
        return WorkflowStep(**defaults)

    def test_valid_workflow_no_errors(self):
        wf = self._make_wf(steps=[self._make_step()])
        errors = validate_workflow(wf)
        assert errors == []

    def test_missing_flow_id(self):
        wf = self._make_wf(flow_id="", steps=[self._make_step()])
        errors = validate_workflow(wf)
        assert any("flow_id" in e.lower() or "Flow ID" in e for e in errors)

    def test_missing_workflow_owner(self):
        wf = self._make_wf(owner="", steps=[self._make_step()])
        errors = validate_workflow(wf)
        assert any("owner" in e.lower() for e in errors)

    def test_step_missing_goal(self):
        step = self._make_step(goal="")
        wf = self._make_wf(steps=[step])
        errors = validate_workflow(wf)
        assert any("goal" in e.lower() for e in errors)

    def test_step_missing_responsible(self):
        step = self._make_step(owner="")
        wf = self._make_wf(steps=[step])
        errors = validate_workflow(wf)
        assert any("responsible" in e.lower() or "owner" in e.lower() for e in errors)

    def test_depends_on_self_reference(self):
        step = self._make_step(index=0, depends_on=[0])
        wf = self._make_wf(steps=[step])
        errors = validate_workflow(wf)
        assert any("self" in e.lower() or "itself" in e.lower() for e in errors)

    def test_depends_on_out_of_bounds(self):
        step = self._make_step(index=0, depends_on=[5])
        wf = self._make_wf(steps=[step])
        errors = validate_workflow(wf)
        assert any("out of bounds" in e.lower() or "does not exist" in e.lower() for e in errors)

    def test_depends_on_valid_reference(self):
        step0 = self._make_step(index=0, title="Phase 1: A")
        step1 = self._make_step(index=1, title="Phase 2: B", depends_on=[0])
        wf = self._make_wf(steps=[step0, step1])
        errors = validate_workflow(wf)
        assert errors == []

    def test_multiple_errors_collected(self):
        step = self._make_step(goal="", owner="")
        wf = self._make_wf(flow_id="", steps=[step])
        errors = validate_workflow(wf)
        assert len(errors) >= 3  # flow_id + goal + owner

    def test_error_class_has_errors_list(self):
        err = WorkflowValidationError(["error1", "error2"])
        assert err.errors == ["error1", "error2"]
        assert "error1" in str(err)
        assert "error2" in str(err)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/core/test_workflow_engine.py::TestValidateWorkflow -v`
Expected: FAIL — `WorkflowValidationError` and `validate_workflow` not defined.

- [ ] **Step 3: Implement `WorkflowValidationError` and `validate_workflow()`**

Add to `src/onemancompany/core/workflow_engine.py` after the dataclass definitions (after line 60):

```python
class WorkflowValidationError(Exception):
    """Raised when a workflow document fails schema validation."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__(f"Workflow validation failed: {'; '.join(errors)}")


def validate_workflow(wf: WorkflowDefinition) -> list[str]:
    """Validate a parsed workflow against the schema.

    Returns a list of error strings. Empty list means valid.
    """
    errors: list[str] = []

    # Workflow-level checks
    if not wf.flow_id.strip():
        errors.append("Workflow missing required field: Flow ID")
    if not wf.owner.strip():
        errors.append("Workflow missing required field: Owner")

    # Step-level checks
    valid_indices = {s.index for s in wf.steps}
    for step in wf.steps:
        prefix = f"Step '{step.title}'"
        if not step.goal.strip():
            errors.append(f"{prefix}: missing required field Goal")
        if not step.owner.strip():
            errors.append(f"{prefix}: missing required field Responsible")
        for dep in step.depends_on:
            if dep == step.index:
                errors.append(f"{prefix}: depends_on references itself (index {dep})")
            elif dep not in valid_indices:
                errors.append(
                    f"{prefix}: depends_on index {dep} does not exist "
                    f"(valid: {sorted(valid_indices)})"
                )

    return errors
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/core/test_workflow_engine.py::TestValidateWorkflow -v`
Expected: All PASS.

- [ ] **Step 5: Run full workflow_engine test suite**

Run: `.venv/bin/python -m pytest tests/unit/core/test_workflow_engine.py -v`
Expected: All PASS.

- [ ] **Step 6: Commit**

```bash
git add src/onemancompany/core/workflow_engine.py tests/unit/core/test_workflow_engine.py
git commit -m "feat: add validate_workflow() with WorkflowValidationError"
```

---

### Task 3: Add `WORKFLOW_SCHEMA_DOC` constant

**Files:**
- Modify: `src/onemancompany/core/workflow_engine.py`
- Test: `tests/unit/core/test_workflow_engine.py`

- [ ] **Step 1: Write failing test**

```python
from onemancompany.core.workflow_engine import WORKFLOW_SCHEMA_DOC


class TestWorkflowSchemaDoc:
    def test_schema_doc_exists_and_nonempty(self):
        assert isinstance(WORKFLOW_SCHEMA_DOC, str)
        assert len(WORKFLOW_SCHEMA_DOC) > 100

    def test_schema_doc_mentions_required_fields(self):
        assert "**Goal**" in WORKFLOW_SCHEMA_DOC
        assert "**Responsible**" in WORKFLOW_SCHEMA_DOC
        assert "**Flow ID**" in WORKFLOW_SCHEMA_DOC

    def test_schema_doc_mentions_optional_fields(self):
        assert "**Depends on**" in WORKFLOW_SCHEMA_DOC

    def test_schema_doc_has_example(self):
        assert "## Phase" in WORKFLOW_SCHEMA_DOC
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/core/test_workflow_engine.py::TestWorkflowSchemaDoc -v`
Expected: FAIL — `WORKFLOW_SCHEMA_DOC` not defined.

- [ ] **Step 3: Implement the constant**

Add to `src/onemancompany/core/workflow_engine.py` after the imports:

```python
WORKFLOW_SCHEMA_DOC = """\
# Workflow Format Specification

All workflow documents must follow this standard format.

## Header (before first ## heading)

```markdown
# Workflow Title

- **Flow ID**: unique_identifier (REQUIRED)
- **Owner**: Primary responsible role (REQUIRED)
- **Collaborators**: Additional collaborators
- **Trigger**: When/how this workflow is initiated
```

## Each Phase (## heading)

```markdown
## Phase N: Title

- **Goal**: What this phase must achieve (REQUIRED)
- **Responsible**: Who executes this phase (REQUIRED)
- **Depends on**: Phase 1, Phase 3 (optional, comma-separated)
- **Collaborators**: Additional participants (optional)
- **Steps**:
  1. Concrete action
  2. Another action
- **Output**: What this phase produces (optional)
```

### Field Rules

- **Goal** and **Responsible** are required for every phase. Workflow will be rejected without them.
- **Depends on** references other phases by number (e.g., "Phase 1", "Phase 2.5"). If omitted, phases execute sequentially.
- **Flow ID** and **Owner** are required in the header.

### Example

```markdown
# Hiring Workflow

- **Flow ID**: hiring
- **Owner**: HR
- **Collaborators**: COO
- **Trigger**: COO requests when project is understaffed

---

## Phase 1: Requirements Confirmation

- **Goal**: Establish clear job requirements and hiring count
- **Responsible**: HR + COO
- **Steps**:
  1. Confirm positions and headcount
  2. Define job responsibilities and skills
- **Output**: Hiring requirements document

## Phase 2: Candidate Search

- **Goal**: Produce 3-5 qualified candidate profiles
- **Responsible**: HR
- **Depends on**: Phase 1
- **Steps**:
  1. Search via recruitment channels
  2. Filter by requirements
- **Output**: Candidate shortlist
```
"""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/core/test_workflow_engine.py::TestWorkflowSchemaDoc -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add src/onemancompany/core/workflow_engine.py tests/unit/core/test_workflow_engine.py
git commit -m "feat: add WORKFLOW_SCHEMA_DOC constant for progressive prompt injection"
```

---

### Task 4: Update `save_workflow()` to validate before writing

**Files:**
- Modify: `src/onemancompany/core/config.py:770-774`
- Test: `tests/unit/core/test_config.py:322-331`

- [ ] **Step 1: Write failing tests**

Add to `tests/unit/core/test_config.py` in the `TestWorkflows` class:

```python
    def test_save_rejects_invalid_workflow(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod
        from onemancompany.core.workflow_engine import WorkflowValidationError

        wf_dir = tmp_path / "workflows"
        monkeypatch.setattr(config_mod, "WORKFLOWS_DIR", wf_dir)

        # Missing Flow ID, Owner, Goal, Responsible — should fail validation
        bad_content = "## Step 1: Do Something\n\n- **Steps**:\n  1. Action\n"

        with pytest.raises(WorkflowValidationError) as exc_info:
            config_mod.save_workflow("bad_flow", bad_content)

        assert len(exc_info.value.errors) > 0
        assert not (wf_dir / "bad_flow.md").exists()  # not written to disk

    def test_save_accepts_valid_workflow(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod

        wf_dir = tmp_path / "workflows"
        monkeypatch.setattr(config_mod, "WORKFLOWS_DIR", wf_dir)

        valid_content = (
            "# Test Workflow\n\n"
            "- **Flow ID**: test\n"
            "- **Owner**: HR\n\n"
            "## Phase 1: Do It\n\n"
            "- **Goal**: Achieve something\n"
            "- **Responsible**: HR\n"
            "- **Steps**:\n"
            "  1. Action one\n"
            "- **Output**: Done\n"
        )

        config_mod.save_workflow("valid_flow", valid_content)
        assert (wf_dir / "valid_flow.md").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/core/test_config.py::TestWorkflows::test_save_rejects_invalid_workflow tests/unit/core/test_config.py::TestWorkflows::test_save_accepts_valid_workflow -v`
Expected: `test_save_rejects_invalid_workflow` FAIL (no exception raised), `test_save_accepts_valid_workflow` may pass or fail depending on existing test fixture.

- [ ] **Step 3: Update `save_workflow()` in config.py**

Replace `save_workflow()` at lines 770-774:

```python
def save_workflow(name: str, content: str) -> None:
    """Save a workflow .md file to business/workflows/ after validation.

    Raises WorkflowValidationError if the content does not pass schema validation.
    """
    from onemancompany.core.workflow_engine import (
        WorkflowValidationError,
        parse_workflow,
        validate_workflow,
    )

    wf = parse_workflow(name, content)
    errors = validate_workflow(wf)
    if errors:
        raise WorkflowValidationError(errors)

    WORKFLOWS_DIR.mkdir(parents=True, exist_ok=True)
    path = WORKFLOWS_DIR / f"{name}.md"
    path.write_text(content, encoding=ENCODING_UTF8)
```

- [ ] **Step 4: Update existing `test_save_creates_dir` test**

The existing test at line 322 uses content without Goal/Responsible. Update it to use valid content:

```python
    def test_save_creates_dir(self, tmp_path, monkeypatch):
        import onemancompany.core.config as config_mod

        wf_dir = tmp_path / "workflows"
        monkeypatch.setattr(config_mod, "WORKFLOWS_DIR", wf_dir)

        config_mod.save_workflow("new_flow", (
            "# New Flow\n\n"
            "- **Flow ID**: new_flow\n"
            "- **Owner**: HR\n\n"
            "## Phase 1: Start\n\n"
            "- **Goal**: Begin the process\n"
            "- **Responsible**: HR\n"
        ))

        assert (wf_dir / "new_flow.md").exists()
        assert "New Flow" in (wf_dir / "new_flow.md").read_text()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/core/test_config.py::TestWorkflows -v`
Expected: All PASS.

- [ ] **Step 6: Commit**

```bash
git add src/onemancompany/core/config.py tests/unit/core/test_config.py
git commit -m "feat: save_workflow() validates before writing, rejects invalid content"
```

---

### Task 5: Update API endpoint and COO agent to handle validation errors

**Files:**
- Modify: `src/onemancompany/api/routes.py:1226-1243`
- Modify: `src/onemancompany/agents/coo_agent.py:708-709`
- Test: `tests/unit/api/test_routes.py`
- Test: `tests/unit/agents/test_coo_agent.py`

- [ ] **Step 1: Write failing test for API 422 response**

Add to `tests/unit/api/test_routes.py` (find the existing workflow test area around line 1013):

```python
class TestUpdateWorkflowValidation:
    async def test_invalid_workflow_returns_422(self, client):
        """PUT /api/workflows/{name} returns 422 for invalid workflow content."""
        bad_content = "## Step 1: No Goal\n\n- **Steps**:\n  1. Action\n"
        resp = await client.put(
            "/api/workflows/bad_wf",
            json={"content": bad_content},
        )
        assert resp.status_code == 422
        data = resp.json()
        assert "errors" in data
        assert len(data["errors"]) > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/api/test_routes.py::TestUpdateWorkflowValidation -v`
Expected: FAIL — endpoint returns 200 instead of 422.

- [ ] **Step 3: Update API endpoint**

In `src/onemancompany/api/routes.py`, update the `update_workflow` function (lines 1226-1243):

```python
@router.put("/api/workflows/{name}")
async def update_workflow(name: str, body: dict) -> dict:
    """Update (or create) a workflow document. CEO edits the company rules."""
    from onemancompany.core.config import save_workflow
    from onemancompany.core.workflow_engine import WorkflowValidationError

    content = body.get("content", "")
    if not content:
        return {"error": "Missing content"}

    try:
        save_workflow(name, content)
    except WorkflowValidationError as exc:
        from starlette.responses import JSONResponse
        return JSONResponse(
            status_code=422,
            content={"error": "Workflow validation failed", "errors": exc.errors},
        )

    await event_bus.publish(
        CompanyEvent(
            type=EventType.WORKFLOW_UPDATED,
            payload={"name": name},
            agent="CEO",
        )
    )
    return {"status": "saved", "name": name}
```

- [ ] **Step 4: Update COO agent's save_org_item**

In `src/onemancompany/agents/coo_agent.py`, update line 708-709:

```python
    if category == OrgDir.WORKFLOW:
        try:
            save_workflow(name, content)
        except Exception as exc:
            return {
                "status": "error",
                "message": f"Workflow validation failed: {exc}",
            }
        path = str(WORKFLOWS_DIR / f"{name}.md")
```

- [ ] **Step 5: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/api/test_routes.py::TestUpdateWorkflowValidation tests/unit/agents/test_coo_agent.py -v`
Expected: All PASS.

- [ ] **Step 6: Commit**

```bash
git add src/onemancompany/api/routes.py src/onemancompany/agents/coo_agent.py tests/unit/api/test_routes.py tests/unit/agents/test_coo_agent.py
git commit -m "feat: API returns 422 and COO gets error on invalid workflow"
```

---

### Task 6: Register WORKFLOW_SCHEMA_DOC as SOP for progressive injection

**Files:**
- Modify: `src/onemancompany/core/vessel.py:1948-1969` (SOP injection section)
- Or: Create a new SOP file at the appropriate location

- [ ] **Step 1: Determine injection approach**

The existing SOP injection in `vessel.py:1948-1969` already loads all `.md` files from `WORKFLOWS_DIR`, `SOP_DIR`, and `HR_SOP_DIR` via `load_workflows()`, and injects them as a one-line catalog entry with `read()` path. The `WORKFLOW_SCHEMA_DOC` is a constant, not a file.

Two options:
1. Write `WORKFLOW_SCHEMA_DOC` to a file in `SOP_DIR` on startup
2. Save it as a real `.md` file in the repo under `company/operations/sops/`

**Option 2 is simpler and follows existing patterns.** Create `workflow_schema.md` in the source `company/operations/sops/` directory. It gets copied to `.onemancompany/` on init like other company files. Then `load_workflows()` picks it up automatically, and every employee sees it as a one-line SOP catalog entry they can `read()`.

- [ ] **Step 2: Create the SOP file**

Create `company/operations/sops/workflow_schema.md` with the content of `WORKFLOW_SCHEMA_DOC`. This file becomes the single source of truth. The constant in `workflow_engine.py` can reference this file or be kept as a fallback.

Actually — to avoid duplication, keep `WORKFLOW_SCHEMA_DOC` in `workflow_engine.py` as the single source and generate the SOP file from it on startup. But that adds complexity.

**Simplest approach:** Just create the SOP file manually. The `WORKFLOW_SCHEMA_DOC` constant is still useful for validation error messages (can include "see workflow_schema SOP for format details"). The SOP file is the employee-facing doc; the constant is the developer-facing reference.

Create file: `company/operations/sops/workflow_schema.md`

Content: Same as `WORKFLOW_SCHEMA_DOC` from Task 3.

- [ ] **Step 3: Verify it appears in SOP catalog**

Run: `.venv/bin/python -c "from onemancompany.core.config import load_workflows; wfs = load_workflows(); print('workflow_schema' in wfs, list(wfs.keys())[:5])"`
Expected: `True` and the name appears in the list.

- [ ] **Step 4: Commit**

```bash
git add company/operations/sops/workflow_schema.md
git commit -m "feat: add workflow_schema SOP for progressive prompt injection"
```

---

### Task 7: Migrate existing workflow files to standard format

**Files:**
- Modify: `company/human_resource/workflows/hiring_workflow.md`
- Modify: `company/human_resource/workflows/project_retrospective_workflow.md`
- Rewrite: `company/human_resource/workflows/project_intake_workflow.md`
- Rewrite: `company/human_resource/workflows/onboarding_workflow.md`
- Rewrite: `company/human_resource/workflows/offboarding_workflow.md`

- [ ] **Step 1: Add `**Goal**` to hiring_workflow.md**

Each of the 5 phases needs a `**Goal**` line added after the `##` heading, before `**Responsible**`:

```markdown
## Phase 1: Requirements Confirmation

- **Goal**: Establish clear job requirements, headcount, and timeline
- **Responsible**: HR + COO
...

## Phase 2: Candidate Generation

- **Goal**: Produce a shortlist of qualified candidates matching requirements
- **Responsible**: HR
- **Depends on**: Phase 1
...

## Phase 3: Interview and Assessment

- **Goal**: Evaluate candidates and finalize the hire list
- **Responsible**: HR + senior employees from the hiring department
- **Depends on**: Phase 2
...

## Phase 4: Onboarding Process

- **Goal**: Create employee records and assign initial resources
- **Responsible**: HR
- **Depends on**: Phase 3
...

## Phase 5: Onboarding Training

- **Goal**: New employee is oriented and ready to contribute
- **Responsible**: HR + COO
- **Depends on**: Phase 4
...
```

- [ ] **Step 2: Add `**Goal**` to project_retrospective_workflow.md**

Add `**Goal**` to each of the 11 phases (1, 2, 3, 4, 4.5, 5, 5.5, 6, 7, 8, 9). Examples:

- Phase 1: "Secure a meeting room and ensure all participants are notified"
- Phase 2: "Each employee honestly assesses their own performance"
- Phase 3: "Senior employees provide objective evaluations of junior work"
- Phase 4: "Consolidate feedback into actionable improvement suggestions per employee"
- Phase 4.5: "Persist improvement feedback into employee records for long-term growth"
- Phase 5: "Produce operations status and cost analysis report"
- Phase 5.5: "Identify and preserve valuable project deliverables as company assets"
- Phase 6: "Give all employees a voice to raise concerns and suggestions"
- Phase 7: "Organize actionable plans with clear owners and priorities"
- Phase 8: "EA reviews and approves action plans for execution"
- Phase 9: "Standardize process improvements discovered during retrospective"

- [ ] **Step 3: Rewrite project_intake_workflow.md**

Convert from plain numbered list to standard format:

```markdown
# Project Intake Workflow

- **Flow ID**: project_intake
- **Owner**: EA
- **Collaborators**: CEO, COO, HR
- **Trigger**: CEO submits a new project request

---

## Phase 1: CEO Request

- **Goal**: Capture the CEO's project request with sufficient context
- **Responsible**: CEO
- **Steps**:
  1. CEO submits a new project request
- **Output**: Raw project request

## Phase 2: EA Review

- **Goal**: Clarify requirements and resolve ambiguities before planning
- **Responsible**: EA
- **Depends on**: Phase 1
- **Steps**:
  1. Review the request and clarify requirements if necessary
- **Output**: Clarified project requirements

## Phase 3: Task Translation and Breakdown

- **Goal**: Transform the directive into a structured, actionable task template
- **Responsible**: EA
- **Depends on**: Phase 2
- **Steps**:
  1. Translate the macro directive into a standard "One-Page Task Template"
  2. Break down the task into actionable components
  3. Define measurable acceptance criteria (2-5 points)
  4. Set estimated budget based on complexity ($0.01 / $0.05 / $0.15+)
- **Output**: One-Page Task Template with acceptance criteria and budget

## Phase 4: Workspace Preparation

- **Goal**: Initialize project infrastructure for tracking and version control
- **Responsible**: EA
- **Depends on**: Phase 3
- **Steps**:
  1. Initialize the project workspace using the Version Control and Asset Manager
  2. Set up the changelog.md to track phase deliverables and changes
- **Output**: Initialized project workspace with changelog

## Phase 5: Routing and Dispatch

- **Goal**: Assign tasks to the right people for execution
- **Responsible**: EA
- **Depends on**: Phase 4
- **Steps**:
  1. Dispatch tasks to the appropriate responsible officer (COO/HR) or specific employees
  2. For multi-domain tasks, split and dispatch each part separately
  3. Fast Track: Simple single-tool CEO-facing tasks are executed directly by the EA
- **Output**: Dispatched task assignments

## Phase 6: Execution

- **Goal**: Complete all assigned tasks within the project workspace
- **Responsible**: Assigned officers and employees
- **Depends on**: Phase 5
- **Steps**:
  1. Responsible officer coordinates execution within the assigned workspace
- **Output**: Completed deliverables

## Phase 7: Acceptance and Review

- **Goal**: Verify deliverables meet acceptance criteria
- **Responsible**: EA
- **Depends on**: Phase 6
- **Steps**:
  1. Responsible officer reviews against acceptance criteria (accept_project)
  2. EA performs final quality gate review (ea_review_project)
- **Output**: Acceptance decision
```

- [ ] **Step 4: Rewrite onboarding_workflow.md**

```markdown
# Onboarding Workflow

- **Flow ID**: onboarding
- **Owner**: HR
- **Collaborators**: COO, all employees
- **Trigger**: New employee has been hired and records created

---

## Phase 1: Welcome and Introduction

- **Goal**: New employee understands the company mission, culture, and values
- **Responsible**: HR
- **Steps**:
  1. Welcome the new employee to One Man Company
  2. Introduce the company's mission, culture, and values
- **Output**: Welcome briefing completed

## Phase 2: Meet the Team

- **Goal**: New employee and existing team know each other
- **Responsible**: All employees
- **Depends on**: Phase 1
- **Steps**:
  1. Each team member introduces themselves
  2. New employee shares their background and goals
- **Output**: Team introductions completed

## Phase 3: Tools and Environment Orientation

- **Goal**: New employee can use all company tools and navigate the workspace
- **Responsible**: COO
- **Depends on**: Phase 1
- **Steps**:
  1. Walk through the company's tools and digital workspace
- **Output**: Tools orientation completed

## Phase 4: Work Principles and Expectations

- **Goal**: New employee understands performance expectations and review process
- **Responsible**: HR
- **Depends on**: Phase 2, Phase 3
- **Steps**:
  1. Discuss performance expectations and review process
  2. Explain probation period milestones
- **Output**: Expectations briefing completed
```

- [ ] **Step 5: Rewrite offboarding_workflow.md**

```markdown
# Offboarding Workflow

- **Flow ID**: offboarding
- **Owner**: HR
- **Collaborators**: COO, departing employee
- **Trigger**: Employee departure confirmed

---

## Phase 1: Exit Interview

- **Goal**: Understand departure reasons and gather feedback for improvement
- **Responsible**: HR
- **Steps**:
  1. Conduct a private exit interview with the departing employee
- **Output**: Exit interview notes

## Phase 2: Knowledge Transfer

- **Goal**: Ensure no critical knowledge is lost with the departing employee
- **Responsible**: Departing employee
- **Depends on**: Phase 1
- **Steps**:
  1. Summarize key responsibilities and ongoing work
- **Output**: Knowledge transfer document

## Phase 3: Asset Recovery

- **Goal**: Reclaim all company resources and revoke access
- **Responsible**: COO
- **Depends on**: Phase 2
- **Steps**:
  1. Collect company equipment
  2. Revoke system access
- **Output**: Asset recovery confirmation

## Phase 4: Final Processing

- **Goal**: Complete all administrative offboarding tasks
- **Responsible**: HR
- **Depends on**: Phase 3
- **Steps**:
  1. Complete termination paperwork
  2. Move records to ex-employees archive
- **Output**: Offboarding completed
```

- [ ] **Step 6: Validate all migrated files pass**

Run a quick script to parse and validate each file:

```bash
.venv/bin/python -c "
from onemancompany.core.workflow_engine import parse_workflow, validate_workflow
from pathlib import Path

wf_dir = Path('company/human_resource/workflows')
for f in sorted(wf_dir.glob('*.md')):
    content = f.read_text()
    wf = parse_workflow(f.stem, content)
    errors = validate_workflow(wf)
    status = 'PASS' if not errors else f'FAIL: {errors}'
    print(f'{f.name}: {status}')
"
```

Expected: All 5 files show PASS.

- [ ] **Step 7: Commit**

```bash
git add company/human_resource/workflows/
git commit -m "refactor: migrate all 5 workflow files to standard schema format"
```

---

### Task 8: Full test suite + compilation check

**Files:** None (verification only)

- [ ] **Step 1: Run full unit test suite**

Run: `.venv/bin/python -m pytest tests/unit/ -x -q`
Expected: All tests pass.

- [ ] **Step 2: Verify compilation**

Run: `.venv/bin/python -c "from onemancompany.core.workflow_engine import parse_workflow, validate_workflow, WorkflowValidationError, WORKFLOW_SCHEMA_DOC; print('OK')"`
Expected: `OK`

Run: `.venv/bin/python -c "from onemancompany.api.routes import router; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Check for code smells**

Run: `.venv/bin/python -m pytest tests/unit/ -q --tb=no` to ensure no regressions.

Verify: no `except Exception: pass`, no `print()` statements added.
