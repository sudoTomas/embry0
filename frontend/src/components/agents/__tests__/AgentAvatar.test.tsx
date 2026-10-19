import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { identityOf } from "@/lib/agentIdentity";
import { AgentAvatar } from "../AgentAvatar";

describe("AgentAvatar", () => {
  it("renders the initial, the roster id and the palette id", () => {
    render(<AgentAvatar identity={identityOf({ type: "developer" })} />);
    const tile = screen.getByTitle("Developer");
    expect(tile).toHaveTextContent("D");
    expect(tile).toHaveAttribute("data-avatar", "smith");
    expect(tile).toHaveAttribute("data-color", "amber");
    expect(tile.className).toContain("text-amber-400");
  });
});
