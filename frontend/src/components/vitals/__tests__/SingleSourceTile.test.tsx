import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { SingleSourceTile } from "../SingleSourceTile";

describe("SingleSourceTile", () => {
  it("renders the value when the query is ready", () => {
    render(
      <SingleSourceTile
        label="Running"
        query={{ isError: false, isPending: false, data: 7 }}
        format={(n) => String(n)}
      />,
    );
    expect(screen.getByText("Running")).toBeInTheDocument();
    expect(screen.getByText("7")).toBeInTheDocument();
  });

  it("renders a loading placeholder while the query is pending", () => {
    render(
      <SingleSourceTile
        label="Queued"
        query={{ isError: false, isPending: true, data: undefined }}
        format={(n) => String(n)}
      />,
    );
    const root = screen.getByTestId("tile-Queued");
    expect(root).toHaveAttribute("data-state", "loading");
  });

  it("renders an error placeholder when the query is in an error state", () => {
    render(
      <SingleSourceTile
        label="Failed"
        query={{ isError: true, isPending: false, data: undefined }}
        format={(n) => String(n)}
      />,
    );
    const root = screen.getByTestId("tile-Failed");
    expect(root).toHaveAttribute("data-state", "error");
  });

  // Guards the assay finding: error must win over loading. React-query can
  // briefly report both isError=true and isPending=true while retrying, and
  // an initial 4xx leaves data undefined — the old order rendered '…' instead
  // of surfacing the failure.
  it("prefers error over loading when both flags are set", () => {
    render(
      <SingleSourceTile
        label="QA Pass Rate"
        query={{ isError: true, isPending: true, data: undefined }}
        format={(n) => `${n}%`}
      />,
    );
    const root = screen.getByTestId("tile-QA Pass Rate");
    expect(root).toHaveAttribute("data-state", "error");
  });

  it("prefers error over a stale successful value (refetch-after-failure)", () => {
    render(
      <SingleSourceTile
        label="Cost Today"
        query={{ isError: true, isPending: false, data: 12.5 }}
        format={(n) => `$${n.toFixed(2)}`}
      />,
    );
    const root = screen.getByTestId("tile-Cost Today");
    expect(root).toHaveAttribute("data-state", "error");
  });
});
