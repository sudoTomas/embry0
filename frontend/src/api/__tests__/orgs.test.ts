import { beforeEach, describe, expect, it, vi } from "vitest";

const get = vi.fn();
const post = vi.fn();
const patch = vi.fn();
vi.mock("../client", () => ({
  api: {
    get: (...a: unknown[]) => get(...a),
    post: (...a: unknown[]) => post(...a),
    patch: (...a: unknown[]) => patch(...a),
  },
}));

import {
  fetchOrganizationNeeds,
  isMissingEndpoint,
  patchOrganization,
  probeOrganization,
  runOrganizationAction,
} from "../orgs";

const httpError = (status: number, detail: unknown = "x") => ({ response: { status, data: { detail } } });

beforeEach(() => {
  get.mockReset();
  post.mockReset();
  patch.mockReset();
});

describe("organizations API", () => {
  it("calls only FastAPI's own 404 or 405 a missing route", () => {
    expect(isMissingEndpoint(httpError(404, "Not Found"))).toBe(true);
    expect(isMissingEndpoint(httpError(405, "Method Not Allowed"))).toBe(true);
    // A route that exists and answers 404 in its own words is an error to show.
    expect(isMissingEndpoint(httpError(404, "no organization acme"))).toBe(false);
    expect(isMissingEndpoint(httpError(404, "credential not found"))).toBe(false);
    expect(isMissingEndpoint(httpError(405, "Not Found"))).toBe(false);
    expect(isMissingEndpoint(httpError(500, "Not Found"))).toBe(false);
    expect(isMissingEndpoint({ response: { status: 404 } })).toBe(false);
    expect(isMissingEndpoint(new Error("network"))).toBe(false);
  });

  it("reads needs, and answers null when the server has no needs endpoint", async () => {
    get.mockResolvedValueOnce({ data: { owner: "acme", needs: [] } });
    await expect(fetchOrganizationNeeds("acme")).resolves.toEqual({ owner: "acme", needs: [] });
    expect(get).toHaveBeenCalledWith("/orgs/acme/needs");

    get.mockRejectedValueOnce(httpError(404, "Not Found"));
    await expect(fetchOrganizationNeeds("acme")).resolves.toBeNull();
  });

  it("does not hide an app-level 404 behind the missing-endpoint answer", async () => {
    get.mockRejectedValueOnce(httpError(404, "no organization acme"));
    await expect(fetchOrganizationNeeds("acme")).rejects.toEqual(httpError(404, "no organization acme"));
  });

  it("does not hide a server error behind the missing-endpoint answer", async () => {
    get.mockRejectedValueOnce(httpError(500));
    await expect(fetchOrganizationNeeds("acme")).rejects.toEqual(httpError(500));
  });

  it("posts the probe and action bodies of the contract", async () => {
    post.mockResolvedValue({ data: {} });
    await probeOrganization({ owner: "acme", forge: "github", credential_id: "cred-1" });
    expect(post).toHaveBeenCalledWith(
      "/orgs/probe",
      { owner: "acme", forge: "github", credential_id: "cred-1" },
      { suppressValidationToast: true },
    );
    await runOrganizationAction("acme", { action: "hook", repos: ["acme/a"], dry_run: true });
    expect(post).toHaveBeenLastCalledWith(
      "/orgs/acme/actions",
      { action: "hook", repos: ["acme/a"], dry_run: true },
      { suppressValidationToast: true },
    );
  });

  it("patches only the settings it is given, nested under settings, and shows a refusal itself", async () => {
    patch.mockResolvedValue({ data: { owner: "acme", settings: { rediscover_hours: 12 } } });
    await expect(patchOrganization("acme", { rediscover_hours: 12 })).resolves.toEqual({
      owner: "acme",
      settings: { rediscover_hours: 12 },
    });
    expect(patch).toHaveBeenCalledWith("/orgs/acme", { settings: { rediscover_hours: 12 } }, { suppressValidationToast: true });
    await patchOrganization("acme", { rediscover_hours: null });
    expect(patch).toHaveBeenLastCalledWith("/orgs/acme", { settings: { rediscover_hours: null } }, { suppressValidationToast: true });
  });
});
