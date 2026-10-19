import { useLayoutEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  AlertTriangle,
  Check,
  Circle,
  Clock,
  Eye,
  HelpCircle,
  Loader2,
  MinusCircle,
  ShieldAlert,
  type LucideIcon,
} from "lucide-react";
import type { Forge } from "@/api/repos";
import type { Readiness, ReadinessRow, ReadinessRowState } from "@/api/repoSetup";
import { toastApiError } from "@/api/errorToast";
import { prefersReducedMotion } from "@/hooks/useReducedMotion";
import { useRepoReadiness, useSkipTesting } from "@/hooks/useRepoReadiness";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { Button, buttonVariants } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { CreateIssueDialog } from "@/components/issues/CreateIssueDialog";
import { SetupAgentDialog } from "@/components/repos/SetupAgentDialog";
import { SetupProposalCard } from "@/components/repos/SetupProposalCard";
import { EASE_STANDARD, EASE_TRAVEL } from "@/lib/motion";
import { providerLabel } from "@/lib/status";
import { cn } from "@/lib/utils";

const TITLE = "Getting this repository ready";

const STATES: Record<ReadinessRowState, { icon: LucideIcon; tone: string; label: string }> = {
  done: { icon: Check, tone: "text-success", label: "Done" },
  missing: { icon: Circle, tone: "text-fg-muted", label: "To do" },
  in_progress: { icon: Loader2, tone: "text-info animate-spin", label: "In progress" },
  unknown: { icon: HelpCircle, tone: "text-fg-muted", label: "Not known yet" },
  needs_admin: { icon: ShieldAlert, tone: "text-warning", label: "Needs an admin" },
  review: { icon: Eye, tone: "text-info", label: "Ready to review" },
  failed: { icon: AlertTriangle, tone: "text-destructive", label: "Did not work" },
  needs_fix: { icon: AlertTriangle, tone: "text-warning", label: "Needs a fix" },
  off: { icon: MinusCircle, tone: "text-fg-muted", label: "Off" },
  waiting: { icon: Clock, tone: "text-fg-muted", label: "Waiting" },
};

/** The ready-to-send message for an admin, for a step only an admin can do. */
function adminRequest(row: ReadinessRow, repo: string, forge: Forge): string {
  if (row.key === "work_trigger") {
    const owner = repo.split("/")[0];
    return `Please add the embry0 connection for ${repo}: open Organizations, choose ${owner}, and use Add webhook.`;
  }
  return (
    `Please connect a ${providerLabel(forge)} account that can read and push to ${repo} in embry0. ` +
    `Open Credentials, add the account, then pick it on ${window.location.origin}/repos/${repo} ` +
    "under Advanced settings, Accounts."
  );
}

async function copyAdminRequest(text: string) {
  try {
    await navigator.clipboard.writeText(text);
    toast.success("Copied. Send it to your embry0 admin.");
  } catch {
    // An insecure origin has no clipboard: hand the person the text instead.
    toast.error("Could not copy the request. Send this to your embry0 admin:", { description: text });
  }
}

/** Where a link action goes; null for an action that is a button on this page. */
function actionHref(row: ReadinessRow, repo: string): string | null {
  switch (row.action?.kind) {
    case "open_environment":
      return `/environments?repo=${encodeURIComponent(repo)}`;
    case "view_job":
      return row.job_id ? `/jobs/${row.job_id}` : null;
    default:
      return null;
  }
}

/** Where a row sits on the setup line: passed by it, or where it stops. */
type LinePlace = "passed" | "stop";

/** A step behind the person: done, or testing turned off on purpose (the readiness rules count both as settled). */
const SETTLED: ReadonlySet<ReadinessRowState> = new Set(["done", "off"]);

/**
 * The row the setup line stops at: the first required step not settled yet,
 * or -1 when every required one is and the line runs to the last row. An
 * optional step never stops it.
 */
function lineStop(rows: readonly ReadinessRow[]): number {
  return rows.findIndex((row) => row.required && !SETTLED.has(row.state));
}

/** The whole list's draw; a shorter stretch takes its share (index.css --motion-draw). */
const DRAW_MS = 1400;
/** No stretch draws quicker than this, so a short one still reads as travel. */
const MIN_DRAW_MS = 400;
/** A check drawing itself, and the ring arriving (index.css --motion-base). */
const MARK_MS = 280;
/** The check's dash, a little longer than lucide's tick (22.6 units): offset by it, the tick is hidden. */
const CHECK_DASH = 24;
/** The stop ring's radius: the line ends at the ring, not inside it. */
const RING_GAP = 12;

const [X1, Y1, X2, Y2] = (EASE_TRAVEL.match(/[\d.]+/g) ?? []).map(Number);

