# Project Retrospective Workflow

- **Flow ID**: project_review
- **Owner**: HR
- **Collaborators**: COO, all project members
- **Trigger**: Automatically triggered after Project Intake Workflow completes; or manually triggered by CEO

---

## Phase 1: Review Preparation

- **Responsible**: HR
- **Steps**:
  1. HR requests a meeting room booking from COO
  2. Notify all participating employees and COO to attend the meeting
  3. If no meeting room is available, employees work on other tasks or refine their work while waiting for a room
- **Output**: Meeting room booking confirmation

## Phase 2: Self-Evaluation

- **Responsible**: Each participating employee
- **Steps**:
  1. Each employee conducts a self-evaluation of their performance on the project
  2. Self-evaluation covers: personal contribution, work efficiency, errors made, areas for improvement, and any rectifications made during the Acceptance phase
  3. Submit self-evaluation report
- **Output**: Employee self-evaluation report (per person)

## Phase 3: Senior Peer Review

- **Responsible**: Lv.3 and above employees
- **Steps**:
  1. Senior employees evaluate the work of junior employees
  2. Evaluation dimensions: work efficiency, work quality, whether mistakes were made, and how well rectifications were handled
  3. Evaluations must be objective and fair, with specific suggestions
- **Output**: Peer review report

## Phase 4: HR Summary and Improvement Points

- **Responsible**: HR
- **Steps**:
  1. Consolidate self-evaluation and peer review results
  2. Summarize 1-3 specific improvement suggestions for each employee, especially focusing on avoiding issues that caused rectifications
  3. Send improvement suggestions to the corresponding employees
  4. Employees internalize the feedback and incorporate it into future work
- **Output**: Employee improvement suggestions list

## Phase 5: COO Operations Report

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

## Phase 6: Employee Open Floor

- **Responsible**: All meeting attendees
- **Steps**:
  1. Each employee speaks, and may raise:
     - Difficulties encountered at work
     - Missing tools or equipment
     - What kind of talent is needed
     - Other improvement suggestions
- **Output**: Employee remarks record

## Phase 7: Action Plan Organization

- **Responsible**: COO + HR
- **Steps**:
  1. COO organizes operations-related action plans (equipment procurement, process optimization)
  2. HR organizes personnel-related action plans (hiring, training, promotions)
  3. Each action plan specifies the responsible person and priority
- **Output**: Action plan list

## Phase 8: CEO Approval and Execution

- **Responsible**: CEO
- **Steps**:
  1. All materials are compiled into a complete meeting document and submitted to CEO
  2. CEO reviews and selects which improvement points should be executed
  3. Approved action plans are executed by HR and COO respectively
  4. HR executes personnel-related improvements (hiring, training, promotions)
  5. COO executes operations-related improvements (equipment procurement, process adjustments)
- **Output**: Approved execution list, execution results report
