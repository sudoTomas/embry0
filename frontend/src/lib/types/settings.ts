// The settings document (`/api/v1/config/settings`): the deployment's
// operator policy, one YAML document with one section per feature, and a
// layer of it per organization and per repository that sets its own. The
// shapes mirror embry0/api/v1/settings.py.

/** Which document: the deployment's, an organization's layer, or a repository's. */
export type SettingsLayerKind = "deployment" | "organization" | "repository";

/** A document's layer as the API names it in `scope`. */
export interface SettingsScope {
  layer: SettingsLayerKind;
  /** The organization the layer is, or sits under; null for the deployment. */
  owner: string | null;
  /** The repository, as `owner/name`, for a repository's layer only. */
  repo: string | null;
}

/** The stored document, as `GET /config/settings` answers. */
export interface SettingsDocument {
  /** Which document this is; absent from a server older than layers. */
  scope?: SettingsScope;
  /** The text as written, comments included; empty when nothing is stored. */
  yaml_text: string;
  /** The parsed values, sparse: only what an operator wrote. */
  data: Record<string, unknown>;
  schema_version: number;
  /** Bumped by every write; `If-Match` on a save names the one it read. A layer nobody wrote is 0. */
  generation: number;
  /** Who wrote it last; null when it is someone else's and this key may not see it (as `/audit` hides it). */
  updated_by: string | null;
  updated_at: string | null;
  /** The sections this release reads; empty until a feature registers one. */
  sections: string[];
  /** Per section, the keys this document may set: every key for the deployment's, the declared ones for a layer. */
  settable?: Record<string, string[]>;
  /** Stored sections this release does not know (written by a newer one); kept while left unchanged. */
  unknown_sections: string[];
  /** Stored keys of a known section that this release does not have, as `section.key`; kept the same way. */
  unknown_keys: string[];
  /**
   * `"file"` when the document says it is kept in a file and stored with
   * `embry0 settings apply`: the Policy tab then only shows it. Null when it
   * is edited here too.
   */
  managed_by: "file" | null;
  /**
   * Whether the calling key's repository allowlist lets it write this
   * document, by the check a save makes: true without an allowlist; for a
   * key with one, only on a repository's layer it covers. The admin tier is
   * checked apart. Absent from an older server.
   */
  allowlist_covers?: boolean;
}

/** One stored organization or repository layer, as `GET /config/settings/layers` lists it. */
export interface SettingsLayerRow extends SettingsScope {
  layer: "organization" | "repository";
  /** The old scope a renamed repository's layer is still kept under, until its next change moves it; else null. */
  stored_as: string | null;
  generation: number;
  updated_by: string | null;
  updated_at: string | null;
  /** The keys it sets, as `section.key`; empty for a layer cleared with an empty text. */
  keys: string[];
}

/** A value of a layer under the document written that the change makes read as inherited. */
export interface LayerRefusal {
  scope: SettingsScope;
  /** `section.key`. */
  key: string;
  reason: string;
}

/**
 * One key whose stored value differs. `before` and `after` are the stored
 * values, null when the key was not written (`set_*` false). A preview and
 * a save add `effective_*`, what a loop reads with the default filled in.
 */
export interface SettingsChange {
  key: string;
  before: unknown;
  after: unknown;
  set_before: boolean;
  set_after: boolean;
  effective_before?: unknown;
  effective_after?: unknown;
}

/** The layer values a write makes read as inherited: at most 100 named, and how many there are. Absent from an older server. */
export interface LayersRefused {
  layers_refused?: LayerRefusal[];
  layers_refused_total?: number;
}

/** `PUT /config/settings?dry_run=1`: what a save would do, nothing stored. */
export interface SettingsPreview extends LayersRefused {
  /** False when the text equals the stored one. */
  would_change: boolean;
  /** A unified diff, `stored` to `proposed`; empty when nothing would change. */
  text_diff: string;
  changes: SettingsChange[];
  unknown_sections: string[];
  unknown_keys: string[];
}

/** `PUT /config/settings`. `revision_id` is null when the text was unchanged and nothing was recorded. */
export interface SettingsSaveResult extends LayersRefused {
  generation: number;
  revision_id: number | null;
  changes: SettingsChange[];
  unknown_sections: string[];
  unknown_keys: string[];
}

