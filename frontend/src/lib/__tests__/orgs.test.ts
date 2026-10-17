import { describe, expect, it } from "vitest";
import { need, org, repo } from "@/components/orgs/__tests__/fixtures";
import { SENSES, credentialLabel, groupNeeds, isMissing, planActions, senseState } from "../orgs";

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
