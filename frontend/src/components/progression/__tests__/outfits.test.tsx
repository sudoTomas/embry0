import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { PACK, character, progression } from "./fixtures";

const mockUseProgression = vi.fn();
vi.mock("@/hooks/useProgression", () => ({ useProgression: () => mockUseProgression() }));

import { OutfitProvider } from "../OutfitProvider";
import { outfitOf, useOutfit } from "../outfits";

function Probe({ type }: { type: string }) {
  return <span data-testid="probe">{JSON.stringify(useOutfit(type))}</span>;
}

describe("outfits (progression spec 2026-10-24)", () => {
  it("reads a character's gear, its best 99's cloak colours and the first rare it holds in the pack's wear order", () => {
    const c = character({ capes: ["coding", "reading"], rares: [{ id: "harvest-mask", at: "x" }, { id: "paper-crown", at: "y" }, { id: "top-hat", at: "z" }] });
    expect(outfitOf(c, PACK)).toEqual({
      tier: "divine",
      cloak: { base: "#4a4a52", trim: "#e07b2a" },
      trimmed: true,
      questCloak: null,
      head: "paper-crown",
      divineTrim: false,
    });
    expect(outfitOf({ ...c, quest_cloak: true }, PACK, true)).toMatchObject({ questCloak: PACK.questCloak, divineTrim: true });
    // gear the dashboard cannot draw falls back to the first step
    expect(outfitOf(character({ gear: "tin", capes: [] }), PACK)).toMatchObject({ tier: "bronze", cloak: null });
  });

  it("dresses every sprite under the provider, and none without progression", () => {
    mockUseProgression.mockReturnValue({ data: progression() });
    const { getByTestId, rerender } = render(<OutfitProvider><Probe type="developer" /></OutfitProvider>);
    expect(JSON.parse(getByTestId("probe").textContent!)).toEqual({
      tier: "divine",
      cloak: { base: "#4a4a52", trim: "#e07b2a" },
      trimmed: false,
      questCloak: null,
      head: null,
      divineTrim: false,
    });
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
