# Workflow Schema Reference

## Header Section

Every workflow file must start with an H1 title followed by these metadata fields:

- **Flow ID** *(required)*: unique snake_case identifier, e.g. `project_retrospective`
- **Owner** *(required)*: which role owns this workflow, e.g. `HR`, `COO`
- **Collaborators** *(optional)*: other roles involved at the workflow level
- **Trigger** *(optional)*: what event starts this workflow

## Phase Section

Each `## Phase N: Title` block defines one step in the workflow:

- **Goal** *(required)*: what outcome this phase must achieve
- **Responsible** *(required)*: who executes this phase, e.g. `HR`, `COO`, `Each participating employee`
- **Collaborators** *(optional)*: supporting roles for this phase only
- **Depends on** *(optional)*: prerequisite phases, e.g. `Phase 1` or `Phase 1, Phase 2`
- **Steps** *(optional)*: numbered or bulleted sub-tasks
- **Output** *(optional)*: concrete deliverable produced by this phase

## Field Rules

| Field | Scope | Required |
|---|---|---|
| Flow ID | Header | Yes |
| Owner | Header | Yes |
| Collaborators | Header / Phase | No |
| Trigger | Header | No |
| Goal | Phase | Yes |
| Responsible | Phase | Yes |
| Depends on | Phase | No |
| Steps | Phase | No |
| Output | Phase | No |

## Example

```markdown
# Onboarding Workflow

- **Flow ID**: onboarding
- **Owner**: HR
- **Trigger**: New employee joins

---

## Phase 1: Prepare Workstation

- **Goal**: Ensure workstation and accounts are ready before day one
- **Responsible**: COO
- **Steps**:
  1. Set up computer and access credentials
  2. Add to company communication channels
- **Output**: Workstation ready checklist

## Phase 2: Welcome Orientation

- **Goal**: New hire understands company culture and their role
- **Responsible**: HR
- **Depends on**: Phase 1
- **Steps**:
  1. Walk through company handbook
  2. Introduce to team members
- **Output**: Signed onboarding confirmation
```
