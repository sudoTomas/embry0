import { describe, expect, it } from "vitest";
import { FRAME_COUNTS, SPRITE_STATES } from "../sprites";

describe("sprite states", () => {
  it("names six states and the frames each steps between", () => {
    expect(SPRITE_STATES).toEqual(["idle", "thinking", "tool", "done", "failed", "pending"]);
    expect(FRAME_COUNTS).toEqual({ idle: 2, thinking: 2, tool: 2, done: 1, failed: 1, pending: 1 });
  });
});
