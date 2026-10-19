// The role the bindings drawer binds each credential kind with, and the scopes
// each role is offered at, pinned to the rules PUT /credentials/bindings
// enforces: a pair the backend refuses (an LLM key at role forge, role llm at
// host scope) fails here instead of on save.
import { describe, expect, it } from "vitest";
import credentialsPy from "../../../../embry0/storage/repositories/credentials.py?raw";
import { ROLE_FOR_KIND, SCOPES_FOR_ROLE } from "../bindingRoles";

/** A `NAME: dict[str, set[str]] = {...}` table of embry0/storage/repositories/credentials.py, values sorted. */
function backendTable(name: string): Record<string, string[]> {
  const body = new RegExp(`^${name}: dict\\[str, set\\[str\\]\\] = \\{\\n([\\s\\S]*?)\\n\\}`, "m").exec(credentialsPy)?.[1];
  if (body === undefined) throw new Error(`${name} not found in embry0/storage/repositories/credentials.py`);
  const rows = [...body.matchAll(/^\s+"([a-z_]+)": \{([^}]*)\},$/gm)];
  expect(rows.length).toBeGreaterThan(0);
  return Object.fromEntries(rows.map((row) => [row[1], [...row[2].matchAll(/"([a-z_]+)"/g)].map((m) => m[1]).sort()]));
}

describe("binding roles match the backend", () => {
  it("binds each credential kind with the one role the backend takes it at", () => {
    const backend = Object.entries(backendTable("KIND_FOR_ROLE")).flatMap(([role, kinds]) =>
      kinds.map((kind) => [kind, role] as const),
    );
    expect(new Set(backend.map(([kind]) => kind)).size).toBe(backend.length);
    expect(ROLE_FOR_KIND).toEqual(Object.fromEntries(backend));
  });

  it("gives each role exactly the scopes the backend takes it at", () => {
    const frontend = Object.fromEntries(
      Object.entries(SCOPES_FOR_ROLE).map(([role, scopes]) => [role, [...scopes].sort()]),
    );
    expect(frontend).toEqual(backendTable("SCOPES_FOR_ROLE"));
  });
});
