import { describe, expect, it } from "vitest";
import { org } from "@/components/orgs/__tests__/fixtures";
import { envTokenKey } from "../orgs";

describe("envTokenKey", () => {
  it("is the variable an environment token is read from, and nothing for a stored credential", () => {
    expect(envTokenKey(org())).toBe("GITHUB_TOKEN__ACME");
    expect(envTokenKey(org({ access: {} }))).toBeNull();
    expect(envTokenKey(org({ credential_id: "cred-1" }))).toBeNull();
  });
});
