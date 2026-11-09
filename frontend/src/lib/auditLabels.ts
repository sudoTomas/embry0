// Shared audit-event presentation: the words come from the backend's action
// registry (`GET /audit/actions`, embry0/audit/actions.py), the one list the
// writers are typed against; this module adds what the registry does not
// carry, an icon per action and a short rendering of a row's details. An
// action the registry does not list reads in sentence case, never as its
// identifier.

import {
  ArrowLeftRight,
  ArrowRight,
  Ban,
  Check,
  Dot,
  Eye,
  GitBranch,
  GitMerge,
  Key,
  KeyRound,
  LogOut,
  Pencil,
  Plus,
  Search,
  Settings,
  Undo2,
  X,
  Zap,
  type LucideIcon,
} from "lucide-react";
import type { AuditActionEntry } from "@/api/audit";
import { sentenceCase } from "./status";

/** One lucide icon per audit action, drawn muted beside its label, keyed by the registry's current names. Emoji
 * used to stand in here and rendered as empty boxes on a machine without a colour emoji font; every other page
 * already draws lucide icons. */
export const ACTION_ICONS: Record<string, LucideIcon> = {
  "issue.created": Plus,
  "issue.updated": Pencil,
  "issue.status_changed": ArrowRight,
  "issue.triaged": Search,
  "issue.decomposed": GitBranch,
  "issue.job_created": Zap,
  "issue.cancelled": X,
  "user.login": KeyRound,
  "user.disabled": Ban,
  "user.enabled": Check,
  "user.force_logout": LogOut,
  "apikey.created": Key,
  "apikey.revoked": X,
  "environment.secret_revealed": Eye,
  "job.cancelled": X,
  "job.budget_raised": ArrowRight,
  "merge.decided": GitMerge,
  provider_config_updated: Settings,
  integration_config_updated: Settings,
  context_config_updated: Settings,
  context_config_deleted: X,
  provider_config_tested: Settings,
  provider_catalog_refreshed: Settings,
  watcher_stack_updated: Settings,
  watcher_stack_deleted: X,
  "settings.updated": Settings,
  "settings.change_undone": Undo2,
  "settings.bootstrapped": Settings,
  "progression.pack_activated": Check,
  "progression.pack_draft_saved": Pencil,
  "progression.pack_draft_deleted": X,
  "progression.pack_reset": ArrowLeftRight,
};

/** How the dashboard reads audit actions, made from the registry as `GET /audit/actions` serves it. */
export interface AuditVocabulary {
  /** What the Audit filter offers: every current action, in the registry's order. A retired name is not a filter
   * of its own: the filter by the action that replaced it finds its rows too. */
  filterOptions: string[];
  /** The registry's words for an action (a retired name has the label of the action that replaced it); an action
   * it does not list, or any before it is read, in sentence case. */
  label: (action: string) => string;
  /** The action's icon, a retired name drawn as the action that replaced it; a dot for an action with none. */
  icon: (action: string) => LucideIcon;
}

export function auditVocabulary(actions: readonly AuditActionEntry[] = []): AuditVocabulary {
  const byName = new Map(actions.map((entry) => [entry.action, entry]));
  return {
    filterOptions: actions.filter((entry) => !entry.replaced_by).map((entry) => entry.action),
    label: (action) => byName.get(action)?.label ?? sentenceCase(action),
    icon: (action) => ACTION_ICONS[byName.get(action)?.replaced_by ?? action] ?? Dot,
  };
}

export function formatDetails(action: string, details: Record<string, unknown>): string {
  if (action === "issue.status_changed") {
    const from = details.old_status ?? details.from;
    const to = details.new_status ?? details.to;
    if (from && to) return `${from} → ${to}`;
  }
  if (action === "issue.updated") {
    const fields = details.fields ?? details.changed_fields;
    if (Array.isArray(fields) && fields.length > 0) return fields.join(", ");
    if (typeof fields === "string") return fields;
  }
  if (action === "user.login" && details.tier) {
    return `tier ${details.tier}`;
  }
  if (
    (action === "user.disabled" || action === "user.enabled" || action === "user.force_logout") &&
    details.email
  ) {
    return String(details.email);
  }
  if (action === "apikey.created" && details.name) {
    const scope = details.scope ? ` (${details.scope})` : "";
    const service = details.service ? " · service" : "";
    return `${details.name}${scope}${service}`;
  }
  if (action === "apikey.revoked" && details.name) {
    return String(details.name);
  }
  if (action === "context_config_updated") {
    return details.scope === "repo" && details.repo ? String(details.repo) : "global";
  }
  if (action === "context_config_deleted" && details.repo) {
    return String(details.repo);
  }
  if (action === "provider_config_tested" && details.status) {
    return String(details.status);
  }
  // The settings document's rows name the keys a change touched, never their values.
  if (action === "settings.updated" && Array.isArray(details.keys)) {
    return details.keys.length > 0 ? details.keys.join(", ") : "comments only";
  }
  if (action === "settings.bootstrapped" && details.sections && typeof details.sections === "object") {
    return Object.entries(details.sections as Record<string, unknown>)
      .flatMap(([section, keys]) => (Array.isArray(keys) ? keys.map((key) => `${section}.${key}`) : [section]))
      .join(", ");
  }
  // Config-update rows carry the changed-field payload — show field names only.
  if (action.endsWith("_config_updated")) {
    const keys = Object.keys(details);
    if (keys.length > 0) return keys.join(", ");
  }
  return "";
}
