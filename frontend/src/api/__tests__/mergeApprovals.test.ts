import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("../client", () => ({ api: { get: vi.fn(), post: vi.fn() } }));

import { api } from "../client";
import { decideMergeApproval, fetchMergeApproval, fetchMergeApprovals, fetchPendingMergeApprovals } from "../mergeApprovals";
import { detailSentence } from "@/lib/mergeApprovals";

const get = api.get as ReturnType<typeof vi.fn>;
const post = api.post as ReturnType<typeof vi.fn>;

beforeEach(() => vi.clearAllMocks());

describe("merge approvals api", () => {
  it("lists one status with the route's own filter and reads a 503 as the feature being off", async () => {
    get.mockResolvedValueOnce({ status: 200, data: { items: [{ id: "ma-1" }], mode: "dry_run" } });
    await expect(fetchMergeApprovals("pending")).resolves.toEqual({ items: [{ id: "ma-1" }], mode: "dry_run" });
    expect(get.mock.calls[0][1].params).toEqual({ status: "pending", limit: 200 });
    get.mockResolvedValueOnce({ status: 503, data: { detail: "off" } });
    await expect(fetchMergeApprovals("all")).resolves.toBeNull();
    expect(get.mock.calls[1][1].params).toEqual({ limit: 200 });
  });

  it("keeps the pending read the brief bar uses", async () => {
    get.mockResolvedValueOnce({ status: 200, data: { items: [{ id: "ma-1" }] } });
    await expect(fetchPendingMergeApprovals()).resolves.toEqual([{ id: "ma-1" }]);
  });

  it("reads one ask by id, and its 503 as the feature being off", async () => {
    get.mockResolvedValueOnce({ status: 200, data: { id: "ma-1", repo: "acme/widgets" } });
    await expect(fetchMergeApproval("ma-1")).resolves.toEqual({ id: "ma-1", repo: "acme/widgets" });
    expect(get).toHaveBeenCalledWith("/merge-approvals/ma-1", expect.objectContaining({ validateStatus: expect.any(Function) }));
    get.mockResolvedValueOnce({ status: 503, data: { detail: "off" } });
    await expect(fetchMergeApproval("ma-1")).resolves.toBeNull();
  });

  it("decides with the dashboard's form: the decision word alone", async () => {
    post.mockResolvedValueOnce({ data: { outcome: "rejected", status: "rejected", dry_run: true, pr_url: "", head_sha: "" } });
    await expect(decideMergeApproval("ma-1", "reject")).resolves.toMatchObject({ outcome: "rejected" });
    expect(post).toHaveBeenCalledWith("/merge-approvals/ma-1/decide", { decision: "reject" }, expect.anything());
  });
});

describe("detailSentence", () => {
  it("capitalises only the first character and ends the sentence, keeping the service's own spelling", () => {
    expect(detailSentence("could not read the PR from GitHub; try again")).toBe("Could not read the PR from GitHub; try again.");
    expect(detailSentence("The squash-merge failed.")).toBe("The squash-merge failed.");
    expect(detailSentence("head moved; ask again?")).toBe("Head moved; ask again?");
  });
});
