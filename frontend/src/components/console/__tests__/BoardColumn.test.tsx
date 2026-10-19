import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { BoardColumn } from "../BoardColumn";
import { GlideBoard } from "../GlideBoard";
import { createGlide } from "@/hooks/useGlide";
import { tick } from "@/lib/motion";
import type { BoardColumnConfig } from "@/lib/boardColumns";

vi.mock("@/lib/motion", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/motion")>()),
  tick: vi.fn(),
}));

const NEEDS_YOU: BoardColumnConfig = { id: "needs_you", label: "Needs you", tint: "amber" };
const DONE: BoardColumnConfig = { id: "done", label: "Done", tint: "success" };
const RUNNING: BoardColumnConfig = { id: "running", label: "Running", tint: "neutral" };

describe("BoardColumn", () => {
  it("renders the sentence-case label and count pill", () => {
    render(
      <BoardColumn column={RUNNING} count={3}>
        <div>card</div>
      </BoardColumn>,
    );

    expect(screen.getByText("Running")).toBeInTheDocument();
    expect(screen.getByText("Running").className).not.toContain("lowercase");
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

    expect(screen.getByText("Needs you").className).toContain("text-amber-400");
    expect(screen.getByText("Done").className).toContain("text-success");
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

  it("keeps its header's and empty placeholder's places, so they travel with the cards", () => {
    const glide = createGlide();
    const attach = vi.spyOn(glide, "attach");
    render(
      <GlideBoard glide={glide} layout={0} paintKey="">
        <BoardColumn column={RUNNING} count={0} />
      </GlideBoard>,
    );

    expect(attach).toHaveBeenCalledWith("lane:running", screen.getByText("Running").parentElement);
    expect(attach).toHaveBeenCalledWith("lane:running:empty", screen.getByTestId("empty-lane"));
  });

  describe("count tick", () => {
    beforeEach(() => vi.mocked(tick).mockClear());

    it("ticks the count pill when a poll changes the count", () => {
      const { rerender } = render(<BoardColumn column={RUNNING} count={1} />);
      rerender(<BoardColumn column={RUNNING} count={2} />);

      expect(tick).toHaveBeenCalledTimes(1);
      expect(tick).toHaveBeenCalledWith(screen.getByTestId("count-pill"));
    });

    it("does not tick on the first paint", () => {
      const { rerender } = render(<BoardColumn column={RUNNING} count={0} isLoading />);
      rerender(<BoardColumn column={RUNNING} count={3} />);
      expect(tick).not.toHaveBeenCalled();
    });

    it("does not tick when the filters change the count", () => {
      const { rerender } = render(<BoardColumn column={RUNNING} count={3} paintKey="|" />);
      rerender(<BoardColumn column={RUNNING} count={1} paintKey="running|" />);
      expect(tick).not.toHaveBeenCalled();
    });

    it("does not tick when a poll leaves the count as it was", () => {
      const { rerender } = render(<BoardColumn column={RUNNING} count={2} />);
      rerender(<BoardColumn column={RUNNING} count={2} />);
      expect(tick).not.toHaveBeenCalled();
    });
  });
});
