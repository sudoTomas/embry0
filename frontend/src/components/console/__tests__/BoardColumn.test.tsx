import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { BoardColumn } from "../BoardColumn";
import type { BoardColumnConfig } from "@/lib/boardColumns";

const NEEDS_YOU: BoardColumnConfig = { id: "needs_you", label: "needs you", tint: "amber" };
const DONE: BoardColumnConfig = { id: "done", label: "done", tint: "success" };
const RUNNING: BoardColumnConfig = { id: "running", label: "running", tint: "neutral" };

describe("BoardColumn", () => {
  it("renders the lowercase label and count pill", () => {
    render(
      <BoardColumn column={RUNNING} count={3}>
        <div>card</div>
      </BoardColumn>,
    );

    expect(screen.getByText("running")).toBeInTheDocument();
    expect(screen.getByTestId("count-pill")).toHaveTextContent("3");
    expect(screen.getByText("card")).toBeInTheDocument();
  });

  it("dims the count pill at zero", () => {
    render(<BoardColumn column={RUNNING} count={0} />);
    expect(screen.getByTestId("count-pill").className).toContain("opacity-40");
  });

  it("does not dim a non-zero pill", () => {
    render(
      <BoardColumn column={RUNNING} count={2}>
        <div>card</div>
      </BoardColumn>,
    );
    expect(screen.getByTestId("count-pill").className).not.toContain("opacity-40");
  });

  it("tints the Needs You header amber and Done with the success color", () => {
    render(
      <>
        <BoardColumn column={NEEDS_YOU} count={0} />
        <BoardColumn column={DONE} count={0} />
      </>,
    );

    expect(screen.getByText("needs you").className).toContain("text-amber-400");
    expect(screen.getByText("done").className).toContain("text-success");
  });

  it("renders an empty placeholder so an empty Needs You lane stays visible", () => {
    render(<BoardColumn column={NEEDS_YOU} count={0} />);
    expect(screen.getByTestId("empty-lane")).toBeInTheDocument();
  });

  it("renders skeleton cards instead of content on first load", () => {
    const { container } = render(
      <BoardColumn column={RUNNING} count={0} isLoading>
        <div>card</div>
      </BoardColumn>,
    );

    expect(container.querySelectorAll(".animate-pulse").length).toBe(2);
    expect(screen.queryByText("card")).toBeNull();
    expect(screen.queryByTestId("empty-lane")).toBeNull();
  });
});
