// Which settings document the Policy tab shows (spec 2026-10-29): the
// deployment's, an organization's layer or a repository's. The choice rides
// in the URL (`/settings?tab=policy&owner=acme`, `&repo=acme/widgets`), so a
// layer can be linked to and a reload keeps it. Pure: no React, no requests.

import type { SettingsLayerRow, SettingsScope } from "@/lib/types/settings";

/** The document the tab works on. */
export type PolicyLayer =
  | { layer: "deployment" }
  | { layer: "organization"; owner: string }
  | { layer: "repository"; repo: string };

export const DEPLOYMENT_LAYER: PolicyLayer = { layer: "deployment" };

/** The query parameters naming a layer on every settings route: none for the deployment. */
export function layerParams(layer: PolicyLayer): { owner: string } | { repo: string } | undefined {
  if (layer.layer === "organization") return { owner: layer.owner };
  if (layer.layer === "repository") return { repo: layer.repo };
  return undefined;
}

/**
 * The layer a URL names. The server lowercases owners and repositories, so
 * the URL's are too, and `Acme` picks the same layer as `acme`. `repo` wins
 * over `owner` when a hand-written URL has both; the next pick drops the
 * other.
 */
export function layerFromParams(params: URLSearchParams): PolicyLayer {
  const repo = params.get("repo")?.trim().toLowerCase();
  if (repo) return { layer: "repository", repo };
  const owner = params.get("owner")?.trim().toLowerCase();
  if (owner) return { layer: "organization", owner };
  return DEPLOYMENT_LAYER;
}

/** `params` with the layer written in: the other parameters, the open tab among them, kept. */
export function withLayer(params: URLSearchParams, layer: PolicyLayer): URLSearchParams {
  const next = new URLSearchParams(params);
  next.delete("owner");
  next.delete("repo");
  if (layer.layer === "organization") next.set("owner", layer.owner);
  if (layer.layer === "repository") next.set("repo", layer.repo);
  return next;
}

/** The Policy tab with a layer picked, for a link from an organization's or a repository's page. */
export function policyLayerPath(layer: PolicyLayer): string {
  const params = withLayer(new URLSearchParams({ tab: "policy" }), layer);
  return `/settings?${params.toString()}`;
}

/** One string per layer, as the server names its scope (`organization:acme`): a query key, a React key, a select value. */
export function layerKey(layer: PolicyLayer): string {
  if (layer.layer === "organization") return `organization:${layer.owner}`;
  if (layer.layer === "repository") return `repository:${layer.repo}`;
  return "deployment";
}

/** The layer a `layerKey` names; the deployment for anything else. */
export function layerFromKey(key: string): PolicyLayer {
  if (key.startsWith("organization:")) return { layer: "organization", owner: key.slice("organization:".length) };
  if (key.startsWith("repository:")) return { layer: "repository", repo: key.slice("repository:".length) };
  return DEPLOYMENT_LAYER;
}

/** The layer as a scope the API answers names it. */
export function layerOfScope(scope: SettingsScope): PolicyLayer {
  if (scope.layer === "repository" && scope.repo) return { layer: "repository", repo: scope.repo };
  if (scope.layer === "organization" && scope.owner) return { layer: "organization", owner: scope.owner };
  return DEPLOYMENT_LAYER;
}

/** The layer's own name in a sentence: "the deployment", "acme", "acme/widgets". */
export function layerName(layer: PolicyLayer | SettingsScope): string {
  if (layer.layer === "organization") return "owner" in layer && layer.owner ? layer.owner : "the organization";
  if (layer.layer === "repository") return "repo" in layer && layer.repo ? layer.repo : "the repository";
  return "the deployment";
}

/** The document itself in a sentence: "the deployment's document", "acme's layer". */
export function documentName(layer: PolicyLayer | SettingsScope): string {
  return layer.layer === "deployment" ? "the deployment's document" : `${layerName(layer)}'s layer`;
}

