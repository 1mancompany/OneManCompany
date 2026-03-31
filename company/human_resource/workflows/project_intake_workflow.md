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
