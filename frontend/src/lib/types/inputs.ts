export type InputStatus = "pending" | "answered" | "auto_answered" | "rejected" | "timeout";

export interface JobInput {
  input_id: string;
  job_id: string;
  question: string;
  category: string;
  options: string[] | null;
  status: InputStatus;
  answer: string | null;
  auto_answer: string | null;
  created_at: string;
  answered_at: string | null;
}
