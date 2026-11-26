// The provider catalogue's overlay as the Providers page's form edits and writes it (#173, plan item 6). What it
// writes is pinned in ./fixtures/catalogue_overlay_yaml.json, inside the frontend so its image builds with it, which
// the backend reads back too
// (tests/unit/agents/test_catalogue_overlay_yaml.py), so the two cannot drift apart.
import { describe, expect, it } from "vitest";
import shared from "./fixtures/catalogue_overlay_yaml.json";
import {
  hasComments,
  hasDatedPrices,
  modelSource,
  overlayYaml,
  plainDecimal,
  plainPrice,
  providerView,
  withEnabled,
  withModel,
  withModelTakenOut,
  withoutModelEntry,
  DATE_KEYS,
  EXTRAS_KEYS,
  WHOLE_NUMBER_KEYS,
  type CatalogueOverlay,
} from "../catalogueOverlay";

describe("overlayYaml", () => {
  // Each list is the fixture's, which the backend checks against the overlay's schema: the writer decides by name.
  const byName: [string, ReadonlySet<string> | undefined][] = [
    ["whole_number_keys", WHOLE_NUMBER_KEYS],
    ["date_keys", DATE_KEYS],
    ["json_keys", EXTRAS_KEYS],
  ];
  for (const [key, written] of byName) {
    it(`writes by name exactly the fields the fixture's ${key} names, which the backend checks`, () => {
      const listed = (shared as Record<string, unknown>)[key] as string[] | undefined;
      expect(listed, `the shared fixture names no ${key}`).toBeDefined();
      expect([...(written ?? [])].sort()).toEqual([...(listed ?? [])].sort());
    });
  }

  for (const sample of shared.cases) {
    it(`writes ${sample.name} as the catalogue reads it`, () => {
      expect(overlayYaml(sample.overlay as CatalogueOverlay)).toBe(sample.yaml);
    });
  }
});

describe("the form's edits", () => {
  const shippedOff = false;

  it("turns a provider on, and back to what embry0 ships leaves no key", () => {
    const on = withEnabled({}, "deepseek", true, shippedOff);
    expect(on).toEqual({ providers: { deepseek: { enabled: true } } });
    expect(withEnabled(on, "deepseek", false, shippedOff)).toEqual({});
  });

  it("never changes the overlay it was made from", () => {
    const before: CatalogueOverlay = { providers: { deepseek: { enabled: true } } };
    withEnabled(before, "deepseek", false, shippedOff);
    withModel(before, "deepseek", "deepseek-flash", { price: { in: 1, out: 2 }, free: false, chatOnly: false });
    expect(before).toEqual({ providers: { deepseek: { enabled: true } } });
  });

  it("prices a model or makes it free, never both, and keeps what the form does not edit", () => {
    const start: CatalogueOverlay = {
      providers: { custom: { models: { "custom/m": { free: true, request_extras: { temperature: 0.1 } } } } },
    };
    const priced = withModel(start, "custom", "custom/m", { price: { in: 1, out: 2 }, free: false, chatOnly: true });
    expect(priced.providers!.custom.models!["custom/m"]).toEqual({
      prices: [{ in: 1, out: 2 }],
      chat_only: true,
      request_extras: { temperature: 0.1 },
    });
    const free = withModel(priced, "custom", "custom/m", { price: null, free: true, chatOnly: false });
    expect(free.providers!.custom.models!["custom/m"]).toEqual({ free: true, request_extras: { temperature: 0.1 } });
  });

  it("resets a shipped model, takes one out, and says where each comes from", () => {
    const shipped = { "deepseek-flash": {} };
    const changed = withModel({}, "deepseek", "deepseek-flash", { price: { in: 1, out: 2 }, free: false, chatOnly: false });
    expect(modelSource(changed, "deepseek", "deepseek-flash", shipped)).toBe("changed");
    expect(modelSource(withoutModelEntry(changed, "deepseek", "deepseek-flash"), "deepseek", "deepseek-flash", shipped)).toBe(
      "shipped",
    );
    expect(withoutModelEntry(changed, "deepseek", "deepseek-flash")).toEqual({});
    const out = withModelTakenOut({}, "deepseek", "deepseek-flash");
    expect(out).toEqual({ providers: { deepseek: { models: { "deepseek-flash": null } } } });
    expect(modelSource(out, "deepseek", "deepseek-flash", shipped)).toBe("taken out");
    const added = withModel({}, "deepseek", "deepseek-new", { price: { in: 1, out: 2 }, free: false, chatOnly: false });
    expect(modelSource(added, "deepseek", "deepseek-new", shipped)).toBe("added");
  });

  it("tells prices the form cannot edit without losing some", () => {
    expect(hasDatedPrices({ prices: [{ in: 1, out: 2 }] })).toBe(false);
    expect(hasDatedPrices({ prices: [{ in: 1, out: 2, from: "2026-12-01" }] })).toBe(true);
    expect(hasDatedPrices({ prices: [{ in: 1, out: 2 }, { in: 2, out: 4, from: "2026-12-01" }] })).toBe(true);
  });

  it("knows a text with comments, which a write from the values does not keep", () => {
    expect(hasComments("# why\nproviders: {}\n")).toBe(true);
    expect(hasComments("providers:\n  deepseek: {enabled: true}  # for light\n")).toBe(true);
    expect(hasComments("providers:\n  deepseek: {enabled: true}\n")).toBe(false);
  });
});

