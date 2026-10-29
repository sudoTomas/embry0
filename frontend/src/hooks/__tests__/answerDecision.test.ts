import { beforeEach, describe, expect, it, vi } from "vitest";

const post = vi.fn();
const put = vi.fn();
vi.mock("@/api/client", () => ({ api: { post: (...a: unknown[]) => post(...a), put: (...a: unknown[]) => put(...a) } }));

import type { Decision, DecisionAction } from "@/api/decisions";
import { answerDecision } from "../useDecisions";

function done(links: Decision["links"] = { job: "job-1" }): Decision {
  return {
    id: "done:job-1",
    stage: "done",
    kind: "full-magnum-opus",
    kind_label: "Code",
    verdict: { label: "Pull request open", tone: "info" },
    title: "Fix the sender",
    repo: "acme/widgets",
    area: null,
    at: null,
    facts: [],
    why: "",
    yes_starts: null,
    actions: [],
    owners: [],
    held: false,
    assigned_to: null,
    links,
    details: "",
  };
}

beforeEach(() => {
  post.mockReset().mockResolvedValue({ data: { followup_job_id: "job-2" } });
  put.mockReset().mockResolvedValue({ data: {} });
});

describe("answering a finished job's decision", () => {
  it.each<[DecisionAction, string, string, unknown]>([
    ["good", "put", "/jobs/job-1/evaluation", { rating: "good" }],
    ["bad", "put", "/jobs/job-1/evaluation", { rating: "bad" }],
    ["merge", "put", "/jobs/job-1/evaluation", { verdict: "merged" }],
    ["send_back", "post", "/jobs/job-1/send-back", { notes: "Handle the empty list too." }],
    ["reject", "post", "/jobs/job-1/reject", { note: "Handle the empty list too.", close_pr: false }],
    ["run_again", "post", "/jobs/job-1/run-again", {}],
  ])("%s reviews the job under /jobs, not the world's quests", async (action, method, path, body) => {
    await answerDecision({ decision: done(), action, by: "ada@example.com", text: "Handle the empty list too." });
    const call = (method === "put" ? put : post).mock.calls[0];
    expect(call?.[0], `${action} called ${JSON.stringify(call?.[0])}`).toBe(path);
    expect(call?.[1]).toEqual(body);
  });

  it("dismisses the job under /jobs", async () => {
    await answerDecision({ decision: done(), action: "dismiss", by: "ada@example.com" });
    expect(post.mock.calls[0]?.[0], `dismiss called ${JSON.stringify(post.mock.calls[0]?.[0])}`).toBe("/jobs/job-1/dismiss");
  });

  it("merges through the merge approval when the job has one, as before", async () => {
    await answerDecision({ decision: done({ job: "job-1", merge_approval: "ma-1" }), action: "merge", by: "ada@example.com" });
    expect(post.mock.calls[0]?.[0]).toBe("/merge-approvals/ma-1/decide");
    expect(put).not.toHaveBeenCalled();
  });
});
