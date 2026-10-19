import type { OrgActionName, OrgNeed, OrgRepoFacts, Organization } from "@/api/orgs";
import type { Credential } from "@/lib/types/credentials";

/**
 * Pure helpers for the organization pages: which senses a repository has,
 * how needs group, and which action calls a selection of needs becomes.
 */

/** The add-organization wizard. Its "-" segment starts no GitHub login and
 * no GitLab group path, so the wizard never shadows an organization page
 * (`/orgs/<owner>`): an owner named "new" keeps its page. */
export const ORG_WIZARD_PATH = "/orgs/-/new";

export type SenseKey = "intake" | "registry" | "qa" | "watch" | "tracker" | "ui" | "boot";
export type SenseState = "on" | "off" | "unknown";

export interface Sense {
  key: SenseKey;
  label: string;
  /** The `facts` field discovery stores for this sense. */
  fact: string;
  on: string;
  off: string;
}

/** One chip per sense, in this order, in the repositories table. */
export const SENSES: readonly Sense[] = [
  { key: "intake", label: "intake", fact: "hook_present", on: "a webhook points at this deployment", off: "no webhook points at this deployment" },
  { key: "registry", label: "registry", fact: "registered", on: "registered in embry0", off: "not registered in embry0" },
  { key: "qa", label: "QA", fact: "qa_config", on: "QA config stored", off: "no QA config stored" },
  { key: "watch", label: "watch", fact: "watcher_stack", on: "the log watcher has a stack for it", off: "not watched" },
  { key: "tracker", label: "tracker", fact: "tracker_mapped", on: "mapped to a tracker project", off: "not mapped to a tracker project" },
  { key: "ui", label: "UI", fact: "frontend", on: "has a frontend", off: "no frontend found" },
  { key: "boot", label: "boot", fact: "boot_hint", on: "a boot command was found", off: "no boot command found" },
];

/** `unknown` is a fact the credential could not see (stored as null). */
export function senseState(row: OrgRepoFacts, sense: Sense): SenseState {
  const value = row.facts[sense.fact];
  if (sense.key === "boot") {
    if (value === null || value === undefined) return row.facts.root_visible === false ? "unknown" : "off";
    return typeof value === "string" && value.trim() !== "" ? "on" : "off";
  }
  if (value === true) return "on";
  if (value === false) return "off";
  return "unknown";
}

export function senseTitle(sense: Sense, state: SenseState, row: OrgRepoFacts): string {
  if (state === "unknown") return `${sense.label}: the credential could not see this`;
  const base = `${sense.label}: ${state === "on" ? sense.on : sense.off}`;
  if (sense.key === "boot" && state === "on") return `${base} (${String(row.facts.boot_hint)})`;
  return base;
}

/** True when the repository lacks this sense; archived repositories are skipped by discovery and lack nothing. */
export function isMissing(row: OrgRepoFacts, key: SenseKey): boolean {
  if (row.archived) return false;
  const sense = SENSES.find((s) => s.key === key);
  return sense ? senseState(row, sense) !== "on" : false;
}

/** The closed vocabulary of need kinds, in the order the pages list them. */
export const NEED_KINDS: readonly { kind: string; label: string }[] = [
  { kind: "not_registered", label: "Not registered" },
  { kind: "no_webhook", label: "No webhook" },
  { kind: "onboarding_candidate", label: "Ready to onboard" },
  { kind: "not_watched", label: "Not watched" },
  { kind: "no_qa_config", label: "No QA config" },
  { kind: "access_limited", label: "Access limited" },
  { kind: "env_values_needed", label: "Environment values needed" },
  { kind: "no_log_source", label: "No log source" },
  { kind: "no_deploy_marker", label: "No deploy marker" },
  { kind: "count_gap", label: "Count gap" },
  { kind: "hook_origin_unknown", label: "Webhook origin unknown" },
];

export function needKindLabel(kind: string): string {
  return NEED_KINDS.find((k) => k.kind === kind)?.label ?? kind.replace(/_/g, " ");
}

export interface NeedGroup {
  kind: string;
  label: string;
  needs: OrgNeed[];
  /** Distinct repositories across the group's needs. */
  repoCount: number;
  openCount: number;
}

/** Needs grouped by kind, vocabulary order first, then any kind this page does not know, by name. */
export function groupNeeds(needs: readonly OrgNeed[]): NeedGroup[] {
  const byKind = new Map<string, OrgNeed[]>();
  for (const need of needs) {
    const list = byKind.get(need.kind) ?? [];
    list.push(need);
    byKind.set(need.kind, list);
  }
  const order = (kind: string) => {
    const index = NEED_KINDS.findIndex((k) => k.kind === kind);
    return index === -1 ? NEED_KINDS.length : index;
  };
  return [...byKind.entries()]
    .sort(([a], [b]) => order(a) - order(b) || a.localeCompare(b))
    .map(([kind, list]) => ({
      kind,
      label: needKindLabel(kind),
      needs: list,
      repoCount: new Set(list.flatMap((n) => n.repos)).size,
      openCount: list.filter((n) => n.status === "open").length,
    }));
}

/** Each action as the words a person reads, never the raw API name. */
export const ACTION_LABELS: Readonly<Record<OrgActionName, string>> = {
  register: "Register",
  hook: "Add webhook",
  onboard: "Onboard",
  // The stack it adds stays off until its LogQL is set and it is enabled.
  watch: "Add a disabled watcher stack",
};

export function actionLabel(action: string): string {
  return ACTION_LABELS[action as OrgActionName] ?? action;
}

/** Registering comes first: hooking, onboarding and watching act on registered repositories. */
export const ACTION_ORDER: readonly OrgActionName[] = ["register", "hook", "onboard", "watch"];

export function isActionable(need: OrgNeed): boolean {
  return need.status === "open" && (ACTION_ORDER as readonly string[]).includes(need.action.type);
}

export interface PlannedAction {
  action: OrgActionName;
  repos: string[];
}

/** One call per action type, with the union of the selected needs' repositories. */
export function planActions(needs: readonly OrgNeed[]): PlannedAction[] {
  const repos = new Map<OrgActionName, Set<string>>();
  for (const need of needs) {
    if (!isActionable(need)) continue;
    const action = need.action.type as OrgActionName;
    const set = repos.get(action) ?? new Set<string>();
    need.repos.forEach((r) => set.add(r));
    repos.set(action, set);
  }
  return ACTION_ORDER.filter((a) => (repos.get(a)?.size ?? 0) > 0).map((action) => ({
    action,
    repos: [...(repos.get(action) ?? [])].sort(),
  }));
}

/** Discovery summary keys, labelled, in display order. */
export const SUMMARY_FIELDS: readonly { key: string; label: string }[] = [
  { key: "repos", label: "repositories" },
  { key: "archived", label: "archived" },
  { key: "registered", label: "registered" },
  { key: "hooked", label: "webhook here" },
  { key: "hooks_not_visible", label: "hooks not visible" },
  { key: "qa_config", label: "QA config" },
  { key: "watched", label: "watched" },
  { key: "tracker_mapped", label: "tracker mapped" },
  { key: "frontend", label: "frontend" },
  { key: "boot_hint", label: "boot command" },
];

/** The account an organization acts with, in words. */
export function credentialLabel(org: Organization, credentials: readonly Credential[] | undefined): string {
  if (org.credential_id) {
    const found = credentials?.find((c) => c.id === org.credential_id);
    return found ? found.label : org.credential_id;
  }
  const source = typeof org.access.source === "string" ? org.access.source : "";
  return source.startsWith("env:") ? `environment (${source.slice(4)})` : "environment token";
}
