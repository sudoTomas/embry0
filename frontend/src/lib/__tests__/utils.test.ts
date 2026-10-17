import { describe, it, expect } from "vitest";
import { cn, highlightJson } from "../utils";

describe("cn", () => {
  it("merges class names", () => {
    expect(cn("foo", "bar")).toBe("foo bar");
  });

  it("handles conditional classes", () => {
    const isHidden = false;
    expect(cn("base", isHidden && "hidden", "extra")).toBe("base extra");
  });
});

describe("highlightJson", () => {
  it("reassembles to the input text unchanged", () => {
    const json = JSON.stringify({ a: 1, b: "x", c: [true, null] }, null, 2);
    const text = highlightJson(json).map((s) => s.text).join("");
    expect(text).toBe(json);
  });
});
