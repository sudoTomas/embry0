import { PipelineTree } from "@/components/pipeline-tree";
import { DEFAULT_ISSUE_TO_PR_PHASES } from "@/lib/pipeline-phases";
import { IconBox } from "@/components/ui/IconBox";
import { Info } from "lucide-react";

export function PipelinesPage() {
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Pipelines</h1>

      {/* Default Pipeline Preview */}
      <div className="legion-card p-6">
        <PipelineTree
          phases={DEFAULT_ISSUE_TO_PR_PHASES}
          nodeStates={{}}
          title="Issue-to-PR Pipeline"
        />
      </div>

      {/* Info note */}
      <div className="flex items-start gap-3 px-4 py-3 rounded-xl border border-white/[0.06] bg-white/[0.02]">
        <IconBox icon={Info} color="#06b6d4" size="sm" className="mt-0.5" />
        <div>
          <p className="text-sm text-white/50">
            This is the default issue-to-PR pipeline. Custom pipeline templates can be created and managed via the API.
          </p>
          <p className="text-xs text-white/30 mt-1">
            Click any agent node to view its configuration details.
          </p>
        </div>
      </div>
    </div>
  );
}
