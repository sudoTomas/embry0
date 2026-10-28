import { describe, expect, it } from "vitest";
import contractText from "../../../../docs/contracts/progression.json?raw";
import { ICONS } from "@/components/progression/achievementIconData";
import { GEAR_TIERS } from "@/components/roster/appearance/items";
import { RARE_ART } from "@/components/roster/appearance/outfit";
import { fill } from "@/lib/gamePack";

/**
 * docs/contracts/progression.json is generated from the backend's vocabulary
 * (#176): the backend refuses a pack that names art outside it, and the
 * dashboard must draw every piece of it.
 */
const contract = JSON.parse(contractText) as {
  art: { gear_steps: string[]; rare_art: string[]; achievement_icons: string[] };
  template_cases: { text: string; values: Record<string, string | number | boolean>; out: string }[];
};

describe("progression art contract (docs/contracts/progression.json)", () => {
  it("draws every gear step a pack may name, in ladder order", () => {
    expect([...GEAR_TIERS]).toEqual(contract.art.gear_steps);
  });

  it("draws every rare a pack may name", () => {
    expect([...RARE_ART].sort()).toEqual([...contract.art.rare_art].sort());
  });

  it("draws every icon a pack may name, and keeps its own glyph for a secret", () => {
    expect(Object.keys(ICONS).filter((name) => name !== "secret").sort()).toEqual([...contract.art.achievement_icons].sort());
  });
});

describe("the template grammar (embry0/services/progression_templates.py)", () => {
  it("fills every case exactly as the server renders it", () => {
    expect(contract.template_cases.length).toBeGreaterThan(5);
    for (const { text, values, out } of contract.template_cases) expect(fill(text, values), text).toBe(out);
  });

  it("shows a text outside the grammar as written, never half-filled", () => {
    for (const text of ["{n:>9}", "{n!r}", "{0}", "{}", "a lone {", "a lone }", "{n:{n}}"]) {
      expect(fill(text, { n: 5 }), text).toBe(text);
    }
  });
});

