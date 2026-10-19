import { describe, expect, it } from "vitest";
import css from "../../index.css?raw";
import { AGENT_COLORS } from "../agentVisuals";
import { getAgentColor } from "../graph-utils";

/** The palette's named colours (index.css @theme): --color-<name>: #hex. */
const PALETTE: Record<string, string> = Object.fromEntries(
  [...css.matchAll(/--color-([a-z]+):\s*(#[0-9a-f]{6});/g)].map((m) => [m[1], m[2]]),
);

/** The node a card names, to the agent type the pipeline graph colours. */
const GRAPH_TYPE: Record<string, string> = { triage: "triage", developer: "developer", review: "reviewer" };

describe("AGENT_COLORS", () => {
  it("draws every role in a colour of the palette", () => {
    for (const [agent, colors] of Object.entries(AGENT_COLORS)) {
      for (const cls of [colors.text, colors.bg, colors.border]) {
        const name = cls.match(/^(?:text|bg|border)-([a-z]+)/)?.[1] ?? "";
        expect(name in PALETTE, `${agent}: ${cls} is not a palette colour`).toBe(true);
      }
    }
  });

  it("gives each role the colour the pipeline graph draws it in", () => {
    expect(Object.keys(GRAPH_TYPE).sort()).toEqual(Object.keys(AGENT_COLORS).sort());
    for (const [agent, type] of Object.entries(GRAPH_TYPE)) {
      const name = AGENT_COLORS[agent].text.replace(/^text-/, "");
      expect(PALETTE[name], `${agent} reads ${name}, the graph draws ${type} in ${getAgentColor(type)}`).toBe(
        getAgentColor(type),
      );
    }
  });
});
