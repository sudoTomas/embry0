import { Link } from "react-router";
import { buttonVariants } from "@/components/ui/Button";
import { cn } from "@/lib/utils";

interface OnboardJob {
  pipeline_template?: string | null;
  status: string;
  repo?: string | null;
}

/**
 * #29: where to go once an onboard job has finished. A completed job stored
 * the repo's QA config; a failed one did not, and its error is in the job's
 * error banner. The agent's draft lives only in the workflow state and is not
 * served by the job API, so the manual form opens with the repo alone.
 */
export function OnboardOutcomeCard({ job }: { job: OnboardJob }) {
  if (job.pipeline_template !== "onboard" || !job.repo) return null;
  const enc = encodeURIComponent(job.repo);

  if (job.status === "completed") {
    return (
      <div className="flex items-center gap-3 text-sm" data-testid="onboard-outcome">
        <span className="text-white/60">The agent stored a validated QA config for {job.repo}.</span>
        <Link
          to={`/qa/repos/${enc}/config`}
          className={cn(buttonVariants({ size: "sm" }))}
          data-testid="onboard-outcome-config-cta"
        >
          Open the QA config
        </Link>
      </div>
    );
  }

  if (job.status === "failed") {
    return (
      <div className="flex items-center gap-3 text-sm" data-testid="onboard-outcome">
        <span className="text-white/60">No config was stored. You can write a starter config yourself.</span>
        <Link
          to={`/repos/onboard?repo=${enc}&mode=manual`}
          className={cn(buttonVariants({ variant: "outline", size: "sm" }))}
          data-testid="onboard-outcome-manual-cta"
        >
          Configure manually
        </Link>
      </div>
    );
  }

  return null;
}
