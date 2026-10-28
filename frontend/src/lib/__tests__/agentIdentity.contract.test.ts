import { describe, expect, it } from "vitest";
import contractText from "../../../../docs/contracts/identity.json?raw";
import { PRESET_IDS } from "@/components/roster/appearance";
import {
  ADVENTURER_AVATARS, AVATARS, BUILTIN_IDENTITY, DEFAULT_AVATAR, DEFAULT_COLOR, DISPLAY_NAME_MAX, LEGACY_AVATARS, PALETTE, PALETTE_CLASSES, PALETTE_HEX, TAGLINE_MAX,
  isAdventurerId, isAvatarId, isLegacyAvatarId,
} from "../agentIdentity";

interface Contract {
  avatars: string[]; palette: string[]; default_avatar: string; default_color: string;
  display_name_max: number; tagline_max: number;
  builtin: Record<string, { display_name: string; avatar: string; color: string; tagline: string }>;
}
const contract: Contract = JSON.parse(contractText);

describe("identity contract mirror (docs/contracts/identity.json)", () => {
  it("lists the same avatars and palette, in order", () => {
    expect([...AVATARS]).toEqual(contract.avatars);
    expect([...PALETTE]).toEqual(contract.palette);
  });
  it("shares the defaults and limits", () => {
    expect([DEFAULT_AVATAR, DEFAULT_COLOR, DISPLAY_NAME_MAX, TAGLINE_MAX]).toEqual([
      contract.default_avatar, contract.default_color, contract.display_name_max, contract.tagline_max,
    ]);
  });
  it("carries every builtin identity verbatim", () => {
    expect(BUILTIN_IDENTITY).toEqual(contract.builtin);
  });
  it("maps every palette id to a hex and to classes", () => {
    for (const id of contract.palette) {
      expect(PALETTE_HEX[id as keyof typeof PALETTE_HEX], id).toMatch(/^#[0-9a-f]{6}$/);
      expect(PALETTE_CLASSES[id as keyof typeof PALETTE_CLASSES], id).toEqual(
        expect.objectContaining({ text: expect.any(String), bg: expect.any(String), border: expect.any(String) }),
      );
    }
  });
  it("appends the adventurer presets to the twelve original avatars, and composes every one", () => {
    expect(LEGACY_AVATARS).toHaveLength(12);
    expect([...ADVENTURER_AVATARS]).toEqual(PRESET_IDS.slice(0, ADVENTURER_AVATARS.length));
    expect([...PRESET_IDS].sort(), "a preset per avatar (progression spec 2026-10-24)").toEqual([...AVATARS].sort());
    expect([...AVATARS]).toEqual([...LEGACY_AVATARS, ...ADVENTURER_AVATARS]);
    expect(contract.avatars.slice(12)).toEqual([...ADVENTURER_AVATARS]);
    expect(isAdventurerId("obsidian-warrior") && isAvatarId("obsidian-warrior") && !isLegacyAvatarId("obsidian-warrior")).toBe(true);
    expect(isLegacyAvatarId("golem") && !isAdventurerId("golem")).toBe(true);
  });
});
