import type { Node, Edge } from "@xyflow/react";
import { cn } from "@/lib/utils";

interface ValidationBarProps {
  nodes: Node[];
  edges: Edge[];
  onApply: () => void;
  onSaveTemplate?: () => void;
}

export function ValidationBar({
  nodes,
  edges,
  onSaveTemplate,
}: ValidationBarProps) {
  const feedbackCount = edges.filter(
    (e) =>
      (e.data as Record<string, unknown> | undefined)?.edgeType === "feedback",
  ).length;

  const hasNodes = nodes.length > 0;
  const isValid = hasNodes;
  const validationMessage = "Add at least one node";

  return (
    <div className="h-11 shrink-0 border-t border-white/[0.06] bg-[#0a0c14] flex items-center justify-between px-4">
      <div className="flex items-center gap-4 text-[11px]">
        <span className="text-white/35">
          {nodes.length} node{nodes.length !== 1 ? "s" : ""}
        </span>
        <span className="text-white/35">
          {edges.length} edge{edges.length !== 1 ? "s" : ""}
        </span>
        {feedbackCount > 0 && (
          <span className="text-red-400/60">
            {feedbackCount} feedback{feedbackCount !== 1 ? " loops" : " loop"}
          </span>
        )}
        <span
          className={cn(
            "px-2 py-0.5 rounded-full text-[10px] font-medium",
            isValid
              ? "bg-emerald-500/10 text-emerald-400/80"
              : "bg-amber-500/10 text-amber-400/80",
          )}
        >
          {isValid ? "Valid" : validationMessage}
        </span>
      </div>

      {/* Secondary actions kept for completeness — primary Apply is in the header */}
      {onSaveTemplate && (
        <button
          type="button"
          onClick={onSaveTemplate}
          disabled={!isValid}
          className="text-[11px] text-white/30 hover:text-white/50 disabled:opacity-30 transition-colors px-2 py-1 rounded hover:bg-white/[0.04]"
        >
          Save as Template
        </button>
      )}
    </div>
  );
}