function bezier(s: number, p1: number, p2: number): number {
  const r = 1 - s;
  return 3 * r * r * s * p1 + 3 * r * s * s * p2 + s * s * s;
}

/** The share of its time a line easing on --ease-travel takes to cover `share` of its way. */
function reachedAt(share: number): number {
  let lo = 0;
  let hi = 1;
  for (let i = 0; i < 24; i++) {
    const mid = (lo + hi) / 2;
    if (bezier(mid, Y1, Y2) < share) lo = mid;
    else hi = mid;
  }
  return bezier((lo + hi) / 2, X1, X2);
}

const NO_ROWS: readonly ReadinessRow[] = [];

/**
 * The thin line down the checklist's icon column. It runs through the
 * settled steps and stops at the next one, and it reports progress: on
 * first view it draws from the top to its stop, each done step's check
 * drawing as the line reaches it; when a refetch settles a step, only the
 * new stretch draws on. Reduced motion gets the line where it ends.
 *
 * Layout is read when the rows change or the list resizes, never per frame.
 * The line and the checks are whole in the page's own styles: an animation
 * only hides them while it plays, so nothing stays hidden if one fails.
 */
function useSetupLine(rows: readonly ReadinessRow[]) {
  const boxRef = useRef<HTMLDivElement>(null);
  const lineRef = useRef<HTMLSpanElement>(null);
  // What the line last showed: the row it ran to and each row's state.
  const shown = useRef<{ end: number; states: Map<string, ReadinessRowState> } | null>(null);

  useLayoutEffect(() => {
    const box = boxRef.current;
    const line = lineRef.current;
    if (!box || !line || rows.length === 0) return;
    const icons = [...box.querySelectorAll<HTMLElement>("[data-setup-icon]")];
    if (icons.length !== rows.length) return;
    const stop = lineStop(rows);
    const end = stop < 0 ? rows.length - 1 : stop;
    const gap = stop < 0 ? 0 : RING_GAP;

    // Sets the line from the first icon's centre to its end, and returns
    // each icon's centre down from the first one's.
    const place = (): number[] => {
      const top = box.getBoundingClientRect().top;
      const centres = icons.map((icon) => {
        const r = icon.getBoundingClientRect();
        return r.top + r.height / 2 - top;
      });
      line.style.top = `${centres[0]}px`;
      line.style.height = `${Math.max(0, centres[end] - centres[0] - gap)}px`;
      return centres.map((c) => c - centres[0]);
    };

    const d = place();
    const before = shown.current;
    shown.current = { end, states: new Map(rows.map((row) => [row.key, row.state])) };

    if (!prefersReducedMotion() && typeof line.animate === "function") {
      const length = Math.max(0, d[end] - gap);
      // A first view draws from the top; a refetch that moved the stop down
      // draws on from where the line ended (at the old stop's ring).
      const from = before === null ? 0 : before.end < end ? Math.max(0, d[before.end] - RING_GAP) : length;
      const travel = length - from;
      const full = d[d.length - 1];
      const duration = travel > 0 && full > 0 ? Math.max(MIN_DRAW_MS, (DRAW_MS * travel) / full) : 0;
      if (duration > 0) {
        line.animate([{ transform: `scaleY(${from / length})` }, { transform: "scaleY(1)" }], {
          duration,
          easing: EASE_TRAVEL,
        });
      }
      rows.forEach((row, i) => {
        if (row.state !== "done") return;
        // First view: the checks the line passes. Later: a step that just turned done.
        if (before === null ? i > end : before.states.get(row.key) === "done") return;
        const onWay = duration > 0 && i <= end && d[i] > from;
        const delay = onWay ? reachedAt(Math.min(1, (d[i] - from) / travel)) * duration : 0;
        icons[i].querySelector("path")?.animate(
          [{ strokeDashoffset: `${-CHECK_DASH}` }, { strokeDashoffset: "0" }],
          { duration: MARK_MS, delay, easing: EASE_STANDARD, fill: "backwards" },
        );
      });
      if (stop >= 0 && (before === null || before.end !== end)) {
        icons[stop].querySelector("[data-setup-ring]")?.animate(
          [{ opacity: 0, transform: "scale(0.6)" }, { opacity: 1, transform: "scale(1)" }],
          { duration: MARK_MS, delay: duration, easing: EASE_STANDARD, fill: "backwards" },
        );
      }
    }

    // A row growing (a card opening under it, text rewrapping) moves the icons.
    const observer = new ResizeObserver(() => place());
    observer.observe(box);
    return () => observer.disconnect();
  }, [rows]);

  return { boxRef, lineRef };
}

