import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { IssueBoardView } from "../IssueBoardView";

describe("IssueBoardView", () => {
  it("washes the column a card is dragged over without a gold border", () => {
    // Gold marks only the one thing on a view that needs a person.
    render(
      <MemoryRouter>
        <IssueBoardView issues={[]} />
      </MemoryRouter>,
    );
    const column = screen.getByTestId("board-column-closed");
    fireEvent.dragOver(column, { dataTransfer: { dropEffect: "none" } });
    expect(column.className).toContain("bg-primary-dim");
    expect(column.className).not.toMatch(/(^|\s)border-primary/);
  });
});
