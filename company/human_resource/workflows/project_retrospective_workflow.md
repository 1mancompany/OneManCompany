# Project Retrospective Workflow

- **Flow ID**: project_review
- **Owner**: HR
- **Collaborators**: COO, all project members
- **Trigger**: Automatically triggered after Project Intake Workflow completes; or manually triggered by CEO

---

## Phase 1: Review Preparation

- **Goal**: Secure a meeting room and ensure all participants are notified
- **Responsible**: HR
- **Steps**:
  1. HR requests a meeting room booking from COO
  2. Notify all participating employees and COO to attend the meeting
  3. If no meeting room is available, employees work on other tasks or refine their work while waiting for a room
- **Output**: Meeting room booking confirmation

## Phase 2: Self-Evaluation

- **Goal**: Each employee honestly assesses their own performance
- **Responsible**: Each participating employee
- **Steps**:
  1. Each employee conducts a self-evaluation of their performance on the project
  2. Self-evaluation covers: personal contribution, work efficiency, errors made, areas for improvement, and any rectifications made during the Acceptance phase
  3. Submit self-evaluation report
- **Output**: Employee self-evaluation report (per person)

## Phase 3: Senior Peer Review

- **Goal**: Senior employees provide objective evaluations of junior work
- **Responsible**: Lv.3 and above employees
- **Steps**:
  1. Senior employees evaluate the work of junior employees
  2. Evaluation dimensions: work efficiency, work quality, whether mistakes were made, and how well rectifications were handled
  3. Evaluations must be objective and fair, with specific suggestions
- **Output**: Peer review report

## Phase 4: HR Summary and Improvement Points

- **Goal**: Consolidate feedback into actionable improvement suggestions per employee
- **Responsible**: HR
- **Steps**:
  1. Consolidate self-evaluation and peer review results
  2. Summarize 1-3 specific improvement suggestions for each employee, especially focusing on avoiding issues that caused rectifications
  3. Send improvement suggestions to the corresponding employees
  4. Employees internalize the feedback and incorporate it into future work
- **Output**: Employee improvement suggestions list

## Phase 4.5: Internalize Feedback into Employee Records

- **Goal**: Persist improvement feedback into employee records for long-term growth
- **Responsible**: HR
- **Steps**:
  1. For each employee who received improvement suggestions in Phase 4:
     - Update the employee's `work_principles.md` with the new lessons learned
     - If a new skill gap was identified, update or create a skill file under `employees/{id}/skills/`
  2. Ensure the updates are concrete and actionable, not vague platitudes
  3. Verify changes are persisted to disk (all employee data is file-based)
- **Output**: Updated employee work_principles.md and/or skill files

## Phase 5: COO Operations Report

- **Goal**: Produce operations status and cost analysis report
- **Responsible**: COO
- **Steps**:
  1. Produce a company operations status report based on project completion
  2. Report covers: project completion rate, resource utilization, potential risks, and analysis of any acceptance failures/rectifications
  3. Include project cost analysis in the report:
     - Compare actual cost vs. budget estimate
     - Identify which phases/employees consumed the most tokens
     - Suggest ways to reduce costs for similar future tasks (e.g., use cheaper models for simple sub-tasks, reduce unnecessary LLM calls)
     - Flag if budget was exceeded and analyze why
  4. Review all file edits made during the project (code changes, document updates)
     - Verify edits were necessary and aligned with the task objectives
     - Flag any unnecessary or risky file modifications
- **Output**: Operations status report (including cost analysis)

## Phase 5.5: Asset Consolidation

- **Goal**: Identify and preserve valuable project deliverables as company assets
- **Responsible**: COO
- **Steps**:
  1. Review all files in the project workspace
  2. Identify deliverables worth preserving as company assets (tools, templates, reference code, etc.)
  3. For each asset candidate, provide: name, description, and which files to include
  4. Skip if no files are worth preserving
- **Output**: Asset consolidation suggestions (JSON list)

## Phase 6: Employee Open Floor

- **Goal**: Give all employees a voice to raise concerns and suggestions
- **Responsible**: All meeting attendees
- **Steps**:
  1. Each employee speaks, and may raise:
     - Difficulties encountered at work
     - Missing tools or equipment
     - What kind of talent is needed
     - Other improvement suggestions
- **Output**: Employee remarks record

## Phase 7: Action Plan Organization

- **Goal**: Organize actionable plans with clear owners and priorities
- **Responsible**: COO + HR
- **Steps**:
  1. COO organizes operations-related action plans (equipment procurement, process optimization)
  2. HR organizes personnel-related action plans (hiring, training, promotions)
  3. Each action plan specifies the responsible person and priority
- **Output**: Action plan list

## Phase 8: EA Approval and Execution

- **Goal**: EA reviews and approves action plans for execution
- **Responsible**: EA
- **Steps**:
  1. All materials are compiled into a complete meeting document
  2. EA reviews action plans on behalf of CEO, evaluating feasibility, relevance, and priority
  3. EA approves or rejects each action item based on review criteria
  4. Approved action plans are dispatched to COO for execution
  5. COO routes HR-related actions to HR, and executes operations-related actions directly
- **Output**: EA approval results, execution status report

## Phase 9: Process Improvement Consolidation

- **Goal**: Standardize process improvements discovered during retrospective
- **Responsible**: COO
- **Steps**:
  1. Review all findings from the retrospective (improvement suggestions, action plans, recurring issues)
  2. Identify any process improvements that should be standardized:
     - If a new workflow or procedure was discovered → create a new SOP in `company/operations/sops/`
     - If an existing SOP needs updating → update it with lessons learned
  3. Each SOP update must include: what changed, why, and when it takes effect
  4. Skip if no process improvements are warranted
- **Output**: New or updated SOP files (if applicable)
