/** Shapes of the Skills and Tools routes (docs/api.md, "Skills" and "Tools"). */

/** One row of `GET /skills`; `available` is true when the source is ready and enabled. */
export interface SkillCatalogEntry {
  name: string;
  source_id: string;
  description: string;
  available: boolean;
  /** The source's status, or `disabled`. */
  status: string;
}

/** One row of `GET /skills/sources`. */
export interface SkillSource {
  id: string;
  kind: "git" | "path" | "inline";
  url: string | null;
  ref: string | null;
  resolved_sha: string | null;
  plugin_name: string | null;
  skills: { name: string; description: string }[];
  status: "pending" | "syncing" | "ready" | "error" | string;
  error: string | null;
  enabled: boolean;
  is_builtin: boolean;
  synced_at: string | null;
  syncing?: boolean;
}

/** Body of `POST /skills/sources`. */
export interface SkillSourceCreate {
  id: string;
  kind: "git" | "path";
  url: string;
  ref?: string;
  enabled?: boolean;
}

/** Body of `PATCH /skills/sources/{id}`. */
export interface SkillSourceUpdate {
  ref?: string | null;
  enabled?: boolean;
}

/** One row of `GET /skills/inline`. */
export interface InlineSkill {
  name: string;
  description: string;
  body: string;
  created_at: string;
  updated_at: string;
}

/** One row of `GET /tools`. */
export interface ToolCatalogEntry {
  name: string;
  source: string;
  description: string;
}

/** The `detail` of a 409 on a delete that something still references. */
export interface SkillReferences {
  message?: string;
  agents?: string[];
  templates?: string[];
}

/** A 409 body: the references above, or a plain string (a sync is running). */
export interface SkillWriteConflict {
  detail?: string | SkillReferences;
}
