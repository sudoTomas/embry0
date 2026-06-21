import { describe, it, expect, vi, beforeEach } from "vitest";

// agent.ts owns a sibling axios instance scoped to the companion-proxy prefix
// (`/agent`), separate from the orchestrator's `/api/v1` client. These tests
// pin the boundary: every public function's URL + HTTP method is part of the
// contract the rest of the app relies on.

import {
  agentApi,
  fetchTasks,
  fetchTaskBlockedBy,
  deployTask,
  requeueTask,
  retryTask,
  stopTask,
  deadLetterTask,
  fetchCosts,
  fetchStats,
  fetchEvents,
  fetchGitActivity,
  fetchProjects,
  fetchRoutingStats,
  fetchReviewStats,
  fetchHardware,
  fetchMemories,
  fetchProposals,
  rescoreProposal,
  shipProposal,
  batchShipProposals,
  fetchRepos,
  pushRepo,
  pushRepoPr,
  mergeRepoPr,
  fetchNotifications,
  markAllNotificationsRead,
  interpretCommand,
  submitFeedback,
} from "../agent";

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("agentApi axios instance", () => {
  it("targets the /agent reverse-proxy prefix, not /api/v1", () => {
    expect(agentApi.defaults.baseURL).toBe("/agent");
  });
});

describe("agent API client — read endpoints", () => {
  const cases: ReadonlyArray<readonly [string, () => Promise<unknown>, string]> = [
    ["fetchTasks", fetchTasks, "/tasks"],
    ["fetchCosts", fetchCosts, "/costs"],
    ["fetchStats", fetchStats, "/stats"],
    ["fetchEvents", fetchEvents, "/events"],
    ["fetchGitActivity", fetchGitActivity, "/git-activity"],
    ["fetchProjects", fetchProjects, "/projects"],
    ["fetchRoutingStats", fetchRoutingStats, "/routing-stats"],
    ["fetchReviewStats", fetchReviewStats, "/review-stats"],
    ["fetchHardware", fetchHardware, "/hardware"],
    ["fetchMemories", fetchMemories, "/memories"],
    ["fetchProposals", fetchProposals, "/proposals"],
    ["fetchRepos", fetchRepos, "/repos"],
    ["fetchNotifications", fetchNotifications, "/notifications"],
  ];

  for (const [name, fn, path] of cases) {
    it(`${name} GETs ${path}`, async () => {
      const get = vi.spyOn(agentApi, "get").mockResolvedValue({ data: [] });
      await fn();
      expect(get).toHaveBeenCalledWith(path);
    });
  }
});

describe("agent API client — task actions", () => {
  const cases: ReadonlyArray<readonly [string, (id: string) => Promise<unknown>, string]> = [
    ["deployTask", deployTask, "/tasks/abc/deploy"],
    ["requeueTask", requeueTask, "/tasks/abc/requeue"],
    ["retryTask", retryTask, "/tasks/abc/retry"],
    ["stopTask", stopTask, "/tasks/abc/stop"],
    ["deadLetterTask", deadLetterTask, "/tasks/abc/dead-letter"],
  ];

  for (const [name, fn, path] of cases) {
    it(`${name} POSTs ${path}`, async () => {
      const post = vi.spyOn(agentApi, "post").mockResolvedValue({ data: { id: "abc" } });
      await fn("abc");
      expect(post).toHaveBeenCalledWith(path);
    });
  }

  it("encodes task ids that contain URL-unsafe characters", async () => {
    const post = vi.spyOn(agentApi, "post").mockResolvedValue({ data: { id: "x/y" } });
    await deployTask("a b/c");
    expect(post).toHaveBeenCalledWith("/tasks/a%20b%2Fc/deploy");
  });
});

describe("agent API client — proposal actions", () => {
  it("rescoreProposal POSTs /proposals/:id/rescore", async () => {
    const post = vi.spyOn(agentApi, "post").mockResolvedValue({ data: { id: "p-1" } });
    await rescoreProposal("p-1");
    expect(post).toHaveBeenCalledWith("/proposals/p-1/rescore");
  });

  it("shipProposal POSTs /proposals/:id/ship", async () => {
    const post = vi.spyOn(agentApi, "post").mockResolvedValue({ data: { id: "p-1" } });
    await shipProposal("p-1");
    expect(post).toHaveBeenCalledWith("/proposals/p-1/ship");
  });

  it("rescoreProposal/shipProposal encode URL-unsafe ids", async () => {
    const post = vi.spyOn(agentApi, "post").mockResolvedValue({ data: { id: "x" } });
    await rescoreProposal("a/b");
    expect(post).toHaveBeenCalledWith("/proposals/a%2Fb/rescore");
    await shipProposal("a/b");
    expect(post).toHaveBeenCalledWith("/proposals/a%2Fb/ship");
  });

  it("batchShipProposals POSTs /proposals/ship with body { ids }", async () => {
    const post = vi.spyOn(agentApi, "post").mockResolvedValue({ data: { shipped: ["p-1", "p-2"] } });
    await batchShipProposals(["p-1", "p-2"]);
    expect(post).toHaveBeenCalledWith("/proposals/ship", { ids: ["p-1", "p-2"] });
  });
});

