# Project Intake Workflow

- **Flow ID**: project_intake
- **Owner**: COO
- **Collaborators**: HR, relevant employees
- **Trigger**: CEO assigns a new project task

---

## Phase 1: Task Description Summary

- **Responsible**: Highest rank Project Manager (if not exits, then COO)
- **Steps**:
  1. Receive the project task assigned by the CEO
  2. Analyze task requirements and break them down into executable sub-tasks
  3. Write a project overview document clarifying background, scope, and constraints
- **Output**: Project overview document

## Phase 2: Goal Setting

- **Responsible**: Any idle Project Manager (if not exits, then COO)
- **Steps**:
  1. Define quantifiable goals based on the project overview (SMART principles)
  2. Establish project milestones and delivery deadlines
  3. Define acceptance criteria. **必须逐字核对原始需求并修订验收标准，确保需求全覆盖，避免遗漏任何功能点（如UI视觉表现、物理参数调整等细节）。**
- **Output**: Project goals and milestones document

## Phase 3: Personnel Assignment

- **Responsible**: Highest rank Project Manager (if not exits, then HR)
- **Steps**:
  1. Assess existing staff capabilities based on project requirements
  2. Assign project members and designate a project lead
  3. If staffing is insufficient, initiate the Hiring Workflow
  4. Confirm each member's role and responsibilities
- **Output**: Project staffing assignment sheet

## Phase 4: Project Execution and Tracking

- **Responsible**: Assigned Project Manager (if not exits, then COO)
- **Steps**:
  1. Project Manager assign tasks to teammates. **必须完善任务分派记录机制，在项目记录中明确列出所有子任务与对应负责人的映射关系，落实权责清晰与任务闭环。**
  2. Each member executes tasks according to their assigned roles. **执行任务时需遵循模型分级调用成本优化策略：处理UI生成等复杂任务时使用高性能模型，修改物理参数等简单任务降级使用低成本模型。**
  3. **Self-Verification (MANDATORY before reporting completion)**:
     - For code/software: Build and run it. Fix all errors until it runs successfully.
     - For documents/reports: Re-read and verify all claims, data, and formatting.
     - For any deliverable: Test it as a real end-user would. Verify it meets ALL acceptance criteria.
     - Include verification evidence (test output, screenshots, run logs) in your result.
     - Do NOT report a task as complete unless you have personally verified it works.
  4. PM periodically checks progress against milestones
  5. Request resources or plan adjustments from COO when encountering blockers
- **Output**: Progress update records with verification evidence

## Phase 5: Project Acceptance & Rectification

- **Responsible**: Assigned Project Manager (if not exits, then COO)
- **Steps**:
  1. Strict Review: Review deliverables strictly against the predefined acceptance criteria defined in Phase 2.
  2. Quality Review: Organize relevant personnel to conduct a comprehensive quality review of the outputs.
  3. **建立严格的代码变更审查机制：实事求是地把控每次代码修改的必要性，降低不同模块（如UI视觉升级与物理引擎参数修改）高耦合带来的项目延期风险。**
  4. Spot-check: Senior employees spot-check the work output of junior employees.
  5. Acceptance Decision: 
     - If passed: Generate an acceptance report and proceed to the next phase.
     - If not passed: Generate a Rectification Notice detailing specific issues and improvement suggestions.
  6. Rectification Loop:
     - The responsible employee(s) must revise their work according to the Rectification Notice.
     - Resubmit the revised deliverables for another round of Project Acceptance (repeat Phase 5).
- **Output**: Acceptance report (if passed) or Rectification Notice (if not passed)

## Phase 6: Project Report Writing

- **Responsible**: COO
- **Collaborators**: All project members
- **Steps**:
  1. Consolidate key data and results from the project process
  2. Each member submits a personal contribution summary
  3. COO writes the complete project report
  4. Report is submitted to CEO for review
- **Output**: Final project report
- **Follow-up**: Automatically triggers the Project Retrospective Workflow
