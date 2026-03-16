## Background

<!-- WHY does this PR exist? Link the issue, bug report, or product requirement.
     Describe the problem or user need — not the solution. -->

- **Issue/Motivation**:
- **Root Cause** (if bug fix):

## What Changed

<!-- WHAT did you implement? Describe the solution at an architectural level.
     Focus on design decisions, not line-by-line diffs. -->

### Key Design Decisions

<!-- Why did you choose this approach over alternatives?
     What trade-offs did you make? -->

### Files Changed (annotated)

<!-- List key files with a one-line explanation of each change.
     Reviewers will read these first. -->

| File | Change |
|------|--------|
| `path/to/file.py` | Description of change |

## Type

- [ ] Bug fix
- [ ] New feature
- [ ] Refactor
- [ ] Visualization / Theme
- [ ] Talent package
- [ ] Documentation
- [ ] Other

## Review Checklist

<!-- Complete BEFORE requesting review. Check every box or explain why N/A. -->

### Phase 1 — Bug Hunt

- [ ] No off-by-one, null-ref, or race conditions introduced
- [ ] Error paths tested (what happens when it fails?)
- [ ] Edge cases covered (empty input, duplicates, concurrent access)

### Phase 2 — Design Principles ([docs/design-principles.md](docs/design-principles.md))

- [ ] **Single Source of Truth** — No duplicated state; data has one owner, one write function
- [ ] **Systematic Design** — Structural fix, not a patch (would a second similar request need the same code touched?)
- [ ] **Modular & General-Purpose** — New capability addable without modifying existing code
- [ ] **Complete Data Package** — New state is serializable, recoverable, registered, terminable
- [ ] **No Silent Exceptions** — No `except: pass`; errors logged, `CancelledError` re-raised
- [ ] **Registry/Dispatch** — No if-elif chains for type dispatch
- [ ] **Status via transition()** — No direct `node.status = ...` assignment
- [ ] **Minimal Complexity** — No premature abstractions; minimum code for current requirements

### Phase 3 — Side Effects

- [ ] No unintended behavioral changes to existing features
- [ ] Serialization/persistence formats are backward-compatible (or migration provided)
- [ ] No new circular imports introduced

## Test Plan

<!-- How did you verify this works? Check all that apply. -->

- [ ] Unit tests pass (`.venv/bin/python -m pytest tests/unit/ -x`)
- [ ] Compilation check (`.venv/bin/python -c "from onemancompany.api.routes import router; print('OK')"`)
- [ ] Frontend syntax check (`node -c frontend/app.js`)
- [ ] New tests written for new/changed behavior
- [ ] Manual testing (describe below)

### Manual Test Steps

<!-- Numbered steps a reviewer can follow to verify behavior. -->

1.

## Screenshots

<!-- If UI changes, before/after screenshots. Remove section if N/A. -->
