import { describe, expect, it } from "vitest";
import source from "../sprites.ts?raw";
import { AVATARS } from "@/lib/agentIdentity";
import { COMPILED, FRAME_COUNTS, SPRITES, SPRITE_STATES, compileFrame } from "../sprites";

const CELL = /^[.ofhe]{16}$/;

describe("sprites", () => {
  it("has every avatar in every state with the specified frame count", () => {
    expect(SPRITE_STATES).toEqual(["idle", "thinking", "tool", "done", "failed", "pending"]);
    expect(FRAME_COUNTS).toEqual({ idle: 2, thinking: 2, tool: 2, done: 1, failed: 1, pending: 1 });
    for (const avatar of AVATARS) {
      for (const state of SPRITE_STATES) {
        expect(SPRITES[avatar][state], `${avatar}.${state}`).toHaveLength(FRAME_COUNTS[state]);
      }
    }
  });

  it("draws every frame as sixteen rows of sixteen cells over . o f h e", () => {
    for (const avatar of AVATARS) {
      for (const state of SPRITE_STATES) {
        SPRITES[avatar][state].forEach((frame, i) => {
          expect(frame, `${avatar}.${state}[${i}]`).toHaveLength(16);
          for (const row of frame) expect(row, `${avatar}.${state}[${i}] ${JSON.stringify(row)}`).toMatch(CELL);
        });
      }
    }
  });

  it("gives every non-pending frame an outline, a fill and (in idle) eyes", () => {
    for (const avatar of AVATARS) {
      for (const state of SPRITE_STATES) {
        COMPILED[avatar][state].forEach((paths, i) => {
          expect(paths.o, `${avatar}.${state}[${i}] outline`).toMatch(/^M/);
          if (state !== "pending") expect(paths.f, `${avatar}.${state}[${i}] fill`).toMatch(/^M/);
        });
      }
      expect(COMPILED[avatar].idle[0].e, `${avatar} idle eyes`).toMatch(/^M/);
    }
  });

  it("alternates the two frames of a two-frame state", () => {
    for (const avatar of AVATARS) {
      for (const state of ["idle", "thinking", "tool"] as const) {
        const [a, b] = SPRITES[avatar][state];
        expect(a.join("\n"), `${avatar}.${state} frames differ`).not.toEqual(b.join("\n"));
      }
    }
  });

  it("compiles runs into one rectangle path per class", () => {
    const frame = ["o.ff............", ...Array.from({ length: 15 }, () => "................")];
    expect(compileFrame(frame)).toEqual({ o: "M0 0h1v1h-1z", f: "M2 0h2v1h-2z" });
  });

  it("stays under the 40 KB source budget (divine CLAUDE.md rule 9)", () => {
    expect(source.length).toBeLessThan(40_000);
  });
});