describe("providerView", () => {
  const shipped = {
    runtime: "chat_completions",
    enabled: false,
    models: {
      "deepseek-flash": { prices: [{ in: 0.3, out: 1.2 }], free: false, chat_only: false, request_extras: {} },
      "deepseek-pro": { prices: [{ in: 1.3, out: 4 }], free: false, chat_only: true, request_extras: {} },
    },
  };

  it("is what embry0 ships with no overlay", () => {
    const view = providerView(shipped, undefined);
    expect(view.enabled).toBe(false);
    expect(view.models.map((row) => [row.id, row.source])).toEqual([
      ["deepseek-flash", "shipped"],
      ["deepseek-pro", "shipped"],
    ]);
  });

  it("merges the overlay's changes, additions and removals as the catalogue does", () => {
    const view = providerView(shipped, {
      enabled: true,
      models: {
        "deepseek-flash": { free: true },
        "deepseek-pro": null,
        "deepseek-new": { prices: [{ in: 1, out: 2 }] },
      },
    });
    expect(view.enabled).toBe(true);
    const byId = Object.fromEntries(view.models.map((row) => [row.id, row]));
    expect(byId["deepseek-flash"]).toMatchObject({ source: "changed", model: { free: true, prices: [] } });
    expect(byId["deepseek-pro"]).toEqual({ id: "deepseek-pro", model: null, source: "taken out" });
    expect(byId["deepseek-new"]).toMatchObject({ source: "added", model: { prices: [{ in: 1, out: 2 }], free: false } });
    expect(view.models.map((row) => row.id)).toEqual(["deepseek-flash", "deepseek-pro", "deepseek-new"]);
  });

  it("gives the form a plain price only where it can edit one", () => {
    expect(plainPrice({ prices: [{ in: 1, out: 2 }], free: false, chat_only: false, request_extras: {} })).toEqual({
      in: 1,
      out: 2,
    });
    expect(plainPrice({ prices: [], free: true, chat_only: false, request_extras: {} })).toBeNull();
    expect(
      plainPrice({ prices: [{ in: 1, out: 2, from: "2026-12-01" }], free: false, chat_only: false, request_extras: {} }),
    ).toBeNull();
  });
});

describe("plainDecimal", () => {
  it("never writes an exponent, which the catalogue's loader would read as a string", () => {
    expect([1e-7, 1.5e-7, 2.5e-10, 1e21, 1.25e22, 0.15, 3, -1e-7].map(plainDecimal)).toEqual([
      "0.0000001",
      "0.00000015",
      "0.00000000025",
      "1000000000000000000000",
      "12500000000000000000000",
      "0.15",
      "3",
      "-0.0000001",
    ]);
  });
});
