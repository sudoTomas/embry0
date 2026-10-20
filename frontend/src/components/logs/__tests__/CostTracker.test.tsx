import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { CostTracker } from "../CostTracker";

describe("CostTracker", () => {
  // At 390 px the four figures and the status did not fit one row: the
  // figures squeezed and the Complete pill ran 19 px out of the card. jsdom
  // has no layout, so this pins the classes: the figures wrap among
  // themselves, and the status keeps its own width beside them.
  it("wraps the figures and keeps the status inside the card on a phone", () => {
    render(<CostTracker costUsd={0.9} tokensIn={333_759} tokensOut={38_648} turns={50} isComplete />);
    const status = screen.getByText("Complete").parentElement!;
    expect(status).toHaveClass("shrink-0");
    expect(status).not.toHaveClass("ml-auto");
    const figures = screen.getByText("Turns").parentElement!.parentElement!;
    expect(figures).toHaveClass("flex", "flex-wrap");
    expect(figures).toContainElement(screen.getByText("Cost"));
    expect(figures).not.toContainElement(status);
  });
});