describe("agent API client — fetchTaskBlockedBy", () => {
  it("GETs /tasks/:id/blocked-by", async () => {
    const get = vi
      .spyOn(agentApi, "get")
      .mockResolvedValue({ data: { id: "abc", blocked_by: [] } });
    await fetchTaskBlockedBy("abc");
    expect(get).toHaveBeenCalledWith("/tasks/abc/blocked-by");
  });

  it("encodes ids with URL-unsafe characters", async () => {
    const get = vi
      .spyOn(agentApi, "get")
      .mockResolvedValue({ data: { id: "a b/c", blocked_by: [] } });
    await fetchTaskBlockedBy("a b/c");
    expect(get).toHaveBeenCalledWith("/tasks/a%20b%2Fc/blocked-by");
  });
});

describe("agent API client — repo git-op actions", () => {
  const cases: ReadonlyArray<readonly [string, (slug: string) => Promise<unknown>, string]> = [
    ["pushRepo", pushRepo, "/repos/org%2Frepo/push"],
    ["pushRepoPr", pushRepoPr, "/repos/org%2Frepo/push-pr"],
    ["mergeRepoPr", mergeRepoPr, "/repos/org%2Frepo/merge-pr"],
  ];

  for (const [name, fn, path] of cases) {
    it(`${name} POSTs ${path}`, async () => {
      const post = vi
        .spyOn(agentApi, "post")
        .mockResolvedValue({ data: { slug: "org/repo" } });
      await fn("org/repo");
      expect(post).toHaveBeenCalledWith(path);
    });
  }
});

describe("agent API client — notifications + interpret", () => {
  it("markAllNotificationsRead POSTs /notifications/read-all", async () => {
    const post = vi.spyOn(agentApi, "post").mockResolvedValue({ data: { ok: true } });
    await markAllNotificationsRead();
    expect(post).toHaveBeenCalledWith("/notifications/read-all");
  });

  it("interpretCommand POSTs /interpret with the query body", async () => {
    const post = vi
      .spyOn(agentApi, "post")
      .mockResolvedValue({ data: { intent: "navigate", message: "open jobs", url: "/jobs" } });
    const result = await interpretCommand("show me jobs");
    expect(post).toHaveBeenCalledWith("/interpret", { q: "show me jobs" });
    expect(result).toEqual({ intent: "navigate", message: "open jobs", url: "/jobs" });
  });
});

describe("submitFeedback", () => {
  it("POSTs /feedback with FormData carrying all fields", async () => {
    const post = vi.spyOn(agentApi, "post").mockResolvedValue({ data: undefined });
    await submitFeedback({
      category: "bug",
      severity: "high",
      urgency: "medium",
      title: "broken thing",
      body: "details",
    });
    expect(post).toHaveBeenCalledTimes(1);
    const [url, body, config] = post.mock.calls[0];
    expect(url).toBe("/feedback");
    expect(body).toBeInstanceOf(FormData);
    const fd = body as FormData;
    expect(fd.get("category")).toBe("bug");
    expect(fd.get("severity")).toBe("high");
    expect(fd.get("urgency")).toBe("medium");
    expect(fd.get("title")).toBe("broken thing");
    expect(fd.get("body")).toBe("details");
    expect(fd.get("screenshot")).toBeNull();
    // Critical: do NOT manually set the Content-Type header on a FormData POST —
    // the browser must populate the multipart boundary, or the server fails to
    // parse the upload. Either omit the header or omit the headers object.
    const headers = (config as { headers?: Record<string, string> } | undefined)
      ?.headers;
    if (headers) {
      expect(headers["Content-Type"]).toBeUndefined();
      expect(headers["content-type"]).toBeUndefined();
    }
  });

  it("attaches a screenshot blob under the 'screenshot' field when provided", async () => {
    const post = vi.spyOn(agentApi, "post").mockResolvedValue({ data: undefined });
    const shot = new Blob([new Uint8Array([1, 2, 3])], { type: "image/png" });
    await submitFeedback({
      category: "feature",
      severity: "low",
      urgency: "low",
      title: "wishlist",
      body: "would be nice",
      screenshot: shot,
    });
    const [, body] = post.mock.calls[0];
    const fd = body as FormData;
    const sent = fd.get("screenshot");
    expect(sent).toBeInstanceOf(Blob);
    expect((sent as Blob).type).toBe("image/png");
  });
});
