export type InputStatus = "pending" | "answered" | "auto_answered" | "rejected" | "timeout";

export interface JobInput {
  input_id: string;
  job_id: string;
  issue_id: string;
  question: string;
  category: string;
  options: string[] | null;
  status: InputStatus;
  answer: string | null;
  auto_answer: string | null;
  created_at: string;
  answered_at: string | null;
}

export type InputImportance = "blocking" | "auto_answerable";
export type IssueInputStatus = "pending" | "auto_answered" | "answered";

export interface InputResponse {
  id: string;
  issue_id: string;
  job_id: string;
  asking_node: string;
  question: string;
  importance: InputImportance;
  auto_answer: string | null;
  answer: string | null;
  answered_by: string | null;
  status: IssueInputStatus;
  created_at: string;
  answered_at: string | null;
}
