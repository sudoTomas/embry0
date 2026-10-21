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
  fetchOwnerStandards,
  fetchStandardVersions,
  repoPathParam,
  setRepoStandardMode,
  standardApiPath,
  startStandardAudit,
} from "../standards";

beforeEach(() => {
  get.mockReset();
  put.mockReset();
  post.mockReset();
});

describe("standards API", () => {
  it("reads an owner's standards and a kind's versions", async () => {
    get.mockResolvedValueOnce({ data: { owner: "acme", standards: [], repos: [] } });
    await expect(fetchOwnerStandards("acme")).resolves.toEqual({ owner: "acme", standards: [], repos: [] });
    expect(get).toHaveBeenCalledWith("/standards/acme");

    get.mockResolvedValueOnce({ data: { owner: "acme", kind: "design", versions: [{ version: 2 }, { version: 1 }] } });
    await expect(fetchStandardVersions("acme", "design")).resolves.toEqual([{ version: 2 }, { version: 1 }]);
    expect(get).toHaveBeenLastCalledWith("/standards/acme/design/versions");
  });

  it("puts only the mode, so the server keeps the exceptions, and renders a refusal itself", async () => {
    put.mockResolvedValue({ data: { repo: "acme/widgets", mode: "advise" } });
    await setRepoStandardMode("acme", "design", "acme/Widgets.App", "advise");
    expect(put).toHaveBeenCalledWith(
      "/standards/acme/design/repos/acme/Widgets.App",
      { mode: "advise" },
      { suppressValidationToast: true },
    );
  });

  it("posts an audit with no body and keeps every segment of a nested repository", async () => {
    post.mockResolvedValue({ data: { job_id: "job-1" } });
    await expect(startStandardAudit("acme", "design", "acme/group/app")).resolves.toEqual({ job_id: "job-1" });
    expect(post).toHaveBeenCalledWith("/standards/acme/design/audit/acme/group/app", undefined, {
      suppressValidationToast: true,
    });
  });

  it("passes a 409 through with its detail for the page to show", async () => {
    const refusal = { response: { status: 409, data: { detail: "an audit of acme/a is already running: job job-9" } } };
    post.mockRejectedValue(refusal);
    await expect(startStandardAudit("acme", "design", "acme/a")).rejects.toBe(refusal);
  });

  it("encodes each path segment and names the path an admin writes a version to", () => {
    expect(repoPathParam("acme/a b#c")).toBe("acme/a%20b%23c");
    expect(standardApiPath("acme", "design")).toBe("/api/v1/standards/acme/design");
  });
});