function StateIcon({ state, line }: { state: ReadinessRowState; line?: LinePlace }) {
  const { icon: Icon, tone, label } = STATES[state] ?? STATES.unknown;
  // The disc in the card's colour hides the setup line behind the icon.
  return (
    <span
      className="relative mt-0.5 shrink-0 self-start rounded-full bg-card"
      title={label}
      data-setup-icon
      data-line={line}
    >
      {line === "stop" && (
        <span
          data-setup-ring
          className="absolute -inset-1 rounded-full ring-1 ring-fg-muted ring-inset"
          aria-hidden="true"
        />
      )}
      <Icon
        className={cn("h-4 w-4", tone)}
        aria-hidden="true"
        strokeDasharray={state === "done" ? CHECK_DASH : undefined}
      />
      <span className="sr-only">{label}</span>
    </span>
  );
}

interface ChecklistRowProps {
  row: ReadinessRow;
  line?: LinePlace;
  gold: boolean;
  repo: string;
  forge: Forge;
  skipping: boolean;
  onSkip: () => void;
  onStartSetup: () => void;
  onFirstTask: () => void;
  /** The setup proposal card is open under this row (testing only). */
  cardOpen: boolean;
  onToggleCard: () => void;
}

function ChecklistRow({
  row,
  line,
  gold,
  repo,
  forge,
  skipping,
  onSkip,
  onStartSetup,
  onFirstTask,
  cardOpen,
  onToggleCard,
}: ChecklistRowProps) {
  const action = row.action;
  const href = action ? actionHref(row, repo) : null;
  const variant = gold ? "default" : "outline";
  const showsCard = row.key === "testing" && cardOpen;
  const failed = row.key === "testing" && row.state === "failed";
  // An open card on a failed run carries Try again and Skip itself.
  const cardActs = showsCard && failed;
  const canSkip = row.key === "testing" && (row.state === "missing" || failed) && !cardActs;

  return (
    <li className="py-3" data-testid={`setup-row-${row.key}`} data-state={row.state}>
      <div className="flex gap-3">
        <StateIcon state={row.state} line={line} />
        {/* Below lg the actions sit under the text: beside it, two buttons left the
            title a few pixels wide on a phone and pushed one past the card. */}
        <div className="flex min-w-0 flex-1 flex-col gap-2 lg:flex-row lg:gap-3">
          <div className="min-w-0 flex-1 space-y-0.5">
            <p className="text-sm font-semibold text-fg">
              {row.title}
              {!row.required && <span className="ml-2 text-xs font-normal text-fg-muted">Optional</span>}
            </p>
            <p className="text-sm text-fg-secondary">{row.says}</p>
          </div>
          <div className="flex flex-wrap items-start gap-2 lg:shrink-0 lg:justify-end">
            {action?.kind === "copy_admin_request" && (
              <Button variant="outline" size="sm" onClick={() => void copyAdminRequest(adminRequest(row, repo, forge))}>
                {action.label}
              </Button>
            )}
            {(action?.kind === "start_setup" || action?.kind === "fix_setup") && !cardActs && (
              <Button variant={variant} size="sm" onClick={onStartSetup}>
                {action.label}
              </Button>
            )}
            {action?.kind === "create_issue" && (
              <Button variant={variant} size="sm" onClick={onFirstTask}>
                {action.label}
              </Button>
            )}
            {action?.kind === "review_proposal" && (
              <Button variant={variant} size="sm" aria-expanded={cardOpen} onClick={onToggleCard}>
                {action.label}
              </Button>
            )}
            {action && href && (
              <Link to={href} className={cn(buttonVariants({ variant, size: "sm" }))}>
                {action.label}
              </Link>
            )}
            {canSkip && (
              <Button variant="outline" size="sm" disabled={skipping} onClick={onSkip}>
                Skip testing for now
              </Button>
            )}
            {failed && (
              <Button variant="ghost" size="sm" aria-expanded={cardOpen} onClick={onToggleCard}>
                See what happened
              </Button>
            )}
            {row.key === "first_task" && row.state === "done" && row.pr_url && (
              <a
                href={row.pr_url}
                target="_blank"
                rel="noreferrer"
                className={cn(buttonVariants({ variant: "outline", size: "sm" }))}
              >
                Open the pull request
              </a>
            )}
          </div>
        </div>
      </div>
      {showsCard && (
        <div className="pl-7">
          <SetupProposalCard
            repo={repo}
            onClose={onToggleCard}
            onTryAgain={onStartSetup}
            onSkip={onSkip}
            skipping={skipping}
          />
        </div>
      )}
    </li>
  );
}

const CHIPS: Record<Exclude<Readiness["status"], "steps_left">, { label: string; tone: BadgeTone }> = {
  ready: { label: "Ready", tone: "success" },
  needs_admin: { label: "Needs an admin", tone: "warning" },
  setup_to_review: { label: "Setup to review", tone: "info" },
};

