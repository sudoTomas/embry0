import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { Fact, FactStrip } from "../FactStrip";

describe("FactStrip", () => {
  it("lays facts out as label and value on one raised line", () => {
    render(
      <FactStrip data-testid="strip">
        <Fact label="Mode" data-testid="fact">
          Advise
        </Fact>
      </FactStrip>,
    );
    expect(screen.getByTestId("strip").className).toContain("bg-surface");
    expect(screen.getByTestId("fact")).toHaveTextContent("ModeAdvise");
    expect(screen.getByText("Mode").className).toContain("text-fg-muted");
  });
});
