import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PACK, progression } from "./fixtures";
import { QuestJournal } from "../QuestJournal";

describe("QuestJournal (progression spec 2026-10-24)", () => {
  it("lists each quest in its state's colour with its points and where it happened", () => {
    const { quests } = progression();
    render(<QuestJournal quests={quests} points={1} pack={PACK} />);
    expect(screen.getByRole("heading", { name: /Deeds/ })).toHaveTextContent("1 of 6 deed points");
    const [done, open] = screen.getAllByTestId("quest");
    expect(done).toHaveAttribute("data-state", "complete");
    expect(within(done).getByRole("heading", { level: 3 })).toHaveClass("text-[#5fc95a]");
    expect(done).toHaveTextContent("Complete · 1 point");
    expect(done).toHaveTextContent("on iss-1");
    expect(open).toHaveAttribute("data-state", "not_started");
    expect(within(open).getByRole("heading", { level: 3 })).toHaveClass("text-[#e05a4f]");
  });

  it("strikes through a step once done, and a requirement once met", () => {
    const { quests } = progression();
    render(<QuestJournal quests={quests} points={1} pack={PACK} />);
    const [done, open] = screen.getAllByTestId("quest");
    for (const step of within(done).getAllByRole("listitem")) expect(step).toHaveClass("line-through");
    for (const step of within(open).getAllByRole("listitem")) expect(step).not.toHaveClass("line-through");
    expect(within(open).getByTestId("quest-requirement")).toHaveTextContent("Requires: A character at total level 1,000");
    expect(within(open).getByTestId("quest-requirement")).not.toHaveClass("line-through");
  });

  it("names the journal and its points in the pack's words", () => {
    const { quests } = progression();
    const labels = { ...PACK.labels!, quest_journal: "Feats", journal_points: "feat marks", quest_point: "mark", quest_points: "marks" };
    render(<QuestJournal quests={quests} points={1} pack={{ ...PACK, labels }} />);
    expect(screen.getByRole("heading", { name: /Feats/ })).toHaveTextContent("1 of 6 feat marks");
    expect(screen.getAllByTestId("quest")[0]).toHaveTextContent("Complete · 1 mark");
  });
});
