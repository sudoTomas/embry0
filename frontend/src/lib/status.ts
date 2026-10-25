import type { BadgeTone } from "@/components/ui/Badge";

/**
 * Every status, outcome and action word the API returns, as the words a
 * person reads, and the colour its pill takes. This is the one place: a pill
 * never shows a raw enum (`PR_MERGED`, `no_issue`, `skipped_quiet`).
 */
const STATUS: Record<string, { label: string; tone: BadgeTone }> = {
  // Jobs
  pending: { label: "Pending", tone: "neutral" },
  running: { label: "Running", tone: "info" },
  completed: { label: "Completed", tone: "success" },
  failed: { label: "Failed", tone: "error" },
  partial: { label: "Partial", tone: "warning" },
  cancelled: { label: "Cancelled", tone: "neutral" },
  awaiting_input: { label: "Awaiting input", tone: "warning" },
  pr_merged: { label: "PR merged", tone: "purple" },
  pr_closed: { label: "PR closed", tone: "neutral" },
  paused: { label: "Paused", tone: "warning" },
  expired: { label: "Expired", tone: "neutral" },
  skipped: { label: "Skipped", tone: "neutral" },

  // Issues
  open: { label: "Open", tone: "success" },
  triaging: { label: "Triaging", tone: "warning" },
  in_progress: { label: "In progress", tone: "info" },
  closed: { label: "Closed", tone: "purple" },

  // QA runs and per-app results
  passed: { label: "Passed", tone: "success" },
  infra_error: { label: "Infra error", tone: "neutral" },
  qa_failure: { label: "QA failure", tone: "error" },
  e2e_failure: { label: "E2E failure", tone: "error" },
  boot_failure: { label: "Boot failure", tone: "warning" },
  ready_check_failed: { label: "Ready check failed", tone: "warning" },
  infra_failure: { label: "Infra failure", tone: "neutral" },
  inconclusive: { label: "Inconclusive", tone: "warning" },

  // Watcher ticks
  // A draft waiting for a person to decide: the warn colour, never the accent.
  proposed: { label: "Proposed", tone: "warning" },
  deferred: { label: "Filing deferred", tone: "warning" },
  no_issue: { label: "Nothing found", tone: "neutral" },
  skipped_quiet: { label: "Quiet", tone: "neutral" },
  skipped_backlog: { label: "Proposal cap reached", tone: "neutral" },
  skipped_duplicate: { label: "Already filed", tone: "neutral" },
  skipped_unparseable: { label: "Analysis withheld", tone: "warning" },
  skipped_analysis_cap: { label: "Analysis budget spent", tone: "warning" },
  no_change: { label: "Nothing new", tone: "neutral" },
  skipped_known: { label: "Already checked", tone: "neutral" },
  skipped_account_paused: { label: "Waiting for an account", tone: "warning" },
  error: { label: "Error", tone: "error" },

  // Organizations and their needs
  active: { label: "Active", tone: "success" },
  removed: { label: "Removed", tone: "neutral" },
  done: { label: "Done", tone: "success" },
  already: { label: "Already done", tone: "neutral" },
  dismissed: { label: "Dismissed", tone: "neutral" },

  // Credentials
  ok: { label: "OK", tone: "success" },
  quarantined: { label: "Quarantined", tone: "warning" },
  disabled: { label: "Disabled", tone: "neutral" },
};

/**
 * "some_raw_value" or "user.force_logout" -> "Some raw value" / "User force
 * logout": the fallback for any machine word (status, audit action, kind) a
 * label table does not know yet, so the page never shows the raw identifier.
 */
export function sentenceCase(raw: string): string {
  const words = raw.replace(/[_.-]+/g, " ").trim().toLowerCase();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/**
 * Provider, forge and tracker names as their makers spell them. The one map:
 * a provider card, a credential row or an agent's provider never shows the
 * stored identifier (`xai`) or a capitalised guess at it (`Xai`).
 */
export const PROVIDER_LABELS: Record<string, string> = {
  anthropic: "Anthropic",
  xai: "xAI",
  openai: "OpenAI",
  openrouter: "OpenRouter",
  deepseek: "DeepSeek",
  github: "GitHub",
  gitlab: "GitLab",
  linear: "Linear",
};

/** A provider name in words; one the map does not know reads in sentence case. */
export function providerLabel(name: string): string {
  return PROVIDER_LABELS[name.toLowerCase()] ?? sentenceCase(name);
}

export function statusLabel(status: string | null | undefined): string {
  if (!status) return "Unknown";
  return STATUS[status.toLowerCase()]?.label ?? sentenceCase(status);
}

/** Unknown statuses (a newer backend) fall back to neutral. */
export function statusTone(status: string | null | undefined): BadgeTone {
  if (!status) return "neutral";
  return STATUS[status.toLowerCase()]?.tone ?? "neutral";
}
