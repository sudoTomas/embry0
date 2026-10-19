// Mirrors the statuses the backend actually writes (issue_inputs repo):
// pending -> answered | auto_answered | skipped. An overridden auto-answer is
// status "answered" with auto_answer still set (RAV-1059) — not a separate state.
export type InputStatus = "pending" | "answered" | "auto_answered" | "skipped";

export type InputImportance = "blocking" | "auto_answerable";

/**
 * One question an agent asked: an issue_inputs row, as both
 * GET /jobs/{id}/inputs and GET /issues/{id}/inputs return it (the backend's
 * InputResponse in embry0/api/schemas/inputs.py). issue_id is null for a job
 * created without an issue; such a question is answered through the job.
 */
export interface InputResponse {
  id: string;
  issue_id: string | null;
  job_id: string;
  asking_node: string;
  question: string;
  importance: InputImportance;
  auto_answer: string | null;
  answer: string | null;
  answered_by: string | null;
  status: InputStatus;
  created_at: string;
  answered_at: string | null;
}

/** The rows GET /jobs/{id}/inputs returns: the same issue_inputs rows. */
export type JobInput = InputResponse;
