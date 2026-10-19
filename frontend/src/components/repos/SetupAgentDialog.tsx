import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { X } from "lucide-react";
import { apiErrorMessage } from "@/api/errorDetail";
import type { SetupCost } from "@/api/repoSetup";
import { useSetupCost, useStartSetup } from "@/hooks/useSetupProposal";
import { Button } from "@/components/ui/Button";
import { Overlay } from "@/components/ui/Overlay";
import { createFocusTrap } from "@/lib/focus-trap";
import { formatCost } from "@/lib/utils";

const TITLE = "Let embry0 set up testing";

/** Recorded facts only: past runs, the per-job budget and who pays. Never an estimate. */
function CostFacts({ cost }: { cost: SetupCost }) {
  let runs = "No setup run is recorded here yet.";
  if (cost.recorded_runs > 0) {
    runs =
      cost.typical_cost_usd === null
        ? `Setup runs recorded on this embry0: ${cost.recorded_runs}.`
        : `Setup runs recorded on this embry0: ${cost.recorded_runs}, typical cost ${formatCost(cost.typical_cost_usd)}.`;
  }
  return (
    <>
      <p>{runs}</p>
      <p>Your per-job budget is {formatCost(cost.per_job_budget_usd)}.</p>
      {cost.paid_from && <p>Paid from: {cost.paid_from}</p>}
    </>
  );
}

/**
 * Consent before the onboarding agent runs: what it will do, what it will
 * not touch, and what setup runs have cost here. Only Start starts a run,
 * and Start waits until the cost facts are on screen.
 */
export function SetupAgentDialog({
  repo,
  onClose,
  onStarted,
}: {
  repo: string;
  onClose: () => void;
  /** Called once the run has started, before the dialog closes. */
  onStarted?: () => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const cost = useSetupCost(repo);
  const start = useStartSetup(repo);
  const [refusal, setRefusal] = useState<string | null>(null);

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  useEffect(() => {
    if (!containerRef.current) return;
    document.body.style.overflow = "hidden";
    const cleanup = createFocusTrap(containerRef.current);
    return () => {
      cleanup();
      document.body.style.overflow = "";
    };
  }, []);

  function onStart() {
    if (start.isPending) return;
    setRefusal(null);
    start.mutate(undefined, {
      onSuccess: () => {
        toast.success("embry0 is setting up testing. You can leave this page.");
        onStarted?.();
        onClose();
      },
      onError: (error) => setRefusal(apiErrorMessage(error)),
    });
  }

  return (
    <Overlay
      className="flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={containerRef}
        role="dialog"
        aria-modal="true"
        aria-label={TITLE}
        className="relative max-h-full w-full max-w-md overflow-y-auto overscroll-contain rounded-lg border border-border bg-card p-6 shadow-xl"
      >
        <button
          type="button"
          onClick={onClose}
          aria-label="Close"
          className="absolute top-4 right-4 cursor-pointer text-muted-foreground transition-colors hover:text-foreground"
        >
          <X className="h-4 w-4" />
        </button>

        <h2 className="mb-4 text-section font-semibold">{TITLE}</h2>

        <div className="space-y-3 text-sm text-fg-secondary">
          <p>
            What embry0 will do: copy your code to a private test computer, read it, write a test setup, then start
            your app there and check that it answers. It tries up to three times.
          </p>
          <p>
            What it will not do: change your code, open a pull request, or change any setting. You get a proposal to
            review first.
          </p>
          <div
            className="space-y-1 rounded-md border border-border p-3"
            data-testid="setup-cost"
            aria-busy={cost.isPending}
          >
            {cost.data ? (
              <CostFacts cost={cost.data} />
            ) : cost.isError ? (
              <div className="flex flex-wrap items-center gap-3">
                <p>Could not load what setup runs cost here.</p>
                <Button variant="outline" size="sm" onClick={() => void cost.refetch()}>
                  Retry
                </Button>
              </div>
            ) : (
              <div className="h-10 w-full motion-safe:animate-pulse rounded bg-muted/60" />
            )}
          </div>
          {refusal && (
            <p role="alert" className="text-destructive">
              {refusal}
            </p>
          )}
        </div>

        <div className="flex justify-end gap-2 pt-5">
          <Button type="button" variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button type="button" disabled={!cost.data || start.isPending} onClick={onStart}>
            Start
          </Button>
        </div>
      </div>
    </Overlay>
  );
}
