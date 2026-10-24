import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { Diary } from "@/api/progression";
import { progression } from "./fixtures";
import { RepoDiaries } from "../RepoDiaries";

describe("RepoDiaries (progression spec 2026-10-24)", () => {
  it("shows each repo's tiers with tasks done, lights a complete tier, and opens onto the tasks", async () => {
    render(<RepoDiaries diaries={progression().diaries} />);
    const tiers = screen.getAllByTestId("diary-tier");
    expect(tiers.map((t) => t.textContent)).toEqual(["Easy 1/1", "Medium 0/1", "Hard 0/1", "Elite 0/1"]);
    expect(tiers[0]).toHaveAttribute("data-complete", "true");
    expect(screen.queryByTestId("diary-tasks")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: /o\/app/ }));
    const tasks = screen.getByTestId("diary-tasks");
    expect(within(tasks).getByText("Finish a job")).toHaveClass("line-through");
    expect(within(tasks).getByText("Merge a pull request")).not.toHaveClass("line-through");
  });

  it("shows eight diaries until asked for all", async () => {
    const one = progression().diaries[0];
    const many: Diary[] = Array.from({ length: 10 }, (_, i) => ({ ...one, repo: `o/r${i}` }));
    render(<RepoDiaries diaries={many} />);
    expect(screen.getAllByTestId("diary")).toHaveLength(8);
    await userEvent.click(screen.getByRole("button", { name: "Show all 10" }));
    expect(screen.getAllByTestId("diary")).toHaveLength(10);
  });

  it("says when no repository has started its diary", () => {
    render(<RepoDiaries diaries={[]} />);
    expect(screen.getByText("No repository has done a diary task yet.")).toBeInTheDocument();
  });
});
