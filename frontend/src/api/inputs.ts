import { api } from "./client";
import type { JobInput, InputResponse } from "@/lib/types/inputs";

export async function fetchJobInputs(jobId: string): Promise<JobInput[]> {
  const { data } = await api.get<JobInput[]>(`/jobs/${jobId}/inputs`);
  return data;
}

export async function answerInput(
  issueId: string | null | undefined,
  inputId: string,
  answer: string,
  jobId?: string,
): Promise<void> {
  // EMB-43: issue-less jobs (direct POST /jobs) have no issue row — answer
  // via the job-scoped endpoint; issue-backed inputs keep the issue path
  // (its resume also updates the issue row).
  if (issueId) {
    await api.post(`/issues/${issueId}/inputs/${inputId}/answer`, { answer });
  } else if (jobId) {
    await api.post(`/jobs/${jobId}/inputs/${inputId}/answer`, { answer });
  } else {
    throw new Error("answerInput requires issueId or jobId");
  }
}

export async function rejectInput(
  jobId: string,
  inputId: string,
  replacementAnswer: string,
): Promise<void> {
  await api.post(`/jobs/${jobId}/inputs/${inputId}/reject`, { replacement_answer: replacementAnswer });
}

export async function fetchIssueInputs(issueId: string): Promise<InputResponse[]> {
  const { data } = await api.get(`/issues/${issueId}/inputs`);
  return data;
}
