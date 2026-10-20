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
        style={{ stroke: "#f87171", strokeWidth: 1.5, strokeDasharray: "6 4" }}
      />
      {loopConfig?.max_loops && (
        <EdgeLabelRenderer>
          {/* A button, so the label is an opener that takes focus and gets
              it back when the edge's detail closes. Its click reaches the
              edge's own click handler through the portal, which opens the
              detail, so it needs no handler of its own. */}
          <button
            type="button"
            aria-label={`Feedback loop from ${props.source} to ${props.target}, max ${loopConfig.max_loops} loops`}
            className="nodrag nopan pointer-events-auto"
            style={{
              position: "absolute",
              transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
              padding: "3px 8px",
              borderRadius: "10px",
              background: "rgba(248,113,113,0.12)",
              border: "1px solid rgba(248,113,113,0.25)",
              color: "#f87171",
              fontSize: "10px",
              fontWeight: 600,
              whiteSpace: "nowrap",
              display: "flex",
              alignItems: "center",
              gap: "4px",
            }}
          >
            <span aria-hidden="true">↩</span> max {loopConfig.max_loops} loops
          </button>
        </EdgeLabelRenderer>
      )}
    </>
  );
}
