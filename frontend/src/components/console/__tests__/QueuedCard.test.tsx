import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
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
});
