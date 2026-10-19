import { beforeEach, describe, expect, it, vi } from "vitest";

const get = vi.fn();
const post = vi.fn();
vi.mock("../client", () => ({
  api: {
    get: (...a: unknown[]) => get(...a),
    post: (...a: unknown[]) => post(...a),
  },
}));

import { applyProposal, fetchProposal, fetchSetupCost, rejectProposal, startSetup } from "../repoSetup";

beforeEach(() => {
  get.mockReset();
  post.mockReset();
});

describe("repo setup API", () => {
  it("reads the recorded cost facts, keeping every segment of a nested repository", async () => {
    const cost = {
      recorded_runs: 3,
      typical_cost_usd: 0.41,
      per_job_budget_usd: 10,
      model: "medium-model",
      paid_from: "Team AI account",
    };
    get.mockResolvedValue({ data: cost });
    await expect(fetchSetupCost("group/sub/project")).resolves.toEqual(cost);
    expect(get).toHaveBeenCalledWith("/repos/group/sub/project/setup/cost");
  });

  it("starts a setup run only with the spend confirmed, and leaves a refusal to the dialog", async () => {
    post.mockResolvedValue({ data: { proposal_id: "prop-abc", job_id: "job-1" } });
    await expect(startSetup("acme/widgets")).resolves.toEqual({ proposal_id: "prop-abc", job_id: "job-1" });
    expect(post).toHaveBeenCalledWith(
      "/repos/acme/widgets/setup/propose",
      { confirm_spend: true },
      { suppressValidationToast: true, suppressErrorToast: true },
    );
  });

  it("reads the latest proposal, and no proposal as null", async () => {
    get.mockResolvedValueOnce({ data: { id: "prop-abc", status: "pending" } });
    await expect(fetchProposal("acme/widgets")).resolves.toEqual({ id: "prop-abc", status: "pending" });
    expect(get).toHaveBeenCalledWith("/repos/acme/widgets/setup/proposal");

    get.mockRejectedValueOnce({ response: { status: 404, data: { detail: "No proposal" } } });
    await expect(fetchProposal("acme/widgets")).resolves.toBeNull();
  });

  it("passes any other failure to read a proposal through", async () => {
    const failure = { response: { status: 500, data: { detail: "boom" } } };
    get.mockRejectedValue(failure);
    await expect(fetchProposal("acme/widgets")).rejects.toBe(failure);
  });

  it("applies with the ticked warning codes, and leaves a refusal to the card", async () => {
    post.mockResolvedValue({ data: { revision_id: 12 } });
    await expect(applyProposal("acme/widgets", "prop-abc", ["deployed_target"])).resolves.toEqual({
      revision_id: 12,
    });
    expect(post).toHaveBeenCalledWith(
      "/repos/acme/widgets/setup/proposal/prop-abc/apply",
      { acknowledged: ["deployed_target"] },
      { suppressValidationToast: true },
    );
  });

  it("turns a proposal down with no body", async () => {
    post.mockResolvedValue({ data: {} });
    await rejectProposal("group/sub/project", "prop-abc");
    expect(post).toHaveBeenCalledWith("/repos/group/sub/project/setup/proposal/prop-abc/reject");
  });
});
