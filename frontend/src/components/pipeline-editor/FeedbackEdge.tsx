import { BaseEdge, getBezierPath, type EdgeProps } from "@xyflow/react";

export function FeedbackEdge(props: EdgeProps) {
  const [edgePath] = getBezierPath(props);
  const loopConfig = (props.data as Record<string, unknown>)?.loopConfig as
    | { max_loops?: number }
    | undefined;

  return (
    <>
      <BaseEdge
        path={edgePath}
        style={{ stroke: "#f87171", strokeWidth: 2, strokeDasharray: "6 4" }}
      />
      {loopConfig?.max_loops && (
        <text>
          <textPath
            href={`#${props.id}`}
            startOffset="50%"
            textAnchor="middle"
            className="text-[10px] fill-red-400"
          >
            max {loopConfig.max_loops}
          </textPath>
        </text>
      )}
    </>
  );
}
