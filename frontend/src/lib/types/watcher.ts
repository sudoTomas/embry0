/** RAV-657 log-watcher audit trail — rows from GET /watcher/runs. */

export type WatcherAction =
  | "skipped_quiet"
  | "skipped_backlog"
  | "skipped_duplicate"
  /** Analysis withheld: the last attempt on this window was unparseable and nothing changed since. */
  | "skipped_unparseable"
  | "no_issue"
  | "proposed"
  /** A proposal made while the draft cap was full: recorded, filed when a slot frees (#283). */
  | "deferred"
  /** The draft cap was full and the day's budget for full-cap analyses was spent (#283). */
  | "skipped_analysis_cap"
  /** Nothing in the window was new, spiking or regressed, or all of it has an open case (#291): no analysis paid. */
  | "no_change"
  /** Everything notable was found clean by an analysis within the stack's skip_known_hours: no analysis paid. */
  | "skipped_known"
  | "error";

export interface WatcherRun {
  id: string;
  window_start: string;
  window_end: string;
  log_lines: number;
  action: WatcherAction;
  detail: string | null;
  job_id: string | null;
  fingerprint: string | null;
  /** The stack this tick watched ("owner/name"); "" for ticks before stacks existed. */
  repo: string;
  /** The filed ticket, tracker-neutral since #378 (was linear_issue_identifier). */
  tracker: string | null;
  tracker_key: string | null;
  tracker_url: string | null;
  created_at: string;
}

/** POST /watcher/run — the tick's summary (fields vary by action). */
export interface WatcherTickResult {
  action: WatcherAction;
  log_lines?: number;
  job_id?: string | null;
  fingerprint?: string | null;
  tracker?: string | null;
  tracker_key?: string | null;
  tracker_url?: string | null;
}

export type WatcherTracker = "linear" | "github" | "gitlab";

/** One stack embry0 watches (#378): a repository, its Loki selector, where findings are filed, and the settings it overrides. */
export interface WatcherStack {
  repo: string;
  logql: string;
  tracker: WatcherTracker;
  /** linear: `{team_id, project_id}`; github and gitlab file into the stack's repository. */
  tracker_target: Record<string, unknown>;
  /** Overrides only; an absent key inherits the global default. */
  settings: Record<string, unknown>;
  enabled: boolean;
}

export interface WatcherStackList {
  stacks: WatcherStack[];
  overridable_settings: string[];
}

export type WatcherStackWrite = Omit<WatcherStack, "repo">;

/** What PUT answers: the stored stack and the settings keys it dropped. */
export interface WatcherStackSaved extends WatcherStack {
  ignored_settings: string[];
}