/** Who a layer is, as the subject of a rule: "An organization", "A repository". */
export const LAYER_NOUNS = { organization: "An organization", repository: "A repository" } as const;

/** One choice in the layer picker. */
export interface LayerOption {
  key: string;
  layer: PolicyLayer;
  /** What the option reads: the name, and whether a layer is stored and what it sets. */
  label: string;
  /** A layer is stored for it (possibly cleared to no keys). */
  stored: boolean;
}

export interface LayerOptions {
  organizations: LayerOption[];
  repositories: LayerOption[];
}

function stamp(name: string, row: SettingsLayerRow | undefined): string {
  if (!row) return name;
  const count = row.keys.length;
  const sets = count === 0 ? "layer, no keys" : `layer, ${count} ${count === 1 ? "key" : "keys"}`;
  const kept = row.stored_as ? `, kept under ${row.stored_as.replace(/^repository:/, "")}` : "";
  return `${name} (${sets}${kept})`;
}

/**
 * The picker's choices: the organizations and repositories the lists show
 * (a removed organization and an inactive repository are hidden from lists,
 * as everywhere), and every stored layer, whether or not what it belongs
 * to is still listed or registered (a layer outlives both). Each is marked
 * when a layer is stored, with how many keys it sets. The layer the URL
 * names is always a choice, so the picker shows what the page shows even
 * before the lists load, or for a name nothing lists.
 */
export function layerOptions(
  stored: SettingsLayerRow[],
  organizations: string[],
  repositories: string[],
  selected: PolicyLayer,
): LayerOptions {
  const orgRows = new Map(stored.filter((row) => row.layer === "organization" && row.owner).map((row) => [row.owner!, row]));
  const repoRows = new Map(stored.filter((row) => row.layer === "repository" && row.repo).map((row) => [row.repo!, row]));

  const orgNames = new Set([...organizations.map((o) => o.toLowerCase()), ...orgRows.keys()]);
  if (selected.layer === "organization") orgNames.add(selected.owner);
  const repoNames = new Set([...repositories.map((r) => r.toLowerCase()), ...repoRows.keys()]);
  if (selected.layer === "repository") repoNames.add(selected.repo);

  const byName = (a: string, b: string) => a.localeCompare(b);
  return {
    organizations: [...orgNames].sort(byName).map((owner) => ({
      key: `organization:${owner}`,
      layer: { layer: "organization", owner },
      label: stamp(owner, orgRows.get(owner)),
      stored: orgRows.has(owner),
    })),
    repositories: [...repoNames].sort(byName).map((repo) => ({
      key: `repository:${repo}`,
      layer: { layer: "repository", repo },
      label: stamp(repo, repoRows.get(repo)),
      stored: repoRows.has(repo),
    })),
  };
}


/** Whether this caller may change a document, and why not in words. */
export interface WriteAccess {
  canWrite: boolean;
  reason: string | null;
}

/**
 * What the server lets this caller write, as every write checks it: the
 * admin tier, and for a key with a repository allowlist only the layers of
 * the repositories it covers (`allowlist_covers`, which an older server
 * does not send: the tier alone decides then).
 */
export function writeAccess(isAdmin: boolean, allowlistCovers: boolean | undefined, layer: PolicyLayer): WriteAccess {
  if (!isAdmin) {
    return {
      canWrite: false,
      reason:
        layer.layer === "deployment"
          ? "Only an admin can change the settings document."
          : `Only an admin can change ${documentName(layer)}.`,
    };
  }
  if (allowlistCovers === false) {
    return {
      canWrite: false,
      reason:
        layer.layer === "repository"
          ? `This API key's repository allowlist does not include ${layer.repo}, so it cannot change its layer.`
          : `This API key is limited to a list of repositories, so it cannot change ${
              layer.layer === "deployment" ? "the deployment's document" : "an organization's layer"
            }: only the layers of the repositories on its list.`,
    };
  }
  return { canWrite: true, reason: null };
}
