import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { LiveActivityBand } from "../LiveActivityBand";

describe("LiveActivityBand", () => {
  it("renders nothing when there is no activity", () => {
    const { container } = render(
      <LiveActivityBand running={0} queued={0} awaitingInput={0} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders only the segments that have a non-zero count", () => {
    render(<LiveActivityBand running={2} queued={0} awaitingInput={3} />);
    expect(screen.getByText("running")).toBeInTheDocument();
    expect(screen.getByText("awaiting input")).toBeInTheDocument();
    expect(screen.queryByText("queued")).toBeNull();
  });

  it("shows the LIVE label when any activity is present", () => {
    render(<LiveActivityBand running={1} queued={0} awaitingInput={0} />);
    expect(screen.getByText("Live")).toBeInTheDocument();
  });
});
