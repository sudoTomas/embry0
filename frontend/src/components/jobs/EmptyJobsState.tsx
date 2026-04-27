import { Link } from "react-router";
import { Workflow, CircleDot } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { IconBox } from "@/components/ui/IconBox";

export function EmptyJobsState() {
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
