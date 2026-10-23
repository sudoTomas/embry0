import { Link } from "react-router";
import { buttonVariants } from "@/components/ui/Button";
import { cn } from "@/lib/utils";

interface OnboardJob {
  pipeline_template?: string | null;
  status: string;
  repo?: string | null;
  pipeline_config?: Record<string, unknown> | null;
}

const BOX = "flex flex-wrap items-center gap-3 rounded-lg border border-border bg-card px-4 py-3 text-sm";

/**
 * #29: where to go once an onboard job has finished. A completed job stored
 * the repo's test setup, or, in propose mode (ADR 0019), a proposal for the
 * repository page; a failed one stored nothing, and its error is in the job's
 * error banner. The agent's draft lives only in the workflow state and is not
 * served by the job API, so the by-hand form opens with the repo alone.
 */
export function OnboardOutcomeCard({ job }: { job: OnboardJob }) {
  if (job.pipeline_template !== "onboard" || !job.repo) return null;
  const enc = encodeURIComponent(job.repo);
  const proposal = !!job.pipeline_config?.proposal_id;

  if (job.status === "completed") {
    return (
      <div className={BOX} data-testid="onboard-outcome">
        <span className="text-muted-foreground">
          {proposal
            ? `The agent proposed a test setup for ${job.repo}.`
            : `The agent stored a validated test setup for ${job.repo}.`}
        </span>
        {proposal ? (
          <Link
            to={`/repos/${job.repo}`}
            className={cn(buttonVariants({ size: "sm" }))}
            data-testid="onboard-outcome-proposal-cta"
          >
            Review the proposal
          </Link>
        ) : (
          <Link
            to={`/qa/repos/${enc}/config`}
            className={cn(buttonVariants({ size: "sm" }))}
            data-testid="onboard-outcome-config-cta"
          >
            Open the test setup
          </Link>
        )}
      </div>
    );
  }

  if (job.status === "failed") {
    return (
      <div className={BOX} data-testid="onboard-outcome">
        <span className="text-muted-foreground">No test setup was stored. You can fill in the form yourself.</span>
        <Link
          to={`/repos/onboard?repo=${enc}&mode=manual`}
          className={cn(buttonVariants({ variant: "outline", size: "sm" }))}
          data-testid="onboard-outcome-manual-cta"
        >
          Set up by hand
        </Link>
      </div>
    );
  }

  return null;
}
