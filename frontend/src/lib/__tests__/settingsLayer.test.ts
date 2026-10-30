import { describe, it, expect } from "vitest";
import {
  DEPLOYMENT_LAYER,
  layerFromKey,
  layerFromParams,
  layerKey,
  layerOptions,
  layerParams,
  policyLayerPath,
  withLayer,
  writeAccess,
} from "../settingsLayer";
import type { SettingsLayerRow } from "@/lib/types/settings";

const ACME = { layer: "organization", owner: "acme" } as const;
const WIDGETS = { layer: "repository", repo: "acme/widgets" } as const;

function row(over: Partial<SettingsLayerRow>): SettingsLayerRow {
  return {
    layer: "organization",
    owner: "acme",
    repo: null,
    stored_as: null,
    generation: 1,
    updated_by: "alex@example.com",
    updated_at: "2026-10-29T10:00:00Z",
    keys: [],
    ...over,
  };
}

describe("the layer in the URL", () => {
  it("reads the deployment, an organization or a repository, lowercased as the server names them", () => {
    expect(layerFromParams(new URLSearchParams("tab=policy"))).toEqual(DEPLOYMENT_LAYER);
    expect(layerFromParams(new URLSearchParams("tab=policy&owner=Acme"))).toEqual(ACME);
    expect(layerFromParams(new URLSearchParams("tab=policy&repo=Acme/Widgets"))).toEqual(WIDGETS);
    expect(layerFromParams(new URLSearchParams("tab=policy&owner=%20"))).toEqual(DEPLOYMENT_LAYER);
    expect(layerFromParams(new URLSearchParams("owner=globex&repo=acme/widgets")), "repo wins over owner").toEqual(WIDGETS);
  });

  it("writes a layer in, keeping the tab and dropping the other layer parameter", () => {
    const start = new URLSearchParams("tab=policy&owner=acme");
    expect(withLayer(start, WIDGETS).toString()).toBe("tab=policy&repo=acme%2Fwidgets");
    expect(withLayer(start, DEPLOYMENT_LAYER).toString()).toBe("tab=policy");
    expect(start.toString(), "the params it was given are left as they were").toBe("tab=policy&owner=acme");
    expect(layerFromParams(withLayer(start, WIDGETS))).toEqual(WIDGETS);
  });

  it("links to the Policy tab with a layer picked", () => {
    expect(policyLayerPath(ACME)).toBe("/settings?tab=policy&owner=acme");
    expect(policyLayerPath({ layer: "repository", repo: "group/sub/project" })).toBe(
      "/settings?tab=policy&repo=group%2Fsub%2Fproject",
    );
  });

  it("names each layer as the server's scope does, and reads that name back", () => {
    for (const layer of [DEPLOYMENT_LAYER, ACME, WIDGETS]) expect(layerFromKey(layerKey(layer))).toEqual(layer);
    expect(layerKey(ACME)).toBe("organization:acme");
    expect(layerKey(WIDGETS)).toBe("repository:acme/widgets");
  });

  it("sends the layer as the API's query parameter, and none for the deployment", () => {
    expect(layerParams(DEPLOYMENT_LAYER)).toBeUndefined();
    expect(layerParams(ACME)).toEqual({ owner: "acme" });
    expect(layerParams(WIDGETS)).toEqual({ repo: "acme/widgets" });
  });
});

describe("layerOptions", () => {
  it("offers the listed organizations and repositories and every stored layer, marking the stored ones", () => {
    const stored = [
      row({ owner: "acme", keys: ["asks.hours", "asks.timezone"] }),
      row({ owner: "initech", keys: [] }),
      row({ layer: "repository", owner: "acme", repo: "acme/widgets", keys: ["sample.mode"], stored_as: "repository:acme/old-widgets" }),
    ];
    const options = layerOptions(stored, ["Globex", "acme"], ["acme/widgets", "globex/gizmos"], DEPLOYMENT_LAYER);

    expect(options.organizations.map((o) => [o.key, o.label, o.stored])).toEqual([
      ["organization:acme", "acme (layer, 2 keys)", true],
      ["organization:globex", "globex", false],
      // No longer listed, but its layer is stored: it is still offered.
      ["organization:initech", "initech (layer, no keys)", true],
    ]);
    expect(options.repositories.map((o) => [o.key, o.label, o.stored])).toEqual([
      ["repository:acme/widgets", "acme/widgets (layer, 1 key, kept under acme/old-widgets)", true],
      ["repository:globex/gizmos", "globex/gizmos", false],
    ]);
  });

  it("always offers the layer the URL names, before the lists load or when nothing lists it", () => {
    const options = layerOptions([], [], [], { layer: "repository", repo: "umbrella/labs" });
    expect(options.repositories.map((o) => o.key)).toEqual(["repository:umbrella/labs"]);
    expect(layerOptions([], [], [], ACME).organizations.map((o) => o.key)).toEqual(["organization:acme"]);
  });
});

describe("writeAccess", () => {
  it("lets an admin write any document when the key has no allowlist, or the server does not say", () => {
    for (const layer of [DEPLOYMENT_LAYER, ACME, WIDGETS]) {
      expect(writeAccess(true, true, layer)).toEqual({ canWrite: true, reason: null });
      expect(writeAccess(true, undefined, layer).canWrite, "an older server: the tier alone decides").toBe(true);
    }
  });

  it("lets nobody but an admin write, and says so per document", () => {
    expect(writeAccess(false, true, DEPLOYMENT_LAYER)).toEqual({
      canWrite: false,
      reason: "Only an admin can change the settings document.",
    });
    expect(writeAccess(false, true, ACME).reason).toBe("Only an admin can change acme's layer.");
    expect(writeAccess(false, true, WIDGETS).reason).toBe("Only an admin can change acme/widgets's layer.");
  });

  it("refuses an admin key whose allowlist does not cover the document, saying why", () => {
    expect(writeAccess(true, false, WIDGETS)).toEqual({
      canWrite: false,
      reason: "This API key's repository allowlist does not include acme/widgets, so it cannot change its layer.",
    });
    expect(writeAccess(true, false, ACME).reason).toContain("so it cannot change an organization's layer");
    expect(writeAccess(true, false, DEPLOYMENT_LAYER).reason).toContain("so it cannot change the deployment's document");
  });
});
