// Shared audit-event presentation maps (RAV-1056 Phase C) — lifted from
// IssueActivityFeed and extended with the global-audit event vocabulary.
// Backend event names span two conventions (dotted `issue.*`/`user.*` vs
// snake `*_config_updated`); unknown actions fall back to sentence case
// (`actionLabel`), never the raw string.

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
import { sentenceCase } from "./status";

/** Names older rows carry and nothing writes any more, each with the action that replaced it (the backend's
 * `RETIRED_AUDIT_ACTIONS`). Such a row reads as its replacement; it is not a filter of its own, because the
 * audit API's filter by the new name finds it too. */
export const RETIRED_ACTIONS: Record<string, string> = {
  budget_config_updated: "settings.updated",
  "quest.evaluated": "job.review.evaluated",
  "quest.sent_back": "job.review.sent_back",
  "quest.rejected": "job.review.rejected",
  "quest.restarted": "job.review.restarted",
  "quest.dismissed": "job.review.dismissed",
};

/** One lucide icon per audit action, drawn muted beside its label. Emoji used
 * to stand in here and rendered as empty boxes on a machine without a colour
 * emoji font; every other page already draws lucide icons. */
export const ACTION_ICONS: Record<string, LucideIcon> = {
  "issue.created": Plus,
  "issue.updated": Pencil,
  "issue.status_changed": ArrowRight,
  "issue.triaged": Search,
  "issue.decomposed": GitBranch,
  "issue.job_created": Zap,
  "issue.github_synced": ArrowLeftRight,
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

/** The icon for an audit action; an action the map does not know yet gets a dot. */
export function actionIcon(action: string): LucideIcon {
  return ACTION_ICONS[RETIRED_ACTIONS[action] ?? action] ?? Dot;
}

export const ACTION_LABELS: Record<string, string> = {
  "issue.created": "Created",
  "issue.updated": "Updated",
  "issue.status_changed": "Status changed",
  "issue.triaged": "Triaged",
  "issue.decomposed": "Decomposed",
  "issue.job_created": "Job created",
  "issue.github_synced": "GitHub synced",
  "issue.cancelled": "Cancelled",
  "user.login": "Signed in",
  "user.disabled": "User disabled",
  "user.enabled": "User enabled",
  "user.force_logout": "Forced logout",
  "apikey.created": "API key created",
  "apikey.revoked": "API key revoked",
  "environment.secret_revealed": "Secret revealed",
  "job.cancelled": "Job cancelled",
  "job.budget_raised": "Job budget raised",
  "merge.decided": "Merge decided",
  provider_config_updated: "Provider config updated",
  integration_config_updated: "Integrations updated",
  context_config_updated: "Context updated",
  context_config_deleted: "Context deleted",
  provider_config_tested: "Provider tested",
  provider_catalog_refreshed: "Provider model list read",
  watcher_stack_updated: "Watcher stack updated",
  watcher_stack_deleted: "Watcher stack deleted",
  "settings.updated": "Settings document changed",
  "settings.change_undone": "Settings change undone",
  "settings.bootstrapped": "Settings imported from the environment",
  "progression.pack_activated": "Game content pack activated",
  "progression.pack_draft_saved": "Game content draft saved",
  "progression.pack_draft_deleted": "Game content draft discarded",
  "progression.pack_reset": "Game content reset to the shipped default",
  "job.review.evaluated": "Job evaluated",
  "job.review.sent_back": "Job sent back",
  "job.review.rejected": "Job rejected",
  "job.review.restarted": "Job run again",
  "job.review.dismissed": "Job dismissed",
};

/** The words for an audit action; a retired name reads as the action that replaced it, and an action this map
 * does not know yet reads in sentence case rather than as its identifier. */
export function actionLabel(action: string): string {
  return ACTION_LABELS[RETIRED_ACTIONS[action] ?? action] ?? sentenceCase(action);
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
  if (action === "issue.github_synced") {
    const direction = details.direction ?? details.sync_direction;
    if (direction) return String(direction);
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
