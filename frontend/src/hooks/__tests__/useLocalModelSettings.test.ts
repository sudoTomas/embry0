import { describe, expect, it } from "vitest";
import type { EffectiveSettings } from "@/lib/types/settings";
import { LOCAL_MODELS_SECTION, localMaxRuns } from "@/hooks/useLocalModelSettings";

/** The effective settings with ``sections``, each of one key and its value. */
function effective(sections: Record<string, Record<string, unknown>>): EffectiveSettings {
  return {
    generation: 1,
    sections: Object.entries(sections).map(([name, keys]) => ({
      name,
      summary: "",
      keys: Object.entries(keys).map(([key, value]) => ({
        key,
        value,
        default: null,
        source: "document",
        set_by: null,
        set_at: null,
        overridable: [],
        refused: null,
        description: null,
      })),
    })),
    unknown_sections: [],
    unknown_keys: [],
  } as unknown as EffectiveSettings;
}

describe("how many calls each model server takes at once (local_models.max_runs, #174)", () => {
  it("is the section's max_runs", () => {
    expect(LOCAL_MODELS_SECTION).toBe("local_models");
    expect(localMaxRuns(effective({ local_models: { max_runs: 3 } }))).toBe(3);
  });

  it("is unknown until read, and against a backend without the section", () => {
    expect(localMaxRuns(undefined)).toBeUndefined();
    expect(localMaxRuns(effective({ budget: { max_runs: 3 } })), "another section's key is not the cap").toBeUndefined();
  });

  it("is never a value that is not a number", () => {
    expect(localMaxRuns(effective({ local_models: { max_runs: "3" } }))).toBeUndefined();
    expect(localMaxRuns(effective({ local_models: { max_runs: null } }))).toBeUndefined();
  });
});
