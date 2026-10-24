import { describe, expect, it } from "vitest";
import { need, org, repo } from "@/components/orgs/__tests__/fixtures";
import {
  SENSES,
  actionLabel,
  contextAuditDays,
  credentialLabel,
  groupNeeds,
  isMissing,
  needKindLabel,
  planActions,
  rediscoverHours,
  senseState,
} from "../orgs";

const sense = (key: string) => SENSES.find((s) => s.key === key)!;

describe("groupNeeds", () => {
  it("groups by kind in vocabulary order, counting distinct repositories and open needs", () => {
    const groups = groupNeeds([
      need("n1", "no_webhook", ["acme/a", "acme/b"]),
      need("n2", "not_registered", ["acme/a"]),
      need("n3", "no_webhook", ["acme/b", "acme/c"], { status: "dismissed" }),
      need("n4", "something_new", ["acme/z"]),
    ]);
    expect(groups.map((g) => g.kind)).toEqual(["not_registered", "no_webhook", "something_new"]);
    const hooks = groups[1];
    expect(hooks.label).toBe("No webhook");
    expect(hooks.needs).toHaveLength(2);
    expect(hooks.repoCount).toBe(3);
    expect(hooks.openCount).toBe(1);
    expect(groups[2].label).toBe("something new");
  });

  it("returns nothing for no needs", () => {
    expect(groupNeeds([])).toEqual([]);
  });
});

describe("planActions", () => {
  it("makes one call per action with the union of repositories, registering first", () => {
    const plan = planActions([
      need("n1", "no_webhook", ["acme/b"]),
      need("n2", "not_registered", ["acme/b", "acme/a"]),
      need("n3", "no_webhook", ["acme/a", "acme/b"]),
    ]);
    expect(plan).toEqual([
      { action: "register", repos: ["acme/a", "acme/b"] },
      { action: "hook", repos: ["acme/a", "acme/b"] },
    ]);
  });

  it("deactivates last, after every action that needs a registration", () => {
    const plan = planActions([
      need("v1", "repo_vanished", ["acme/gone"], { action: { type: "deactivate", params: {} } }),
      need("w1", "not_watched", ["acme/a"]),
      need("r1", "not_registered", ["acme/b"]),
    ]);
    expect(plan.map((p) => p.action)).toEqual(["register", "watch", "deactivate"]);
  });

  it("leaves out needs done by hand and needs that are not open", () => {
    const plan = planActions([
      need("n1", "access_limited", ["acme/a"]),
      need("n2", "not_registered", ["acme/b"], { status: "dismissed" }),
    ]);
    expect(plan).toEqual([]);
  });
});

describe("senses", () => {
  it("reads true, false and an unseen fact", () => {
    expect(senseState(repo("a", { registered: true }), sense("registry"))).toBe("on");
    expect(senseState(repo("a", { registered: false }), sense("registry"))).toBe("off");
    expect(senseState(repo("a", { hook_present: null }), sense("intake"))).toBe("unknown");
  });

  it("counts a boot hint as present only when it says something", () => {
    expect(senseState(repo("a", { boot_hint: "npm run dev" }), sense("boot"))).toBe("on");
    expect(senseState(repo("a", { boot_hint: null }), sense("boot"))).toBe("off");
    expect(senseState(repo("a", { boot_hint: null, root_visible: false }), sense("boot"))).toBe("unknown");
  });

  it("treats an archived repository as missing nothing", () => {
    expect(isMissing(repo("a"), "registry")).toBe(true);
    expect(isMissing(repo("a", {}, { archived: true }), "registry")).toBe(false);
    expect(isMissing(repo("a", { registered: true }), "registry")).toBe(false);
  });
});

describe("credentialLabel", () => {
  it("names a registry credential by label and an environment token by key", () => {
    const credentials = [{ id: "cred-1", label: "ci bot" }] as never;
    expect(credentialLabel(org({ credential_id: "cred-1" }), credentials)).toBe("ci bot");
    expect(credentialLabel(org({ credential_id: "cred-9" }), credentials)).toBe("cred-9");
    expect(credentialLabel(org(), undefined)).toBe("environment (GITHUB_TOKEN__ACME)");
    expect(credentialLabel(org({ access: {} }), undefined)).toBe("environment token");
  });
});

describe("labels", () => {
  it("names the repositories gone from the forge and every action in words", () => {
    expect(needKindLabel("repo_vanished")).toBe("Repositories gone from the forge");
    expect(actionLabel("deactivate")).toBe("Deactivate");
    expect(actionLabel("hook")).toBe("Add webhook");
    expect(actionLabel("something_new")).toBe("something_new");
  });
});

describe("rediscoverHours", () => {
  it("reads a whole number of hours in range, and anything else as off", () => {
    expect(rediscoverHours(org({ settings: { rediscover_hours: 24 } }))).toBe(24);
    expect(rediscoverHours(org({ settings: { rediscover_hours: 1 } }))).toBe(1);
    expect(rediscoverHours(org({ settings: { rediscover_hours: 168 } }))).toBe(168);
    expect(rediscoverHours(org({ settings: { rediscover_hours: null } }))).toBeNull();
    expect(rediscoverHours(org({ settings: {} }))).toBeNull();
    expect(rediscoverHours(org({ settings: { rediscover_hours: 0 } }))).toBeNull();
    expect(rediscoverHours(org({ settings: { rediscover_hours: 169 } }))).toBeNull();
    expect(rediscoverHours(org({ settings: { rediscover_hours: 1.5 } }))).toBeNull();
    expect(rediscoverHours(org({ settings: { rediscover_hours: true } }))).toBeNull();
  });
});

describe("contextAuditDays", () => {
  it("reads a whole number of days in range, and anything else as off", () => {
    expect(contextAuditDays(org({ settings: { context_audit_days: 7 } }))).toBe(7);
    expect(contextAuditDays(org({ settings: { context_audit_days: 1 } }))).toBe(1);
    expect(contextAuditDays(org({ settings: { context_audit_days: 365 } }))).toBe(365);
    expect(contextAuditDays(org({ settings: { context_audit_days: null } }))).toBeNull();
    expect(contextAuditDays(org({ settings: {} }))).toBeNull();
    expect(contextAuditDays(org({ settings: { context_audit_days: 0 } }))).toBeNull();
    expect(contextAuditDays(org({ settings: { context_audit_days: 366 } }))).toBeNull();
    expect(contextAuditDays(org({ settings: { context_audit_days: 1.5 } }))).toBeNull();
    expect(contextAuditDays(org({ settings: { context_audit_days: "7" } }))).toBeNull();
    expect(contextAuditDays(org({ settings: { rediscover_hours: 24 } }))).toBeNull();
  });
});
