import { Link } from "react-router";
import { Workflow, CircleDot, Plus } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { IconBox } from "@/components/ui/IconBox";

interface EmptyJobsStateProps {
  /** "jobs" (default) keeps the JobsPage copy; "console" is the live board's
   * "Nothing running — dispatch a job" variant with a New Job action. */
  variant?: "jobs" | "console";
  /** Console variant only: opens the board's New Job form. */
  onNewJob?: () => void;
}

export function EmptyJobsState({ variant = "jobs", onNewJob }: EmptyJobsStateProps) {
  if (variant === "console") {
    return (
      <div className="flex flex-col items-center justify-center py-16 px-4">
        <div className="mb-6">
          <IconBox icon={Workflow} color="#06b6d4" size="lg" />
        </div>
        <h3 className="text-lg font-semibold text-white/70 mb-2">
          Nothing running — dispatch a job
        </h3>
        <p className="text-sm text-white/30 text-center max-w-sm mb-6">
          Dispatched jobs show up here live — operator sessions, issue pipelines, all of it.
        </p>
        <Button size="sm" className="gap-1.5" onClick={onNewJob}>
          <Plus className="w-3.5 h-3.5" />
          New Job
        </Button>
      </div>
    );
  }

  return (
    <div className="flex flex-col items-center justify-center py-16 px-4">
      <div className="mb-6">
        <IconBox icon={Workflow} color="#06b6d4" size="lg" />
      </div>
      <h3 className="text-lg font-semibold text-white/70 mb-2">No jobs yet</h3>
      <p className="text-sm text-white/30 text-center max-w-sm mb-6">
        Jobs are created when GitHub issues are labeled or via the API. Create an issue to get started.
      </p>
      <div className="flex gap-3">
        <Link to="/issues">
          <Button variant="outline" size="sm" className="gap-1.5">
            <CircleDot className="w-3.5 h-3.5" />
            Create your first issue
          </Button>
        </Link>
      </div>
    </div>
  );
}
