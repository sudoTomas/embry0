import { beforeEach, describe, expect, it, vi } from "vitest";

const get = vi.fn();
const put = vi.fn();
const post = vi.fn();
vi.mock("../client", () => ({
  api: {
    get: (...a: unknown[]) => get(...a),
    put: (...a: unknown[]) => put(...a),
    post: (...a: unknown[]) => post(...a),
  },
}));

import {
  applyAreasProposal,
  fetchAreasProposal,
  fetchPendingAreasProposals,
  previewRepoAreas,
  rejectAreasProposal,
  saveRepoAreas,
  startMapAreas,
} from "../areas";

const YAML = { "Content-Type": "application/yaml" };

beforeEach(() => {
  for (const fn of [get, put, post]) fn.mockReset();
});

describe("areas API (spec 2026-11-10)", () => {
  it("previews and saves the text as written, naming the version the edit started from", async () => {
    put.mockResolvedValue({ data: { generation: 4 } });
    await previewRepoAreas("group/sub/project", "areas: []\n", 3);
    expect(put).toHaveBeenLastCalledWith("/repos/group/sub/project/-/areas", "areas: []\n", {
      headers: { ...YAML, "If-Match": '"3"' },
      suppressValidationToast: true,
      params: { dry_run: 1 },
    });
    await saveRepoAreas("acme/web", "# ours\n", 0);
    expect(put).toHaveBeenLastCalledWith("/repos/acme/web/-/areas", "# ours\n", {
      headers: { ...YAML, "If-Match": '"0"' },
      suppressValidationToast: true,
    });
  });

  it("starts a map with the run's choice and reads its proposal id", async () => {
    post.mockResolvedValue({ data: { job_id: "job-1", repo: "acme/web", proposal_id: "areas-1" } });
    await expect(startMapAreas("acme/web", { model: "claude-sonnet-5" })).resolves.toEqual({
      job_id: "job-1",
      repo: "acme/web",
      proposal_id: "areas-1",
    });
    expect(post.mock.calls[0][0]).toBe("/repos/acme/web/-/areas/map");
  });

  it("reads the newest map, and no map as null", async () => {
    get.mockResolvedValueOnce({ data: { id: "areas-1" } });
    await expect(fetchAreasProposal("acme/web")).resolves.toEqual({ id: "areas-1" });
    expect(get).toHaveBeenLastCalledWith("/repos/acme/web/-/areas/proposal");
    get.mockRejectedValueOnce(Object.assign(new Error("404"), { isAxiosError: true, response: { status: 404 } }));
    await expect(fetchAreasProposal("acme/web")).resolves.toBeNull();
  });

  it("applies and rejects a map by its id, and lists the maps waiting", async () => {
    post.mockResolvedValue({ data: { revision_id: 7 } });
    await expect(applyAreasProposal("acme/web", "areas-1")).resolves.toEqual({ revision_id: 7 });
    expect(post).toHaveBeenLastCalledWith("/repos/acme/web/-/areas/proposal/areas-1/apply");
    await rejectAreasProposal("acme/web", "areas-1");
    expect(post).toHaveBeenLastCalledWith("/repos/acme/web/-/areas/proposal/areas-1/reject");
    get.mockResolvedValue({ data: { proposals: [{ id: "areas-1", repo: "acme/web", created_at: "x" }] } });
    await expect(fetchPendingAreasProposals()).resolves.toEqual([{ id: "areas-1", repo: "acme/web", created_at: "x" }]);
    expect(get).toHaveBeenLastCalledWith("/areas/proposals", { params: { status: "pending" } });
  });
});
