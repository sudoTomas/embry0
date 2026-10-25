import { useState, useEffect } from "react";
import { Pause, Play, MessageSquare, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/Button";
import type { PauseBudget } from "@/lib/types";

interface PausedBannerProps {
  jobId: string;
  reason: string;
  retryCount?: number;
  latestReview?: string;
  /** A job over its budget: a continue lets it spend `continue_adds` more. */
  budget?: PauseBudget;
  prUrl?: string;
  pausedAt?: string;
  ttlHours?: number;
  onResume: (choice: string, guidance?: string) => void;
  onDiscard: () => void;
}

export function PausedBanner({
  reason,
  retryCount,
  latestReview,
  budget,
  pausedAt,
  ttlHours = 48,
  onResume,
  onDiscard,
}: PausedBannerProps) {
  const [showGuidance, setShowGuidance] = useState(false);
  const [guidance, setGuidance] = useState("");
  const [timeRemaining, setTimeRemaining] = useState("");

  useEffect(() => {
    if (!pausedAt) return;
    const update = () => {
      const paused = new Date(pausedAt).getTime();
      const expiry = paused + ttlHours * 3600 * 1000;
      const remaining = expiry - Date.now();
      if (remaining <= 0) {
        setTimeRemaining("Expired");
        return;
      }
      const hours = Math.floor(remaining / 3600000);
      const mins = Math.floor((remaining % 3600000) / 60000);
      setTimeRemaining(`${hours}h ${mins}m remaining`);
    };
    update();
    const interval = setInterval(update, 60000);
    return () => clearInterval(interval);
  }, [pausedAt, ttlHours]);

  // `reason` is the interrupt event's reason when the viewer received it live,
  // otherwise the job's current_stage — never guess, an operator picks their
  // recovery from this line and the wrong guess sends them the wrong way.
  const reasonText = reason === "max_retries"
    ? `Max retries reached${retryCount ? ` (${retryCount}/3)` : ""}`
    : reason === "budget_exceeded"
      ? "Budget exceeded"
      : reason === "dev_branch_missing"
        ? "Developer pushed no branch — resume to commit and push the existing work"
        : reason === "review_errored"
          ? "Review could not complete"
          : reason || "Pipeline paused";

  return (
    <div className="rounded-lg border border-amber-500/20 bg-amber-500/[0.06] overflow-hidden">
      {/* Header */}
      <div className="px-4 py-3 flex items-center gap-3">
        <Pause className="w-4 h-4 text-amber-400" />
        <div className="flex-1">
          <div className="text-sm font-medium text-amber-300">{reasonText}</div>
          {timeRemaining && (
            <div className="text-xs text-amber-400/60 mt-0.5">{timeRemaining}</div>
          )}
        </div>
      </div>

      {budget && (
        <div className="px-4 pb-3 text-xs text-amber-200/80" data-testid="paused-budget">
          Over budget: ${budget.spent.toFixed(2)} spent of ${budget.cap.toFixed(2)}. Continue lets it spend $
          {budget.continue_adds.toFixed(2)} more.
        </div>
      )}

      {/* Review feedback if available */}
      {latestReview && (
        <div className="px-4 pb-3 text-xs text-white/50 leading-relaxed border-t border-amber-500/10 pt-2">
          <div className="font-medium text-white/60 mb-1">Review feedback:</div>
          <div className="whitespace-pre-wrap max-h-32 overflow-y-auto">{latestReview.slice(0, 500)}</div>
        </div>
      )}

      {/* Actions */}
      <div className="px-4 py-3 flex items-center gap-2 border-t border-amber-500/10 bg-amber-500/[0.03]">
        <Button
          size="sm"
          onClick={() => onResume("continue_retrying", guidance || undefined)}
          className="gap-1.5"
        >
          <Play className="w-3 h-3" /> Continue
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setShowGuidance(!showGuidance)}
          className="gap-1.5 text-white/60"
        >
          <MessageSquare className="w-3 h-3" /> Add Guidance
        </Button>
        <div className="flex-1" />
        <Button
          variant="ghost"
          size="sm"
          onClick={onDiscard}
          className="gap-1.5 text-destructive/60 hover:text-destructive"
        >
          <Trash2 className="w-3 h-3" /> Discard
        </Button>
      </div>

      {/* Guidance input */}
      {showGuidance && (
        <div className="px-4 pb-3">
          <textarea
            value={guidance}
            onChange={(e) => setGuidance(e.target.value)}
            placeholder="e.g., Focus on the factorial edge case, ignore the README for now..."
            className="w-full bg-black/30 border border-white/10 rounded-md px-3 py-2 text-sm text-white/80 placeholder:text-white/20 resize-none h-20 focus:outline-none focus:border-primary/50"
          />
          <div className="flex justify-end mt-2">
            <Button
              size="sm"
              onClick={() => onResume("continue_retrying", guidance)}
              className="gap-1.5"
            >
              <Play className="w-3 h-3" /> Resume with Guidance
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
