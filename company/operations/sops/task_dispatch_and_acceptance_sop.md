# Task Dispatch and Acceptance Standard Operating Procedure (SOP)

## 1. Task Dispatch Standards
- The task description must clearly specify the **absolute path** of the workspace.
- Clearly state the directory structure requirements for file storage.
- **Tool usage requirements**: If the task description involves operations that can be completed using system tools (e.g., email, calendar, file saving), the assignee must be explicitly required to invoke the corresponding tool and produce tangible artifacts. Pure text descriptions are not accepted as substitutes.

## 2. Project Acceptance Standards
- **No cloud-based acceptance**: Deliverables must be verified by reading actual files or executing command-line operations (e.g., `ls` / `pwd`).
- **Cross-validation**: File tree structures or physical execution logs must be attached for verification.
- Each acceptance criterion must be verified as actually persisted and executable at the specified path.
- **Tool artifact verification**: For tasks involving system tool operations, acceptance must confirm that the tool was actually invoked and produced real artifacts (e.g., email draft ID, calendar event link, file path). Pure text displays are not considered complete.

## 3. System-Level Verification Credential Requirements
During the task dispatch phase, assignees must be explicitly required to provide at least one of the following system-level credentials upon delivery:
- **API response receipt**: ID, status code, or confirmation returned by the tool call (e.g., Gmail Draft ID, Calendar Event ID)
- **Draft/preview link**: A system-generated accessible link (e.g., email draft link, document preview URL)
- **System state snapshot**: Screenshot or log fragment of the tool state after execution (e.g., file listing, command output)

The reviewer must verify the authenticity of the above credentials upon receipt of deliverables. Verbal/text claims without credentials are not accepted.
