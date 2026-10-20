import { BaseEdge, EdgeLabelRenderer, getBezierPath, type EdgeProps } from "@xyflow/react";

export function FeedbackEdge(props: EdgeProps) {
  const [edgePath, labelX, labelY] = getBezierPath(props);
  const loopConfig = (props.data as Record<string, unknown>)?.loopConfig as
    | { max_loops?: number }
    | undefined;

  return (
    <>
      <BaseEdge
        path={edgePath}
        style={{ stroke: "var(--color-destructive)", strokeWidth: 1.5, strokeDasharray: "6 4" }}
      />
      {loopConfig?.max_loops && (
        <EdgeLabelRenderer>
          {/* A button, so the label is an opener that takes focus and gets
              it back when the edge's detail closes. Its click reaches the
              edge's own click handler through the portal, which opens the
              detail, so it needs no handler of its own. It sits on its own
              line, so its surface is opaque: the line stops at the label
              instead of striking through it. */}
          <button
            type="button"
            aria-label={`Feedback loop from ${props.source} to ${props.target}, max ${loopConfig.max_loops} loops`}
            className="nodrag nopan pointer-events-auto absolute flex items-center gap-1 whitespace-nowrap rounded-[10px] border border-destructive/25 bg-surface px-2 py-[3px] text-[10px] font-semibold text-destructive"
            style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)` }}
          >
            <span aria-hidden="true">↩</span> max {loopConfig.max_loops} loops
          </button>
        </EdgeLabelRenderer>
      )}
    </>
  );
}