/** The repo header's one status chip, from the same setup status. */
export function ReadinessChip({ readiness }: { readiness: Readiness }) {
  const chip =
    readiness.status === "steps_left"
      ? { label: `${readiness.steps_left} ${readiness.steps_left === 1 ? "step" : "steps"} left`, tone: "neutral" as const }
      : (CHIPS[readiness.status] ?? { label: readiness.status, tone: "neutral" as const });
  return (
    <span data-testid="repo-readiness-chip">
      <Badge tone={chip.tone}>{chip.label}</Badge>
    </span>
  );
}

/**
 * The repo page's opening card: what is done, what is left and the one next
 * step, from the readiness rules (no model call). The next step's action is
 * the page's gold, except a request to an admin, which stays quiet: the
 * person cannot do that step themselves. While the setup proposal card is
 * open, its own main action is the gold and every row action is quiet.
 */
export function SetupChecklist({ repo, forge }: { repo: string; forge: Forge }) {
  const readiness = useRepoReadiness(repo);
  const skip = useSkipTesting(repo);
  const queryClient = useQueryClient();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [firstTaskOpen, setFirstTaskOpen] = useState(false);
  const [cardOpen, setCardOpen] = useState(false);
  const data = readiness.data;
  const { boxRef, lineRef } = useSetupLine(data?.rows ?? NO_ROWS);

  function onSkip() {
    if (skip.isPending) return;
    skip.mutate(undefined, {
      onSuccess: () => {
        setCardOpen(false);
        toast.success("Testing is off for now. embry0 opens pull requests without testing the app.");
      },
      onError: (error) => toastApiError(error, "Could not skip testing"),
    });
  }

  if (!data) {
    return (
      <Card className="space-y-2 p-4" data-testid="setup-checklist" aria-busy={!readiness.isError}>
        <h2 className="text-section font-semibold">{TITLE}</h2>
        {readiness.isError ? (
          <div className="flex flex-wrap items-center gap-3">
            <p className="text-sm text-fg-secondary">Could not load the setup status.</p>
            <Button variant="outline" size="sm" onClick={() => void readiness.refetch()}>
              Retry
            </Button>
          </div>
        ) : (
          <div className="space-y-2">
            <div className="h-4 w-2/3 motion-safe:animate-pulse rounded bg-muted/60" />
            <div className="h-10 w-full motion-safe:animate-pulse rounded bg-muted/60" />
          </div>
        )}
      </Card>
    );
  }

  const lineAt = lineStop(data.rows);

  return (
    <>
    <Card className="space-y-3 p-4" data-testid="setup-checklist">
      <div className="space-y-1">
        <h2 className="text-section font-semibold">{TITLE}</h2>
        <p className="text-sm text-fg">{data.summary}</p>
        <p className="text-sm text-fg-muted">
          embry0 works on its own branch and opens a pull request for you to review. It never merges your code.
        </p>
      </div>
      <div ref={boxRef} className="relative">
        <span
          ref={lineRef}
          className="pointer-events-none absolute left-[7.5px] w-px origin-top bg-border-strong"
          aria-hidden="true"
          data-testid="setup-line"
        />
        <ol className="divide-y divide-border">
          {data.rows.map((row, i) => (
            <ChecklistRow
              key={row.key}
              row={row}
              line={i === lineAt ? "stop" : lineAt < 0 || i < lineAt ? "passed" : undefined}
              gold={!cardOpen && row.key === data.next_step && row.action?.kind !== "copy_admin_request"}
              repo={repo}
              forge={forge}
              skipping={skip.isPending}
              onSkip={onSkip}
              onStartSetup={() => setDialogOpen(true)}
              onFirstTask={() => setFirstTaskOpen(true)}
              cardOpen={cardOpen}
              onToggleCard={() => setCardOpen((open) => !open)}
            />
          ))}
        </ol>
      </div>
      <div className="flex flex-wrap gap-4 border-t border-border pt-3 text-sm">
        <a href="#advanced" className="text-fg-secondary hover:text-fg hover:underline">
          Advanced settings
        </a>
        <a href="#history" className="text-fg-secondary hover:text-fg hover:underline">
          History
        </a>
      </div>
    </Card>
    {dialogOpen && (
      <SetupAgentDialog
        repo={repo}
        onStarted={() => setCardOpen(false)}
        onClose={() => setDialogOpen(false)}
      />
    )}
    {firstTaskOpen && (
      <CreateIssueDialog
        initialRepo={repo}
        onCreated={() => void queryClient.invalidateQueries({ queryKey: ["repo-readiness", repo] })}
        onClose={() => setFirstTaskOpen(false)}
      />
    )}
    </>
  );
}
