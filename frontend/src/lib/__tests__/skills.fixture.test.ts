import { describe, expect, it } from "vitest";
import fixtureText from "../../../../tests/fixtures/skills/effective-skills.json?raw";
import { effectiveSkills } from "../skills";

interface Case { name: string; base: string[]; deltas: unknown[]; expected: string[] }
const { cases } = JSON.parse(fixtureText) as { cases: Case[] };

describe("effectiveSkills mirrors embry0/agents/skills.py (tests/fixtures/skills/effective-skills.json)", () => {
  it("has cases", () => expect(cases.length).toBeGreaterThan(5));
  for (const c of cases) {
    it(c.name, () => {
      expect(effectiveSkills(c.base, ...c.deltas)).toEqual(c.expected);
    });
  }
});
