/**
 * WebSocket event type definitions — auto-generated from event_models.py
 *
 * Each CompanyEvent has a `type` string and a typed `payload`.
 * Use the discriminated union `CompanyEvent` for type-safe event handling.
 */

// ---------------------------------------------------------------------------
// Event payload types
// ---------------------------------------------------------------------------

interface TaskStartedPayload {
  task_id: string;
  employee_id: string;
  description: string;
}

interface TaskCompletedPayload {
  task_id: string;
  employee_id: string;
  success: boolean;
  output_summary: string;
  cost_usd: number;
}

interface AgentThinkingPayload {
  employee_id: string;
  message: string;
  content: string;
  tool_name: string | null;
}

interface AgentDonePayload {
  role: string;
  summary: string;
  employee_id: string;
}

interface AgentLogPayload {
  employee_id: string;
  log_type: string;
  content: string;
}

interface AgentTaskUpdatePayload {
  employee_id: string;
  task_id: string;
  status: string;
  summary: string;
}

interface CandidatesReadyPayload {
  batch_id: string;
  jd: string;
  candidates: Record<string, unknown>[];
  roles?: { role: string; description: string; candidates: Record<string, unknown>[] }[];
}

interface ResolutionReadyPayload {
  resolution_id: string;
  edit_count: number;
  project_id: string | null;
}

interface ResolutionDecidedPayload {
  resolution_id: string;
  project_id: string;
  results: Record<string, unknown>[];
}

interface MeetingChatPayload {
  meeting_id: string;
  speaker_id: string;
  speaker_name: string;
  content: string;
}

interface MeetingBookedPayload {
  meeting_id: string;
  room_id: string;
  booked_by: string;
  participants: string[];
}

interface EmployeeHiredPayload {
  name: string;
  nickname: string;
  role: string;
  employee_id: string;
}

interface EmployeeFiredPayload {
  name: string;
  nickname: string;
  employee_id: string;
  reason: string;
}

interface EmployeeReviewedPayload {
  employee_id: string;
  score: number;
  quarter: number;
}

interface FileEditProposedPayload {
  edit_id: string;
  file_path: string;
  employee_id: string;
  reason: string;
}

interface GuidancePayload {
  employee_id: string;
  content: string;
}

interface StateSnapshotPayload {}

interface InquiryPayload {
  employee_id: string;
  inquiry_id: string;
  question: string;
}

interface WorkflowUpdatedPayload {
  workflow_id: string;
  phase: string;
}

interface CompanyCultureUpdatedPayload {
  items: Record<string, unknown>[];
}

// ---------------------------------------------------------------------------
// Discriminated union — all WebSocket events
// ---------------------------------------------------------------------------

type CompanyEvent =
  | { type: "employee_hired"; payload: EmployeeHiredPayload; agent: string }
  | { type: "employee_fired"; payload: EmployeeFiredPayload; agent: string }
  | { type: "employee_reviewed"; payload: EmployeeReviewedPayload; agent: string }
  | { type: "employee_rehired"; payload: EmployeeHiredPayload; agent: string }
  | { type: "tool_added"; payload: Record<string, unknown>; agent: string }
  | { type: "ceo_task_submitted"; payload: TaskStartedPayload; agent: string }
  | { type: "agent_thinking"; payload: AgentThinkingPayload; agent: string }
  | { type: "agent_done"; payload: AgentDonePayload; agent: string }
  | { type: "agent_log"; payload: AgentLogPayload; agent: string }
  | { type: "agent_task_update"; payload: AgentTaskUpdatePayload; agent: string }
  | { type: "state_snapshot"; payload: StateSnapshotPayload; agent: string }
  | { type: "guidance_start"; payload: GuidancePayload; agent: string }
  | { type: "guidance_noted"; payload: GuidancePayload; agent: string }
  | { type: "guidance_end"; payload: GuidancePayload; agent: string }
  | { type: "meeting_booked"; payload: MeetingBookedPayload; agent: string }
  | { type: "meeting_released"; payload: Record<string, unknown>; agent: string }
  | { type: "meeting_denied"; payload: Record<string, unknown>; agent: string }
  | { type: "meeting_chat"; payload: MeetingChatPayload; agent: string }
  | { type: "meeting_report_ready"; payload: Record<string, unknown>; agent: string }
  | { type: "routine_phase"; payload: Record<string, unknown>; agent: string }
  | { type: "workflow_updated"; payload: WorkflowUpdatedPayload; agent: string }
  | { type: "candidates_ready"; payload: CandidatesReadyPayload; agent: string }
  | { type: "company_culture_updated"; payload: CompanyCultureUpdatedPayload; agent: string }
  | { type: "file_edit_proposed"; payload: FileEditProposedPayload; agent: string }
  | { type: "file_edit_applied"; payload: Record<string, unknown>; agent: string }
  | { type: "file_edit_rejected"; payload: Record<string, unknown>; agent: string }
  | { type: "resolution_ready"; payload: ResolutionReadyPayload; agent: string }
  | { type: "resolution_decided"; payload: ResolutionDecidedPayload; agent: string }
  | { type: "inquiry_started"; payload: InquiryPayload; agent: string }
  | { type: "inquiry_ended"; payload: InquiryPayload; agent: string };

// ---------------------------------------------------------------------------
// Helper: extract event type string
// ---------------------------------------------------------------------------

type EventType = CompanyEvent["type"];
