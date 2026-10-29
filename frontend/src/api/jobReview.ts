import { api } from "./client";
import { type RunWith, runWithBody } from "./runWith";

/** Reviewing a finished job: a person's verdict on its work, and what they do with it. Not part of the game:
 * the guild only shows the evaluation. */

export type Rating = "good" | "needs_work" | "bad";
export type Verdict = "merged" | "sent_back" | "rejected";

/** A job's evaluation as it now stands. */
export interface Evaluation {
  rating: Rating | null;
  verdict: Verdict | null;
  note: string;
  followup_job_id: string | null;
  by: string | null;
  at: string | null;
}

function reviewPath(jobId: string, what: string): string {
  return `/jobs/${encodeURIComponent(jobId)}/${what}`;
}

/** Rate the work (good, needs work, bad, with a note), or record that the pull request was merged. */
export async function evaluateJob(
  jobId: string,
  body: { rating?: Rating; verdict?: "merged"; note?: string },
): Promise<Evaluation> {
  const { data } = await api.put<Evaluation>(reviewPath(jobId, "evaluation"), body);
  return data;
}

/** Send the work back with notes: a follow-up job on the job's own pull request branch. */
export async function sendJobBack(jobId: string, notes: string, runWith?: RunWith): Promise<Evaluation> {
  const { data } = await api.post<Evaluation>(reviewPath(jobId, "send-back"), { notes, ...runWithBody(runWith) });
  return data;
}

/** The work is not wanted; with `closePr`, its pull request is closed with the note as its last comment. */
export async function rejectJob(jobId: string, note: string, closePr: boolean): Promise<Evaluation & { closed_pr: boolean }> {
  const { data } = await api.post<Evaluation & { closed_pr: boolean }>(reviewPath(jobId, "reject"), {
    note,
    close_pr: closePr,
  });
  return data;
}

/** Start the job again as a new job, from what started it; the evaluation records the new job. */
export async function runJobAgain(jobId: string, runWith?: RunWith): Promise<Evaluation & { followup_job_id: string }> {
  const { data } = await api.post<Evaluation & { followup_job_id: string }>(reviewPath(jobId, "run-again"), runWithBody(runWith));
  return data;
}

/** Seen, nothing to do: the job leaves the review inbox without a rating. */
export async function dismissJob(jobId: string): Promise<Evaluation> {
  const { data } = await api.post<Evaluation>(reviewPath(jobId, "dismiss"));
  return data;
}
