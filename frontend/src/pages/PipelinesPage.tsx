import { PipelineEditor } from "@/components/pipeline-editor/PipelineEditor";

export function PipelinesPage() {
  return (
    <div className="h-full -m-[var(--density-padding)]">
      <PipelineEditor mode="page" />
    </div>
  );
}