/** What a change of keys answers beyond a whole document's: which document, and whether it is kept in a file. */
interface SettingsKeysExtras {
  scope: SettingsScope;
  /**
   * `"file"` when the document is kept in a file and stored with
   * `embry0 settings apply`: the change is written all the same, and lasts
   * until the file is applied again, so say so where it was made.
   */
  managed_by: "file" | null;
}

/** `PATCH /config/settings/{section}?dry_run=1`: what changing the keys would do, nothing stored. */
export type SettingsKeysPreview = SettingsPreview & SettingsKeysExtras;

/** `PATCH /config/settings/{section}`. `revision_id` is null when no value changed and nothing was recorded. */
export type SettingsKeysResult = SettingsSaveResult & SettingsKeysExtras;

/**
 * Where an effective value comes from: the deployment's (`default`, `document`, `bootstrap`), a layer's, or
 * `not_imported`: a key moved from older storage that the import has not imported yet, read meanwhile as
 * the release before read it (`EffectiveSetting.not_imported` says from where).
 */
export type SettingSource = "default" | "document" | "bootstrap" | "not_imported" | "organization" | "repository";

/** Where a key the import has not imported yet is read from meanwhile. */
export interface NotImportedFrom {
  /** `row`: a record kept in the database before the document; `variable`; `default`; `organization`: its own settings. */
  read_from: "row" | "variable" | "default" | "organization";
  /** It, named for a person: the table, the variable, or the organization's settings. */
  label: string;
}

/** A layer that can override a key, as its section declares. */
export type SettingOverrideLayer = "organization" | "repository";

/** One document's say on one key, in a layer's effective view, most general first. */
export interface EffectiveLayerEntry extends SettingsScope {
  /** Whether this document supplies a value that is read. */
  set: boolean;
  /** What it stores for the key; null when it stores nothing. */
  value: unknown;
  /** Who and when last changed it here; `set_by` null when hidden or not known. */
  set_by: string | null;
  set_at: string | null;
  /** Whether this document may set the key. */
  settable: boolean;
  /** Why a stored value is read as the inherited one; null when it is not. */
  refused: string | null;
}

export interface EffectiveSetting {
  key: string;
  /** What loops read now. */
  value: unknown;
  default: unknown;
  source: SettingSource;
  /** Set when `source` is `not_imported`: where the value enforced meanwhile is read from. */
  not_imported?: NotImportedFrom | null;
  /**
   * Who and when last changed it, from the kept history; both null for a
   * default or older history. `set_by` alone is null when it was someone
   * else and this key may not see who (as `/audit` hides it).
   */
  set_by: string | null;
  set_at: string | null;
  /** The environment variable the key was imported from, if any. */
  env: string | null;
  /** That variable is still in the process environment, where it is now ignored. */
  env_still_set: boolean;
  overridable: SettingOverrideLayer[];
  /** Why the deployment's stored value is read as its default instead; null when it is not. */
  refused: string | null;
  description: string | null;
  /** A layer's view only: the value the key would have without the layer viewed, what an override overrides. */
  inherited?: unknown;
  /** A layer's view only: each document in the view, most general first. */
  layers?: EffectiveLayerEntry[];
}

export interface EffectiveSection {
  name: string;
  summary: string;
  keys: EffectiveSetting[];
}

/** `GET /config/settings/effective`, with `?owner=` or `?repo=` as that layer reads it. */
export interface EffectiveSettings {
  generation: number;
  sections: EffectiveSection[];
  unknown_sections: string[];
  unknown_keys: string[];
  /** A layer's view only: which layer. */
  scope?: SettingsScope;
}

/** The path a revision came in by. */
export type SettingsRevisionSource = "person" | "file" | "bootstrap" | "restore" | "upgrade";

/** One change to the document, newest first in `GET /config/settings/revisions`. */
export interface SettingsRevision {
  id: number;
  /** The change in plain words, written by the server. */
  what: string;
  /** Who made it. Null or left out when the key may not see someone else's (as `/audit` hides it). */
  actor?: string | null;
  source: SettingsRevisionSource;
  reason: string;
  created_at: string | null;
  /** Only the newest change, while the stored text is still what it wrote. */
  undoable: boolean;
  why_not: string | null;
}

/** `GET /config/settings/revisions/{id}`: the texts either side of the change. */
export interface SettingsRevisionDetail extends SettingsRevision {
  before_text: string | null;
  after_text: string | null;
  changes: SettingsChange[];
}

/** `POST /config/settings/revisions/{id}/undo`: answers like a save; recorded with source `restore`. */
export type SettingsUndoResult = SettingsSaveResult;
