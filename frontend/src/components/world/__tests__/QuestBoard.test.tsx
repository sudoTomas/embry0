import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { Lead, LeadKind } from "@/api/world";

vi.mock("@/components/world/art/manifest", async (original) => {
  const actual = await original<typeof import("../art/manifest")>();
  return { ...actual, useArt: () => actual.EMPTY_ART };
});

import { QuestBoard } from "../QuestBoard";

/** Two entries of GET /world's lead_kinds (the backend's catalogue, embry0/services/lead_kinds.py). */
const CATALOGUE: LeadKind[] = [
  {
    id: "crack",
    label: "Crack",
    plural: "Cracks",
    about: "a crack the scout found in the code",
    found_by: "the scout",
  },
  {
    id: "ci",
    label: "Failing check",
    plural: "Failing checks",
    about: "a failing CI check",
    found_by: "the failing-CI finder",
  },
];
// Built here, not with lib/world's helper, so this file also runs against a board that predates the catalogue.
const byId = (kinds: LeadKind[]) => new Map(kinds.map((k) => [k.id, k]));

const lead = (over: Partial<Lead>): Lead => ({
  id: "crack:1",
  kind: "crack",
  repo: "acme/widgets",
  area: null,
  owners: [],
  title: "A status filter matches one spelling",
  summary: "",
  details: "",
  action: "",
  url: null,
  created_at: "2026-10-29T00:00:00Z",
  level: 30,
  verb: "Mend",
  creature: "crack_pot",
  leader: "developer",
  ...over,
});

const QUESTS = [lead({}), lead({ id: "ci:1", kind: "ci", title: "The build fails", verb: "Attack", creature: "ci_golem", level: 50 })];

function quest(title: string): HTMLElement {
  const row = screen.getAllByTestId("quest").find((q) => q.textContent?.includes(title));
  if (!row) throw new Error(`no quest titled ${title}`);
  return row;
}

describe("QuestBoard kind names (#166)", () => {
  it("names each kind as the backend's catalogue does: one on its badge, several in the filter", () => {
    render(<QuestBoard quests={QUESTS} kinds={byId(CATALOGUE)} partyLevel={40} onPick={vi.fn()} />);
    const crack = within(quest("A status filter")).getByText("Crack");
    expect(crack).toHaveAttribute("title", "A crack the scout found in the code. Found by the scout.");
    expect(within(quest("The build fails")).getByText("Failing check")).toBeInTheDocument();
    const filter = screen.getByRole("combobox", { name: "Kinds" });
    expect(within(filter).getAllByRole("option").map((o) => o.textContent)).toEqual(["All kinds", "Cracks", "Failing checks"]);
  });

  it("falls back to each kind's own id against an older backend that serves no catalogue", () => {
    render(<QuestBoard quests={QUESTS} kinds={byId([])} partyLevel={40} onPick={vi.fn()} />);
    expect(within(quest("The build fails")).getByText("ci")).not.toHaveAttribute("title");
    const filter = screen.getByRole("combobox", { name: "Kinds" });
    expect(within(filter).getAllByRole("option").map((o) => o.textContent)).toEqual(["All kinds", "crack", "ci"]);
  });
});
