import { describe, expect, it } from "vitest";
import { matchRoutes } from "react-router";
import { ORG_WIZARD_PATH } from "@/lib/orgs";
import { router } from "../index";

const leaf = (path: string) => matchRoutes(router.routes, path)?.at(-1);

describe("organization routes", () => {
  it("gives an owner named new its organization page", () => {
    const match = leaf("/orgs/new");
    expect(match?.route.path).toBe("orgs/:owner");
    expect(match?.params.owner).toBe("new");
  });

  it("puts the wizard at a path no owner can take", () => {
    expect(leaf(ORG_WIZARD_PATH)?.route.path).toBe("orgs/-/new");
    expect(leaf("/orgs/acme")?.route.path).toBe("orgs/:owner");
    expect(leaf("/orgs")?.route.path).toBe("orgs");
  });
});
