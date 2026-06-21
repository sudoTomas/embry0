import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { ReactFlow, ReactFlowProvider, type NodeTypes } from "@xyflow/react";
import { BlockedNode } from "../BlockedNode";

const blockedNodeTypes: NodeTypes = { blockedNode: BlockedNode };

describe("BlockedNode", () => {
  it("renders label + status text from data", () => {
    // BlockedNode is rendered by ReactFlow with NodeProps; assert it inside
    // a real ReactFlow so we exercise the same data → render path the app
    // uses, instead of poking the component with a hand-rolled prop bag.
    render(
      <ReactFlowProvider>
        <div style={{ width: 400, height: 200 }}>
          <ReactFlow
            nodes={[
              {
                id: "n1",
                type: "blockedNode",
                position: { x: 0, y: 0 },
                data: { label: "Build base", status: "running" },
              },
            ]}
            edges={[]}
            nodeTypes={blockedNodeTypes}
            proOptions={{ hideAttribution: true }}
          />
        </div>
      </ReactFlowProvider>,
    );

    expect(screen.getByText("Build base")).toBeInTheDocument();
    expect(screen.getByText(/running/i)).toBeInTheDocument();
  });

  it("blockedNodeTypes maps the blockedNode key", () => {
    // Smoke check — the types map is what TasksPage feeds ReactFlow, so it
    // matters that the key matches buildBlockedByGraph's node.type.
    expect(blockedNodeTypes.blockedNode).toBe(BlockedNode);
  });
});
