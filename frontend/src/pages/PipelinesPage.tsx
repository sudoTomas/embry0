import { PipelineEditor } from "@/components/pipeline-editor/PipelineEditor";
import { PipelineTree } from "@/components/pipeline-tree";
import { DEFAULT_ISSUE_TO_PR_PHASES } from "@/lib/pipeline-phases";

export function PipelinesPage() {
  return (
    <div className="flex flex-col gap-6 h-full">
      {/* Default Pipeline Preview */}
      <div className="legion-card p-6">
        <PipelineTree
          phases={DEFAULT_ISSUE_TO_PR_PHASES}
          nodeStates={{}}
          title="Issue-to-PR Pipeline"
        />
      </div>

      {/* Pipeline Editor */}
      <div className="flex-1 min-h-0 -mx-[var(--density-padding)]">
        <PipelineEditor mode="page" />
      </div>
    </div>
  );
}
