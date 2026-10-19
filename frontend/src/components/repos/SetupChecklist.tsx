import { Link } from "react-router";
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
import { useRepoReadiness, useSkipTesting } from "@/hooks/useRepoReadiness";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { Button, buttonVariants } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
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

/** Where a link action goes; null for an action this page cannot place. */
function actionHref(row: ReadinessRow, repo: string): string | null {
  const encoded = encodeURIComponent(repo);
  switch (row.action?.kind) {
    case "open_environment":
      return `/environments?repo=${encoded}`;
    case "start_setup":
    case "fix_setup":
      return `/repos/onboard?repo=${encoded}`;
    case "create_issue":
      return "/issues";
    case "view_job":
    case "review_proposal":
      // Until the review card lands, the proposal is read on its job's page.
      return row.job_id ? `/jobs/${row.job_id}` : null;
    default:
      return null;
  }
}

function StateIcon({ state }: { state: ReadinessRowState }) {
  const { icon: Icon, tone, label } = STATES[state] ?? STATES.unknown;
  return (
    <span className="mt-0.5 shrink-0" title={label}>
      <Icon className={cn("h-4 w-4", tone)} aria-hidden="true" />
      <span className="sr-only">{label}</span>
    </span>
  );
}

interface ChecklistRowProps {
  row: ReadinessRow;
  gold: boolean;
  repo: string;
  forge: Forge;
  skipping: boolean;
  onSkip: () => void;
}

function ChecklistRow({ row, gold, repo, forge, skipping, onSkip }: ChecklistRowProps) {
  const action = row.action;
  const href = action ? actionHref(row, repo) : null;
  const canSkip = row.key === "testing" && (row.state === "missing" || row.state === "failed");

  return (
    <li className="flex gap-3 py-3" data-testid={`setup-row-${row.key}`} data-state={row.state}>
      <StateIcon state={row.state} />
      <div className="min-w-0 flex-1 space-y-0.5">
        <p className="text-sm font-semibold text-fg">
          {row.title}
          {!row.required && <span className="ml-2 text-xs font-normal text-fg-muted">Optional</span>}
        </p>
        <p className="text-sm text-fg-secondary">{row.says}</p>
      </div>
      <div className="flex shrink-0 flex-wrap items-start justify-end gap-2">
        {action?.kind === "copy_admin_request" && (
          <Button variant="outline" size="sm" onClick={() => void copyAdminRequest(adminRequest(row, repo, forge))}>
            {action.label}
          </Button>
        )}
        {action && href && (
          <Link to={href} className={cn(buttonVariants({ variant: gold ? "default" : "outline", size: "sm" }))}>
            {action.label}
          </Link>
        )}
        {canSkip && (
          <Button variant="outline" size="sm" disabled={skipping} onClick={onSkip}>
            Skip testing for now
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
 * person cannot do that step themselves.
 */
export function SetupChecklist({ repo, forge }: { repo: string; forge: Forge }) {
  const readiness = useRepoReadiness(repo);
  const skip = useSkipTesting(repo);
  const data = readiness.data;

  function onSkip() {
    if (skip.isPending) return;
    skip.mutate(undefined, {
      onSuccess: () => toast.success("Testing is off for now. embry0 opens pull requests without testing the app."),
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
            <div className="h-4 w-2/3 animate-pulse rounded bg-muted/60" />
            <div className="h-10 w-full animate-pulse rounded bg-muted/60" />
          </div>
        )}
      </Card>
    );
  }

  return (
    <Card className="space-y-3 p-4" data-testid="setup-checklist">
      <div className="space-y-1">
        <h2 className="text-section font-semibold">{TITLE}</h2>
        <p className="text-sm text-fg">{data.summary}</p>
        <p className="text-sm text-fg-muted">
          embry0 works on its own branch and opens a pull request for you to review. It never merges your code.
        </p>
      </div>
      <ol className="divide-y divide-border">
        {data.rows.map((row) => (
          <ChecklistRow
            key={row.key}
            row={row}
            gold={row.key === data.next_step && row.action?.kind !== "copy_admin_request"}
            repo={repo}
            forge={forge}
            skipping={skip.isPending}
            onSkip={onSkip}
          />
        ))}
      </ol>
      <div className="flex flex-wrap gap-4 border-t border-border pt-3 text-sm">
        <a href="#advanced" className="text-fg-secondary hover:text-fg hover:underline">
          Advanced settings
        </a>
        <a href="#history" className="text-fg-secondary hover:text-fg hover:underline">
          History
        </a>
      </div>
    </Card>
  );
}
