// The settings document (`/api/v1/config/settings`): the deployment's
// operator policy, one YAML document with one section per feature. The
// shapes mirror embry0/api/v1/settings.py.

/** The stored document, as `GET /config/settings` answers. */
export interface SettingsDocument {
  /** The text as written, comments included; empty when nothing is stored. */
  yaml_text: string;
  /** The parsed values, sparse: only what an operator wrote. */
  data: Record<string, unknown>;
  schema_version: number;
  /** Bumped by every write; `If-Match` on a save names the one it read. */
  generation: number;
  updated_by: string;
  updated_at: string | null;
  /** The sections this release reads; empty until a feature registers one. */
  sections: string[];
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

/** `PUT /config/settings?dry_run=1`: what a save would do, nothing stored. */
export interface SettingsPreview {
  /** False when the text equals the stored one. */
  would_change: boolean;
  /** A unified diff, `stored` to `proposed`; empty when nothing would change. */
  text_diff: string;
  changes: SettingsChange[];
  unknown_sections: string[];
  unknown_keys: string[];
}

/** `PUT /config/settings`. `revision_id` is null when the text was unchanged and nothing was recorded. */
export interface SettingsSaveResult {
  generation: number;
  revision_id: number | null;
  changes: SettingsChange[];
  unknown_sections: string[];
  unknown_keys: string[];
}

/** Where an effective value comes from. */
export type SettingSource = "default" | "document" | "bootstrap";

/** A layer that can override a key, as its section declares. */
export type SettingOverrideLayer = "organization" | "repository";

export interface EffectiveSetting {
  key: string;
  /** What loops read now. */
  value: unknown;
  default: unknown;
  source: SettingSource;
  /** Who and when last changed it, from the kept history; null for a default or older history. */
  set_by: string | null;
  set_at: string | null;
  /** The environment variable the key was imported from, if any. */
  env: string | null;
  /** That variable is still in the process environment, where it is now ignored. */
  env_still_set: boolean;
  overridable: SettingOverrideLayer[];
  /** Why a stored value is read as its default instead; null when it is not. */
  refused: string | null;
  description: string | null;
}

export interface EffectiveSection {
  name: string;
  summary: string;
  keys: EffectiveSetting[];
}

/** `GET /config/settings/effective`. */
export interface EffectiveSettings {
  generation: number;
  sections: EffectiveSection[];
  unknown_sections: string[];
  unknown_keys: string[];
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
