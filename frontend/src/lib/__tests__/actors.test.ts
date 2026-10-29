import { describe, expect, it } from "vitest";
import { SOMEONE_ELSE, actorInText, actorLabel } from "../actors";

describe("actors", () => {
  it("names the actor when the API names one", () => {
    expect(actorLabel("ada@example.com")).toBe("ada@example.com");
    expect(actorInText("ada@example.com")).toBe("ada@example.com");
  });

  it("says someone else when the API hides the actor, or does not know one", () => {
    for (const hidden of [null, undefined, ""]) {
      expect(actorLabel(hidden)).toBe(SOMEONE_ELSE);
      expect(actorInText(hidden)).toBe("someone else");
    }
  });
});
