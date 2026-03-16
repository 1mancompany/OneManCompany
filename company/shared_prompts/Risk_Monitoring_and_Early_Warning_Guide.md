# Project Risk Monitoring and Early Warning Operations Guide (For EA and Management)

## 1. Objectives and Scope
This guide aims to help the EA (Executive Assistant) and relevant management personnel (such as COO, CSO, etc.) quickly and accurately review error logs during project follow-up and acceptance, identify engineers' systematic errors and technical blockers, and establish effective early warning and blocker removal mechanisms to prevent projects from falling into infinite loops or stagnation due to technical issues.
Applicable scope: All software development, iterative delivery, and project acceptance processes involving code output.

## 2. Core Review Steps and Operating Procedures

### 1. Pre-Acceptance Basic Review (Prevention)
When an engineer submits deliverables (e.g., submits an acceptance request or updates project status to `completed`), management must perform the first round of review.
*   **Verify deliverable list and paths:**
    *   **Action:** Check whether actual deliverable files are stored in the project's designated `workspace` directory.
    *   **Warning signal:** Files stored in old directories, temporary directories (e.g., `/tmp`), or default root directories.
    *   **Response:** Immediately reject and clearly indicate the correct storage path.
*   **Review output information completeness:**
    *   **Action:** Check whether task output (`output`) and detailed logs (`detail`) are truncated (e.g., ending with strange characters or incomplete sentences).
    *   **Warning signal:** Text truncation indicates the system may have encountered length limits (token exceeded) or internal errors during log generation.
    *   **Response:** Require the submitter (or engineer) to provide complete information and check whether token limits were triggered (e.g., `Error code: 402`).

### 2. Deep Error Log Review (Identifying Systematic Issues)
When a project is rejected or an engineer reports encountering issues, logs must be thoroughly reviewed.
*   **Action:** Review the project timeline (`timeline`) or task execution logs, focusing on keywords like `Error`, `Exception`, `Failed`.
*   **Common systematic errors and warning signals:**
    *   **Recursion failure/infinite loop:**
        *   **Signal:** The same task or subtask is dispatched repeatedly (e.g., the same "file path error" remediation task dispatched multiple times), and the engineer's responses are identical or fall into a "fix-fail-fix again-fail again" cycle.
        *   **Example:** In the `iter_002` project, EA repeatedly rejected path errors, the engineer repeatedly claimed migration was done, but metadata (e.g., `status`) was not updated, causing a loop.
    *   **Build/dependency cascading failures:**
        *   **Signal:** Logs frequently show `ModuleNotFoundError`, `ImportError`, `Build failed`, and fixing one issue immediately triggers another dependency error.
    *   **Runtime crash:**
        *   **Signal:** Logs show `Segmentation fault`, `Out of memory`, `Connection refused`, or other severe system-level errors.
    *   **Resource/permission restrictions:**
        *   **Signal:** `Error code: 402` (insufficient funds/tokens), `Permission denied` (no access to files or directories).

### 3. Technical Blocker Identification and Removal
*   **Identifying blockers:** When the above warning signals appear more than **2 times**, or cause the project to stagnate beyond the expected duration (e.g., half a day), it is classified as a "technical blocker."
*   **Blocker removal mechanism:**
    1.  **Pause automatic flow:** Immediately stop automated task dispatch to prevent machine loops from consuming resources.
    2.  **Manual intervention and diagnosis:** Management (e.g., COO) must personally intervene, not just reviewing the engineer's "self-assessment" or "response" but delving into underlying configuration files (e.g., `project.yaml`, `iterations.yaml`) and the actual file system.
    3.  **Find the root cause:** Distinguish between "files genuinely misplaced" vs. "system metadata not updated" (e.g., `status: in_progress` causing continuous retries).
    4.  **Forced intervention:** Allow or authorize the engineer to modify system metadata (provided actual work is confirmed complete), or have management manually update the status to break the loop.

## 3. Warning Level Classification and Escalation Path

| Warning Level | Trigger Condition | Response Action | Escalation Path |
| :--- | :--- | :--- | :--- |
| **Low (L1)** | Single deliverable path error, occasional compilation error, minor log truncation. | Point out the issue in task comments, require engineer to remediate. | If the same issue repeats 2 times, escalate to Medium. |
| **Medium (L2)** | Task falls into infinite loop (e.g., dispatched 3+ times), frequent dependency errors, token restrictions (e.g., 402 error). | Pause automatic flow, management manually reviews logs, locates blocker. | If blocker involves underlying system configuration or cannot be resolved for an extended period, escalate to High. |
| **High (L3)** | Systematic crash, severe permission issues, project completely stalled due to technical blocker. | Immediately notify CEO, convene emergency technical meeting, may need to modify underlying framework or add budget. | Record in critical risk registry, conduct post-mortem review. |

## 4. Case Review: Angry Birds (iter_002) Infinite Loop Incident
*   **Symptom:** EA repeatedly rejected the project, citing incorrect file storage paths; the engineer replied multiple times that files had been migrated, but the task kept being re-dispatched.
*   **Review blind spot:** EA and COO relied solely on surface-level path checking logic without deeply verifying project metadata (`project.yaml` or `iterations.yaml`).
*   **Root cause:** Files had actually been migrated to the correct directory, but in the underlying YAML file driving the acceptance loop, `status` remained `in_progress` and `output` was empty, causing the system to determine the task was incomplete.
*   **Correct action:** After discovering repeated dispatch (warning signal: recursion failure), management should have immediately intervened, verified the file system to find files were in place, checked the YAML configuration, and authorized the engineer to update `status: completed` and `output` to break the loop.

## 5. Archiving and Enforcement Requirements
1.  This guide takes effect from the date of publication. All new project acceptances must strictly follow this guide for basic review and deep log review.
2.  EA (Pat) must explicitly perform "anti-truncation" and "anti-infinite-loop" checks before each acceptance.
3.  COO (Alex) and other management personnel bear primary responsibility for intervening on L2 and above warnings.
4.  This training record and guide document will be archived in the company's shared knowledge base or project management standards directory as a basis for future evaluations.
