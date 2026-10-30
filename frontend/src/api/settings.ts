import { api } from "./client";
import { DEPLOYMENT_LAYER, layerParams, type PolicyLayer } from "@/lib/settingsLayer";
import type {
  EffectiveSettings,
  SettingsDocument,
  SettingsLayerRow,
  SettingsPreview,
  SettingsRevision,
  SettingsRevisionDetail,
  SettingsSaveResult,
  SettingsUndoResult,
} from "@/lib/types/settings";

// Every route but a revision's own takes the layer: no parameter is the
// deployment's document, `owner` an organization's layer, `repo` a
// repository's. A revision's id names its layer, so one revision and its
// undo take none.

// The writes send the YAML text as written, so the stored document keeps its
// comments; an empty text puts every key back to its default. A 422 lists
// every problem with the key it belongs to; the caller shows them next to
// the text, so the global validation toast is suppressed.
const YAML_BODY = {
  headers: { "Content-Type": "application/yaml" },
  suppressValidationToast: true,
};

/** The stored document, its values and its generation; a layer nobody wrote is generation 0. */
export async function fetchSettings(layer: PolicyLayer = DEPLOYMENT_LAYER): Promise<SettingsDocument> {
  const { data } = await api.get<SettingsDocument>("/config/settings", { params: layerParams(layer) });
  return data;
}

/**
 * What saving `yamlText` would change. Stores nothing; 422 as a real save
 * would, and 409 when `generation` is no longer the stored one, so a
 * preview is always of the version the edit started from. Admin only.
 */
export async function previewSettings(
  yamlText: string,
  generation: number,
  layer: PolicyLayer = DEPLOYMENT_LAYER,
): Promise<SettingsPreview> {
  const { data } = await api.put<SettingsPreview>("/config/settings", yamlText, {
    ...YAML_BODY,
    headers: { ...YAML_BODY.headers, "If-Match": `"${generation}"` },
    params: { ...layerParams(layer), dry_run: 1 },
  });
  return data;
}

export interface SaveSettingsRequest {
  yamlText: string;
  /** The generation the edit started from; the save is refused with 409 when the document moved since. */
  generation: number;
  /** Why, recorded with the revision; optional. */
  reason?: string;
}

/**
 * Replace the document. 409 when `generation` is no longer the stored one
 * (`0` for a layer nobody wrote: it creates it, unless someone else did
 * first); 413 past 256 KiB. Admin only; a key with a repository allowlist
 * writes only the layers of the repositories it covers.
 */
export async function saveSettings(
  { yamlText, generation, reason }: SaveSettingsRequest,
  layer: PolicyLayer = DEPLOYMENT_LAYER,
): Promise<SettingsSaveResult> {
  const trimmed = reason?.trim();
  const params = { ...layerParams(layer), ...(trimmed ? { reason: trimmed } : {}) };
  const { data } = await api.put<SettingsSaveResult>("/config/settings", yamlText, {
    ...YAML_BODY,
    headers: { ...YAML_BODY.headers, "If-Match": `"${generation}"` },
    params: Object.keys(params).length > 0 ? params : undefined,
  });
  return data;
}

/** Every key of every section: the value loops read, its default, and where it came from; for a layer, as it reads them. */
export async function fetchEffectiveSettings(layer: PolicyLayer = DEPLOYMENT_LAYER): Promise<EffectiveSettings> {
  const { data } = await api.get<EffectiveSettings>("/config/settings/effective", { params: layerParams(layer) });
  return data;
}

/** The document's changes, newest first. */
export async function fetchSettingsRevisions(layer: PolicyLayer = DEPLOYMENT_LAYER, limit = 50): Promise<SettingsRevision[]> {
  const { data } = await api.get<{ revisions: SettingsRevision[] }>("/config/settings/revisions", {
    params: { ...layerParams(layer), limit },
  });
  return data.revisions;
}

/** Every stored organization and repository layer, with the keys it sets. */
export async function fetchSettingsLayers(): Promise<SettingsLayerRow[]> {
  const { data } = await api.get<{ layers: SettingsLayerRow[] }>("/config/settings/layers");
  return data.layers;
}

/** One change, with the text before and after it. */
export async function fetchSettingsRevision(id: number): Promise<SettingsRevisionDetail> {
  const { data } = await api.get<SettingsRevisionDetail>(`/config/settings/revisions/${id}`);
  return data;
}

/**
 * Put back what the newest change replaced; recorded as a change of its own.
 * 409 for any other change; 422 when today's sections refuse the old text.
 * Admin only.
 */
export async function undoSettingsChange(id: number): Promise<SettingsUndoResult> {
  const { data } = await api.post<SettingsUndoResult>(`/config/settings/revisions/${id}/undo`, undefined, {
    suppressValidationToast: true,
  });
  return data;
}
