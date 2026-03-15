# Anti-Cloud Acceptance and Evidence-Based Delivery Standards — Training Material v1

## 1. Training Material: Slide Outline and Lecture Notes

### Part 1: Core Values Reinforcement (Evidence-Based Approach)
- **Pain Point**: What is "cloud acceptance"? (i.e., the negligent practice of passing acceptance without opening files or actually running them, based solely on reports or assumptions.)
- **Severe Consequences**: Leads to false deliveries, concealed technical blockers, and severe project stagnation.
- **Core Requirement**: "Physical verification" must be performed — what you see is what you get.

### Part 2: "Acceptance Evidence Checklist" and Violation Red Lines
**Acceptance Evidence Checklist (must be retained for every acceptance)**:
1. **Actual workspace path**: Confirm output files actually exist in the project workspace, not at a fabricated path.
2. **Runtime screenshot/recording**: Must provide actual runtime visuals of the code/product.
3. **Key logs**: Provide screenshot or text of error-free runtime logs.
4. **Version number and change records**: Clearly identify the deliverable version being accepted.

**Violation Red Lines (violation means elimination)**:
- Fabricating false workspace paths or file links.
- Passing project acceptance without actual local or sandbox runtime verification.
- Ignoring obvious systematic errors (e.g., recursion limit exceeded) and forcing acceptance.

### Part 3: Case Exercise
**Scenario**: An engineer reports completing "Angry Birds" game development and provides the path `/Users/fake_path/index.html`, but no runtime screenshot.
**Exercise Actions**:
1. **Wrong approach**: Reply "acceptance passed, great job."
2. **Correct approach**:
   - Check path: Discover the path is not in the project's standard workspace, determine path fabrication.
   - Request runtime screenshots and logs.
   - Reject acceptance and trigger the violation warning procedure.

### Part 4: Quiz Questions (Excerpts)
1. (Multiple choice) When an engineer submits an acceptance request without runtime screenshots, you should:
   A. Trust their technical skills and pass directly
   B. Reject and require all items from the "Acceptance Evidence Checklist"
   C. Write the code for them yourself
   *Answer: B*
2. (True/False) As long as code has no syntax errors, it can pass acceptance without being run. (False)

### Part 5: Sign-In Sheet Template
| Name | Department | Title | Sign-In Time | Signature Confirmation (pledge to uphold evidence-based principles) |
|------|-----------|-------|-------------|---------------------------------------------------------------------|
|      |           |       |             |                                                                     |

---

## 2. Training Execution Record

- **Training Topic**: Anti-Cloud Acceptance and Evidence-Based Delivery Standards
- **Training Time**: 2026-03-05 14:00
- **Attendee List**:
  - Alex COO (00003) - Operations/Project Delivery
  - Pat EA (00004) - CEO Office/Quality Review
  - Morgan CSO (00005) - Sales/Client Delivery Definition
  - Claude PM (00012) - Engineering R&D
- **Assessment Scores**:
  - Alex COO: 100 (Pass)
  - Pat EA: 100 (Pass)
  - Morgan CSO: 100 (Pass)
  - Claude PM: 100 (Pass)
- **Remediation and Follow-Up Actions**:
  1. All management must enforce the "Acceptance Evidence Checklist" verification in all subsequent acceptances.
  2. This document will serve as mandatory training material for all new hires (especially management positions).
