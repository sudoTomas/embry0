import { api } from "./client";
import type { CatalogueOverlay, CatalogueProvider } from "@/lib/catalogueOverlay";

/** A problem the catalogue names: where it is and what is wrong, never the value. */
export interface CatalogueProblem {
  loc: (string | number)[];
  type: string;
  msg: string;
}

/** Why the values stored with the overlay stay in force, not its text as this release reads it. */
export interface TextNotReadAgain {
  /**
   * `unreadable`: the text does not read; `differs`: it reads to other values than those stored with it (not only a
   * number in exponent form that was stored as text); `refused`: it reads to a part the catalogue refuses.
   */
  reason: "unreadable" | "differs" | "refused";
  /** Where the text does not read, or each part refused; never a value. */
  problems: CatalogueProblem[];
}

/** `GET /config/providers/catalog`: the deployment's overlay and the catalogue in force. Any key reads it. */
export interface ProviderCatalogueDocument {
  /** The overlay as written, comments included; empty: the catalogue embry0 ships, as it is. */
  yaml_text: string;
  /** The overlay's values in force, which the form edits: its text's as this release reads it, or the stored ones. */
  data: CatalogueOverlay;
  /** The version a write names in `If-Match`. */
  generation: number;
  updated_by: string | null;
  updated_at: string | null;
  /** The catalogue this deployment runs on: the shipped one with the overlay merged in. */
  catalogue: { version: number; providers: Record<string, CatalogueProvider> };
  /** The catalogue this release ships, which the overlay changes. */
  shipped: { version: number; providers: Record<string, CatalogueProvider> };
  in_force: "whole" | "in_part" | "earlier";
  in_force_generation: number | null;
  /** Each part of the stored overlay this release does not run on. */
  refused: CatalogueProblem[];
  notes: CatalogueProblem[];
  /** Set when the values stored with the overlay stay in force; an orchestrator of an earlier release leaves it out. */
  text_not_read_again?: TextNotReadAgain | null;
}

/** What a write would change, or changed: the overlay's text before and after, as a unified diff. */
export interface CatalogueWriteResult {
  generation: number;
  changed: boolean;
  diff: string;
  revision_id?: number | null;
  dry_run?: boolean;
}

// The writes send the YAML text as written, so the stored overlay keeps its comments; an empty text is the shipped
// catalogue. A 422 lists every problem with where it is; the caller shows them, so the global toast is suppressed.
const YAML_BODY = {
  headers: { "Content-Type": "application/yaml" },
  suppressValidationToast: true,
};

export const CATALOGUE_ROUTE = "/config/providers/catalog";

export async function fetchProviderCatalogue(): Promise<ProviderCatalogueDocument> {
  const { data } = await api.get<ProviderCatalogueDocument>(CATALOGUE_ROUTE);
  return data;
}

/**
 * What saving `yamlText` would change, from the version the edit started from. Stores nothing; 422 as a real save
 * would, 409 when the catalogue moved since `generation`. Admin only.
 */
export async function previewProviderCatalogue(yamlText: string, generation: number): Promise<CatalogueWriteResult> {
  const { data } = await api.put<CatalogueWriteResult>(CATALOGUE_ROUTE, yamlText, {
    ...YAML_BODY,
    headers: { ...YAML_BODY.headers, "If-Match": `"${generation}"` },
    params: { dry_run: 1 },
  });
  return data;
}

/** Store `yamlText` as the overlay, in force at once; refused with 409 when the catalogue moved since `generation`. */
export async function saveProviderCatalogue(
  yamlText: string,
  generation: number,
  reason = "",
): Promise<CatalogueWriteResult> {
  const { data } = await api.put<CatalogueWriteResult>(CATALOGUE_ROUTE, yamlText, {
    ...YAML_BODY,
    headers: { ...YAML_BODY.headers, "If-Match": `"${generation}"` },
    params: reason ? { reason } : undefined,
  });
  return data;
}
