import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { ReactFlowProvider, type NodeProps } from "@xyflow/react";
import { StartEndNode } from "../StartEndNode";

// Gold marks only the one thing on a view that needs a person. A pipeline's
// start and end never do, so they take the neutral tokens: no primary
// utility, and no colour written inline past the tokens.
function renderNode(nodeRole: "start" | "end", selected: boolean) {
  const props = { id: nodeRole, data: { nodeRole }, selected } as unknown as NodeProps;
  return render(
    <ReactFlowProvider>
      <StartEndNode {...props} />
    </ReactFlowProvider>,
  );
}

const PRIMARY = /^(?:[\w-]+:)*!?(?:text|bg|border|ring|outline|fill|stroke|shadow)-primary(?:\/[\d.]+)?$/;
const INLINE_COLOUR = /#[0-9a-f]{3,8}\b|rgba?\(|hsla?\(/i;

function goldUses(container: HTMLElement): string[] {
  return [...container.querySelectorAll("*")].flatMap((el) => [
    ...(el.getAttribute("class") ?? "").split(/\s+/).filter((cls) => PRIMARY.test(cls)),
    ...[el.getAttribute("style") ?? ""].filter((style) => INLINE_COLOUR.test(style)),
  ]);
}

describe("start and end nodes", () => {
  for (const role of ["start", "end"] as const) {
    for (const selected of [false, true]) {
      it(`draws a${selected ? " selected" : "n unselected"} ${role} node without gold`, () => {
        const { container } = renderNode(role, selected);
        expect(container.querySelector(".react-flow__handle")).not.toBeNull();
        expect(goldUses(container)).toEqual([]);
      });
    }
  }
});
