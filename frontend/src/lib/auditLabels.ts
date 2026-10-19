// Shared audit-event presentation maps (RAV-1056 Phase C) — lifted from
// IssueActivityFeed and extended with the global-audit event vocabulary.
// Backend event names span two conventions (dotted `issue.*`/`user.*` vs
// snake `*_config_updated`); unknown actions fall back to sentence case
// (`actionLabel`), never the raw string.

import { sentenceCase } from "./status";

export const ACTION_ICONS: Record<string, string> = {
  "issue.created": "✦",
  "issue.updated": "✎",
  "issue.status_changed": "→",
  "issue.triaged": "🔍",
  "issue.decomposed": "⑃",
  "issue.job_created": "⚡",
  "issue.github_synced": "⇄",
  "issue.cancelled": "✕",
  "user.login": "🔑",
  "user.disabled": "⛔",
  "user.enabled": "✓",
  "user.force_logout": "⏏",
  "apikey.created": "🗝",
  "apikey.revoked": "✕",
  "environment.secret_revealed": "👁",
  "job.cancelled": "✕",
  "merge.decided": "⇲",
  provider_config_updated: "⚙",
  budget_config_updated: "⚙",
  integration_config_updated: "⚙",
  context_config_updated: "⚙",
  context_config_deleted: "✕",
  provider_config_tested: "⚙",
  watcher_stack_updated: "⚙",
  watcher_stack_deleted: "✕",
};

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
  "merge.decided": "Merge decided",
  provider_config_updated: "Provider config updated",
  budget_config_updated: "Budget config updated",
  integration_config_updated: "Integrations updated",
  context_config_updated: "Context updated",
  context_config_deleted: "Context deleted",
  provider_config_tested: "Provider tested",
  watcher_stack_updated: "Watcher stack updated",
  watcher_stack_deleted: "Watcher stack deleted",
};

/** The words for an audit action; an action this map does not know yet reads
 * in sentence case rather than as its identifier. */
export function actionLabel(action: string): string {
  return ACTION_LABELS[action] ?? sentenceCase(action);
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
  // Config-update rows carry the changed-field payload — show field names only.
  if (action.endsWith("_config_updated")) {
    const keys = Object.keys(details);
    if (keys.length > 0) return keys.join(", ");
  }
  return "";
}
