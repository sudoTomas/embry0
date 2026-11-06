import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { WorldCharacter } from "@/api/world";
import { identityOf } from "@/lib/agentIdentity";

const write = vi.fn();
vi.mock("@/hooks/useWorld", () => ({ useWriteQuest: () => ({ mutate: write, isPending: false }) }));
vi.mock("@/hooks/useAgentIdentity", () => ({ useIdentityLookup: () => (type: string) => identityOf(type) }));
vi.mock("@/components/runwith/RunWithControl", () => ({ RunWithControl: () => null }));
vi.mock("sonner", () => ({ toast: { success: vi.fn() } }));

import { WriteQuestDialog } from "../WriteQuestDialog";

const character = (agent_type: string, can_quest: boolean, quest_template: string | null) =>
  ({ agent_type, level: 2, can_quest, quest_template }) as unknown as WorldCharacter;

/** The guild sends whatever agent its row gives a task (#167), an agent of the deployment's own included. */
describe("a new task from the guild", () => {
  it("offers the agents whose row gives them a task and runs the template the row names", async () => {
    render(
      <WriteQuestDialog
        characters={[character("developer", true, null), character("scout", true, "scout-pass"), character("qa", false, null)]}
        repos={["acme/widgets"]}
        initialAgent="scout"
        onClose={() => {}}
        onSent={() => {}}
      />,
    );
    const agent = screen.getByLabelText("Agent");
    expect([...agent.querySelectorAll("option")].map((o) => (o as HTMLOptionElement).value)).toEqual(["developer", "scout"]);
    expect(agent).toHaveValue("scout");
    await userEvent.type(screen.getByLabelText("Task"), "Find the dead flags");
    await userEvent.click(screen.getByRole("button", { name: "Start" }));
    expect(write).toHaveBeenCalledWith(
      { repo: "acme/widgets", task: "Find the dead flags", template: "scout-pass", runWith: {} },
      expect.anything(),
    );
  });
});
