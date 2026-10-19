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

  it("derives pending as the outline of idle alone", () => {
    for (const avatar of AVATARS) {
      for (const row of SPRITES[avatar].pending[0]) expect(row, `${avatar}.pending ${JSON.stringify(row)}`).toMatch(/^[.o]{16}$/);
    }
  });

  it("closes the eyes and adds one row-0 spark on thinking frame 0, moving it on frame 1", () => {
    const count = (cells: string | readonly string[], c: string) => [...(typeof cells === "string" ? cells : cells.join(""))].filter((x) => x === c).length;
    for (const avatar of AVATARS) {
      const [idle] = SPRITES[avatar].idle;
      const [t0, t1] = SPRITES[avatar].thinking;
      expect(count(t0, "e"), `${avatar}.thinking[0] eyes closed`).toBe(0);
      expect(count(t0, "h"), `${avatar}.thinking[0] one extra h`).toBe(count(idle, "h") + 1);
      expect(count(t0[0], "h"), `${avatar}.thinking[0] spark in row 0`).toBe(1);
      expect(t1[0].indexOf("h"), `${avatar}.thinking spark moves`).not.toBe(t0[0].indexOf("h"));
      expect(count(t1, "e"), `${avatar}.thinking[1] eyes open`).toBeGreaterThan(0);
    }
  });

  it("bobs idle frame 1 up one row", () => {
    for (const avatar of AVATARS) {
      const [a, b] = SPRITES[avatar].idle;
      expect(b, `${avatar}.idle[1]`).toEqual([...a.slice(1), "................"]);
    }
  });

  it("compiles runs into one rectangle path per class", () => {
    const frame = ["o.ff............", ...Array.from({ length: 15 }, () => "................")];
    expect(compileFrame(frame)).toEqual({ o: "M0 0h1v1h-1z", f: "M2 0h2v1h-2z" });
  });

  it("stays under the 40 KB source budget (divine CLAUDE.md rule 9)", () => {
    expect(source.length).toBeGreaterThan(10_000);
    expect(source.length).toBeLessThan(40_000);
  });
});
