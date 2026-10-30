import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * The dashboard names a repository's sub-resources after the `/-/` separator (#257).
 *
 * Without it, `/repos/acme/products/qa-config` is both the repository
 * `acme/products/qa-config` and the QA config of `acme/products`, and the
 * server refuses it (409) while it still answers the old paths. So the
 * repository itself is read at `/repos/acme/products/qa-config/-/`. Every API
 * module builds its `/repos/...` paths with `repoPath`; the scan below fails
 * on a module that builds one by hand.
 */

const { calls, record } = vi.hoisted(() => {
  const calls: [string, string][] = [];
  const record =
    (method: string) =>
    async (url: string): Promise<{ data: unknown }> => {
      calls.push([method, url]);
      return { data: {} };
    };
  return { calls, record };
});
vi.mock("../client", () => ({
  api: { get: record("get"), put: record("put"), post: record("post"), delete: record("delete") },
}));

import { fetchRepoAreas, startMapAreas } from "../areas";
import { fetchRepoCredentials, setRepoCredentials } from "../credentials";
import {
  deleteRepoEnvVar,
  detectRepoEnv,
  fetchRepoEnv,
  fetchResolvedRepoEnv,
  revealSecret,
  setRepoEnv,
} from "../environment";
import { checkQaConfig, deleteQaConfig, fetchQaConfig, saveQaConfig } from "../qaConfig";
import { fetchHistory, undoChange } from "../repoHistory";
import { deleteRepoPreferences, fetchRepoPreferences, setRepoPreferences } from "../repoPreferences";
import { fetchRepoDetail, setRepoActive, setRepoDockerAccess } from "../repos";
import {
  applyProposal,
  fetchProposal,
  fetchReadiness,
  fetchSetupCost,
  rejectProposal,
  skipTesting,
  startSetup,
} from "../repoSetup";
import { fetchSlopMeter } from "../slopMeter";

/** A GitLab subgroup's project named like a sub-resource: the path the separator exists for. */
const REPO = "acme/products/qa-config";

const SUB_RESOURCES: [string, () => Promise<unknown>, string, string][] = [
  ["fetchRepoAreas", () => fetchRepoAreas(REPO), "get", "areas"],
  ["startMapAreas", () => startMapAreas(REPO), "post", "areas/map"],
  ["fetchRepoCredentials", () => fetchRepoCredentials(REPO), "get", "credentials"],
  ["setRepoCredentials", () => setRepoCredentials(REPO, []), "put", "credentials"],
  ["fetchRepoEnv", () => fetchRepoEnv(REPO), "get", "environment"],
  ["setRepoEnv", () => setRepoEnv(REPO, []), "put", "environment"],
  ["deleteRepoEnvVar", () => deleteRepoEnvVar(REPO, "API_URL"), "delete", "environment/API_URL"],
  ["revealSecret", () => revealSecret("repo", "API_TOKEN", REPO), "get", "environment/API_TOKEN/reveal"],
  ["detectRepoEnv", () => detectRepoEnv(REPO), "get", "environment/detect"],
  ["fetchResolvedRepoEnv", () => fetchResolvedRepoEnv(REPO), "get", "environment/resolve"],
  ["fetchQaConfig", () => fetchQaConfig(REPO), "get", "qa-config"],
  ["saveQaConfig", () => saveQaConfig(REPO, "version: 2\n"), "put", "qa-config"],
  ["checkQaConfig", () => checkQaConfig(REPO, "version: 2\n"), "post", "qa-config/check"],
  ["deleteQaConfig", () => deleteQaConfig(REPO), "delete", "qa-config"],
  ["fetchHistory", () => fetchHistory(REPO), "get", "history"],
  ["undoChange", () => undoChange(REPO, 7), "post", "history/7/undo"],
  ["fetchRepoPreferences", () => fetchRepoPreferences(REPO), "get", "preferences"],
  ["setRepoPreferences", () => setRepoPreferences(REPO, {}), "put", "preferences"],
  ["deleteRepoPreferences", () => deleteRepoPreferences(REPO), "delete", "preferences"],
  ["setRepoActive", () => setRepoActive(REPO, false), "put", "active"],
  ["setRepoDockerAccess", () => setRepoDockerAccess(REPO, true), "put", "docker-access"],
  ["fetchReadiness", () => fetchReadiness(REPO), "get", "readiness"],
  ["skipTesting", () => skipTesting(REPO), "post", "setup/skip-testing"],
  ["fetchSetupCost", () => fetchSetupCost(REPO), "get", "setup/cost"],
  ["startSetup", () => startSetup(REPO), "post", "setup/propose"],
  ["fetchProposal", () => fetchProposal(REPO), "get", "setup/proposal"],
  ["applyProposal", () => applyProposal(REPO, "prop-1", []), "post", "setup/proposal/prop-1/apply"],
  ["rejectProposal", () => rejectProposal(REPO, "prop-1"), "post", "setup/proposal/prop-1/reject"],
  ["fetchSlopMeter", () => fetchSlopMeter(REPO), "get", "slop-meter"],
];

/** Every API module but the one that builds the paths. */
const MODULES = import.meta.glob(["../*.ts", "!../repoPath.ts"], {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

/** A `/repos/` path with the repository written into it, as a string or a template: what `repoPath` is for. */
const HAND_BUILT = /["'`]\/repos\/(?:\$\{|["'`]\s*\+)/;

beforeEach(() => {
  calls.length = 0;
});

describe("repository paths (#257)", () => {
  it.each(SUB_RESOURCES)("%s reaches the sub-resource after /-/", async (_name, call, method, subResource) => {
    // Only the request is checked; what the function makes of an empty answer is not.
    await call().catch(() => undefined);
    expect(calls).toEqual([[method, `/repos/${REPO}/-/${subResource}`]]);
  });

  it("reads the repository itself at its own address, the separator with nothing after it", async () => {
    // Without /-/, the path is also the QA config of acme/products, and the server refuses it while old paths last.
    await fetchRepoDetail(REPO);
    expect(calls).toEqual([["get", `/repos/${REPO}/-/`]]);
  });

  it("asks for no repository secret without the repository", async () => {
    await expect(revealSecret("repo", "API_TOKEN")).rejects.toThrow("with its repository");
    expect(calls).toEqual([]);
  });

  it("checks every repository sub-resource the dashboard calls", () => {
    // The scan below would pass on no modules at all.
    expect(Object.keys(MODULES).length).toBeGreaterThan(30);
    expect(SUB_RESOURCES.length).toBe(29);
  });

  it("builds no /repos/ path by hand outside repoPath", () => {
    const offenders = Object.entries(MODULES)
      .filter(([, source]) => HAND_BUILT.test(source))
      .map(([file]) => file);
    expect(offenders, "build these paths with repoPath(fullRepo, subResource)").toEqual([]);
  });
});
