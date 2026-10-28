import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { character, progression } from "./fixtures";

const mockUseProgression = vi.fn();
vi.mock("@/hooks/useProgression", () => ({ useProgression: () => mockUseProgression() }));

import { OutfitProvider } from "../OutfitProvider";
import { outfitOf, useOutfit } from "../outfits";

function Probe({ type }: { type: string }) {
  return <span data-testid="probe">{JSON.stringify(useOutfit(type))}</span>;
}

describe("outfits (progression spec 2026-10-24)", () => {
  it("reads a character's tier, its 99s as mastery cloaks and its known rares", () => {
    const c = character({ capes: ["coding", "reading", "no-such-skill"], rares: [{ id: "paper-crown", at: "x" }, { id: "top-hat", at: "y" }] });
    expect(outfitOf(c)).toEqual({ tier: "divine", capes: ["coding", "reading"], rares: ["paper-crown"], questCloak: false, divineTrim: false });
    expect(outfitOf({ ...c, quest_cloak: true }, true)).toMatchObject({ questCloak: true, divineTrim: true });
  });

  it("dresses every sprite under the provider, and none without progression", () => {
    mockUseProgression.mockReturnValue({ data: progression() });
    const { getByTestId, rerender } = render(<OutfitProvider><Probe type="developer" /></OutfitProvider>);
    expect(JSON.parse(getByTestId("probe").textContent!)).toEqual({ tier: "divine", capes: ["coding"], rares: [], questCloak: false, divineTrim: false });
    rerender(<OutfitProvider><Probe type="nobody" /></OutfitProvider>);
    expect(getByTestId("probe")).toHaveTextContent("null");
    mockUseProgression.mockReturnValue({ data: undefined });
    rerender(<OutfitProvider><Probe type="developer" /></OutfitProvider>);
    expect(getByTestId("probe")).toHaveTextContent("null");
  });

  it("dresses the reviewer node as the review agent", () => {
    mockUseProgression.mockReturnValue({ data: progression() });
    const { getByTestId } = render(<OutfitProvider><Probe type="reviewer" /></OutfitProvider>);
    expect(JSON.parse(getByTestId("probe").textContent!).tier).toBe("bronze");
  });
});
