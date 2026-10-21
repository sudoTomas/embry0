import { describe, expect, it } from "vitest";

// Rule 9 (divine/CLAUDE.md): the sprite is the only motion on a card or node.
const sources = import.meta.glob(["../../jobs/*.tsx", "../../logs/*.tsx"], { query: "?raw", import: "default", eager: true }) as Record<string, string>;

describe("the record carries no second animation", () => {
  it("has no animate-pulse or animate-bounce under components/jobs or components/logs", () => {
    const offenders = Object.entries(sources)
      .filter(([, src]) => /animate-(pulse|bounce)/.test(src))
      .map(([path]) => path);
    expect(offenders, "replace the indicator with the sprite's state or a static marker").toEqual([]);
  });
});
