import { api } from "./client";
import type { JobInput } from "@/lib/types/inputs";

export async function fetchJobInputs(jobId: string): Promise<JobInput[]> {
  const { data } = await api.get<JobInput[]>(`/jobs/${jobId}/inputs`);
  return data;
}

export async function answerInput(
  jobId: string,
  inputId: string,
  answer: string,
): Promise<void> {
  await api.post(`/jobs/${jobId}/inputs/${inputId}/answer`, { answer });
}

export async function rejectInput(
  jobId: string,
  inputId: string,
  replacementAnswer: string,
): Promise<void> {
  await api.post(`/jobs/${jobId}/inputs/${inputId}/reject`, { replacement_answer: replacementAnswer });
}
