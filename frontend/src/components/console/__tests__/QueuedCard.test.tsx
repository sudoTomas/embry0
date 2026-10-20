import { describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import type { PipelineTemplate } from "@/lib/types";

vi.mock("@/hooks/useAgents", () => ({
  useAgents: () => ({
    data: [
      { type: "qa", display_name: "QA", avatar: "alchemist", color: "emerald", tagline: "" },
      { type: "triage", display_name: "Triage", avatar: "seer", color: "violet", tagline: "" },
    ],
    isLoading: false,
  }),
}));

const QA_TEMPLATE: PipelineTemplate = {
  template_id: "t1",
  name: "qa",
  description: "",
  created_at: "",
  graph: {
    graph_id: "g",
    name: "qa",
    nodes: [
      { node_id: "s", agent_type: "__start__", position: { x: 0, y: 0 } },
      { node_id: "q", agent_type: "qa", position: { x: 100, y: 0 } },
    ],
    edges: [{ edge_id: "e1", source: "s", target: "q", edge_type: "flow" }],
    metadata: { max_total_budget_usd: 0, max_total_loops: 0, created_by: "manual" },
  },
};

vi.mock("@/hooks/usePipelines", () => ({
  useTemplates: () => ({ data: [{ template_id: "t1", name: "qa", description: "", created_at: "" }] }),
  useTemplate: (id: string | null) => ({ data: id === "t1" ? QA_TEMPLATE : undefined }),
}));

import { QueuedCard } from "../QueuedCard";
import { makeJob } from "./fixtures";

function renderCard(props: Parameters<typeof QueuedCard>[0]) {
  return render(
    <MemoryRouter>
      <QueuedCard {...props} />
    </MemoryRouter>,
  );
}

describe("QueuedCard", () => {
  it("renders queue position with depth context", () => {
    renderCard({ job: makeJob({ status: "pending" }), position: 2, queueDepth: 5 });
    expect(screen.getByTestId("queue-position")).toHaveTextContent("position 2 of 5");
  });

  it("renders a bare position without depth", () => {
    renderCard({ job: makeJob({ status: "pending" }), position: 1 });
    expect(screen.getByTestId("queue-position")).toHaveTextContent("position 1");
  });

  it("falls back to a generic waiting line when position is unknown", () => {
    renderCard({ job: makeJob({ status: "pending" }) });
    expect(screen.getByTestId("queue-position")).toHaveTextContent("waiting in queue");
  });

  it("shows the template's first party member waiting", () => {
    renderCard({ job: makeJob({ status: "pending", pipeline_template: "qa" }) });
    const sprite = screen.getByTestId("party-sprite").querySelector("svg")!;
    expect(sprite).toHaveAttribute("data-avatar", "alchemist");
    expect(sprite).toHaveAttribute("data-state", "pending");
  });
  it("shows triage waiting when the template is unknown or unset", () => {
    renderCard({ job: makeJob({ status: "pending", pipeline_template: null }) });
    expect(screen.getByTestId("party-sprite").querySelector("svg")).toHaveAttribute("data-avatar", "seer");
    cleanup();
    renderCard({ job: makeJob({ status: "pending", pipeline_template: "no-such-template" }) });
    expect(screen.getByTestId("party-sprite").querySelector("svg")).toHaveAttribute("data-avatar", "seer");
  });
});
