import { describe, expect, it } from "vitest";
import { identityOf, resolveAgentType } from "../agentIdentity";

describe("identityOf", () => {
  it("reads the row", () => {
    const id = identityOf({ type: "release-bot", display_name: "Release Bot", avatar: "rogue", color: "cyan", tagline: "Ships it." });
    expect(id).toMatchObject({ displayName: "Release Bot", avatar: "rogue", color: "cyan", tagline: "Ships it.", hex: "#22d3ee", initial: "R" });
  });
  it("falls back to the builtin table for a row that predates identity", () => {
    expect(identityOf({ type: "developer" })).toMatchObject({ displayName: "Developer", avatar: "smith", color: "amber" });
    expect(identityOf("review")).toMatchObject({ displayName: "Reviewer", avatar: "warden", color: "rose" });
  });
  it("falls back to the slug, golem and slate for an unknown type", () => {
    expect(identityOf("release-bot")).toMatchObject({ displayName: "release-bot", avatar: "golem", color: "slate", tagline: "", initial: "R" });
  });
  it("ignores ids outside the roster and palette", () => {
    expect(identityOf({ type: "x", avatar: "dragon", color: "mauve" })).toMatchObject({ avatar: "golem", color: "slate" });
  });
  it("shows the slug when display_name is empty", () => {
    expect(identityOf({ type: "qa", display_name: "" }).displayName).toBe("QA");
    expect(identityOf({ type: "bot", display_name: "" }).displayName).toBe("bot");
  });
  it("never yields an empty initial", () => {
    expect(identityOf({ type: "", display_name: "  " }).initial).toBe("?");
  });
});

describe("resolveAgentType", () => {
  it("maps the template spelling reviewer to the review row and leaves the rest", () => {
    expect(resolveAgentType("reviewer")).toBe("review");
    expect(resolveAgentType("review")).toBe("review");
    expect(resolveAgentType("developer")).toBe("developer");
  });
});
