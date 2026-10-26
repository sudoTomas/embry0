import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { JobResponse } from "@/lib/types/jobs";
import { JobBriefCard } from "../JobBriefCard";

const TASK = "[ENG-3075] [watcher] Diagnose the worker boot crash-loop\n\n## Evidence\n\n- **20:03** no live upstreams";

const JOB = {
  job_id: "job-1",
  status: "paused",
  repo: "acme/backend",
  area: "Leads",
  task: TASK,
  created_at: "2026-10-25T12:00:00+00:00",
  started_at: null,
  finished_at: null,
  total_cost_usd: 4.5,
  brief: {
    title: "Find why the worker crash-looped and stop it",
    what: "Both worker copies failed to boot 22 times on Oct 15; its pages errored.",
    why: "A boot crash takes every page down, and the logs kept no cause.",
    done: "The boot error is logged in full and the restart path is closed. You get a pull request.",
    size: "medium",
    model: "custom/local",
    written_at: "2026-10-26T10:00:00+00:00",
  },
  status_words: {
    state: "paused",
    headline: "Paused: the developer ran out of turns; its work is saved on its branch.",
    next: "Continue (+$10) or stop.",
    actions: ["continue_retrying", "abandon"],
    until: null,
  },
} as unknown as JobResponse;

describe("JobBriefCard (job briefs, spec 2026-10-26)", () => {
  it("leads with the brief: title, place and size, what, why, done, and now", () => {
    render(<JobBriefCard job={JOB} />);
    const card = screen.getByTestId("job-brief");
    expect(screen.getByRole("heading", { name: "Find why the worker crash-looped and stop it" })).toBeInTheDocument();
    expect(card).toHaveTextContent("acme/backend · Leads · medium");
    expect(card).toHaveTextContent("WhatBoth worker copies failed to boot 22 times on Oct 15; its pages errored.");
    expect(card).toHaveTextContent("WhyA boot crash takes every page down, and the logs kept no cause.");
    expect(card).toHaveTextContent("You get a pull request.");
    expect(screen.getByTestId("job-status-words")).toHaveTextContent(
      "Paused: the developer ran out of turns; its work is saved on its branch. Continue (+$10) or stop.",
    );
    expect(card).not.toHaveTextContent("ENG-3075");
  });

  it("keeps the task one click away, rendered as markdown", async () => {
    render(<JobBriefCard job={JOB} />);
    expect(screen.queryByTestId("job-full-task")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Full task" }));
    const task = screen.getByTestId("job-full-task");
    expect(task).toHaveTextContent("Diagnose the worker boot crash-loop");
    expect(task.querySelector("strong")).toHaveTextContent("20:03");
    await userEvent.click(screen.getByRole("button", { name: "Hide the full task" }));
    expect(screen.queryByTestId("job-full-task")).toBeNull();
  });

  it("before the brief is written, names the job by its task and still says what is happening now", () => {
    render(<JobBriefCard job={{ ...JOB, area: null, brief: null }} />);
    expect(screen.getByRole("heading")).toHaveTextContent("[ENG-3075] [watcher] Diagnose the worker boot crash-loop");
    expect(screen.getByTestId("job-brief")).toHaveTextContent("acme/backend");
    expect(screen.queryByText("What")).toBeNull();
    expect(screen.getByTestId("job-status-words")).toHaveAttribute("data-state", "paused");
  });

  it("says when a wait ends, in the viewer's time", () => {
    const waiting = {
      ...JOB,
      status_words: { state: "waiting", headline: "Waiting: the Claude account is quota exhausted.", next: "Try now or cancel.", actions: [], until: "2026-10-27T00:00:00+00:00" },
    } as JobResponse;
    render(<JobBriefCard job={waiting} />);
    expect(screen.getByTestId("job-status-words")).toHaveTextContent(/It goes on at .+, or sooner\. Try now or cancel\./);
  });
});
