import { useState } from "react";
import type { DoneWhenEdit, JobResponse } from "@/lib/types/jobs";
import { IssueBody } from "@/components/issues/IssueBody";
import { DoneWhenList } from "@/components/jobs/DoneWhenList";
import { jobTitle } from "@/lib/jobTitle";
import { formatDateTime } from "@/lib/utils";

/**
 * The top of a job's page (job briefs, spec 2026-10-26): what the job is
 * about, why it matters and what done looks like, in plain words a model
 * wrote from its task, then what is happening now and what a person can do.
 * The task itself, written for the agents, is one click away. Before the
 * brief is written, the title is the task's first line. Below the brief, the
 * job's done-when list (spec 2026-10-26), editable while the job is live.
 */
export function JobBriefCard({
  job,
  onSaveDoneWhen,
}: {
  job: JobResponse;
  /** Saves a person's done-when list; absent once the job has finished. */
  onSaveDoneWhen?: (body: DoneWhenEdit) => Promise<unknown>;
}) {
  const [taskOpen, setTaskOpen] = useState(false);
  const brief = job.brief ?? null;
  const words = job.status_words;
  const place = [job.repo, job.area, brief?.size].filter(Boolean).join(" · ");
  return (
    <section className="space-y-3 rounded-md border border-border bg-card px-4 py-3" data-testid="job-brief">
      <div className="space-y-0.5">
        <h2 className="text-base font-semibold text-fg">{brief?.title ?? jobTitle(job.task)}</h2>
        {place && <p className="text-xs text-fg-muted">{place}</p>}
      </div>
      {(brief || words) && (
        <dl className="grid grid-cols-[3.5rem_minmax(0,1fr)] gap-x-3 gap-y-1.5 text-sm">
          {brief && (
            <>
              <dt className="text-fg-muted">What</dt>
              <dd className="text-fg-secondary">{brief.what}</dd>
              <dt className="text-fg-muted">Why</dt>
              <dd className="text-fg-secondary">{brief.why}</dd>
              <dt className="text-fg-muted">Done</dt>
              <dd className="text-fg-secondary">{brief.done}</dd>
            </>
          )}
          {words && (
            <>
              <dt className={brief ? "mt-1.5 text-fg-muted" : "text-fg-muted"}>Now</dt>
              <dd className={brief ? "mt-1.5 text-fg" : "text-fg"} data-testid="job-status-words" data-state={words.state}>
                {words.headline}
                {words.until && ` It goes on at ${formatDateTime(words.until)}, or sooner.`}
                {words.next && ` ${words.next}`}
              </dd>
            </>
          )}
        </dl>
      )}
      <DoneWhenList doneWhen={job.done_when} onSave={onSaveDoneWhen} />
      <div className="flex justify-end">
        <button
          type="button"
          className="text-xs text-fg-muted underline hover:text-fg"
          aria-expanded={taskOpen}
          onClick={() => setTaskOpen(!taskOpen)}
        >
          {taskOpen ? "Hide the full task" : "Full task"}
        </button>
      </div>
      {taskOpen && (
        <div className="max-h-[32rem] overflow-auto rounded-md border border-border bg-canvas p-3" data-testid="job-full-task">
          <IssueBody body={job.task} />
        </div>
      )}
    </section>
  );
}
