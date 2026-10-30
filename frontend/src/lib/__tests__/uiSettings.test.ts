import { describe, expect, it } from "vitest";
import { NAV_GROUPS } from "@/components/layout/navGroups";
import { SETTINGS_TABS, settingsTab, settingsTabs } from "@/lib/settingsTabs";
import { homePage, navGroupsFor, normalizeUiSettings, UI_DEFAULTS, type UiSettings } from "@/lib/uiSettings";

/** Each group's entries as `label path`, the way the sidebar draws them. */
function nav(ui: UiSettings): Record<string, string[]> {
  return Object.fromEntries(navGroupsFor(NAV_GROUPS, ui).map((g) => [g.label, g.items.map((i) => `${i.label} ${i.path}`)]));
}

describe("the settings document's ui section, as the dashboard reads it (#177)", () => {
  it("keeps the game on with the guild as its home when nothing is written", () => {
    expect(UI_DEFAULTS).toEqual({ game: true, home: "guild" });
    expect(homePage(UI_DEFAULTS)).toBe("guild");
    expect(navGroupsFor(NAV_GROUPS, UI_DEFAULTS), "the defaults draw the sidebar exactly as before").toEqual(NAV_GROUPS);
  });

  it("opens on the Overview while the game is off, whatever home says", () => {
    expect(homePage({ game: false, home: "guild" })).toBe("overview");
    expect(homePage({ game: false, home: "overview" })).toBe("overview");
    expect(homePage({ game: true, home: "overview" })).toBe("overview");
  });

  it("leaves the game's entries out of the sidebar while it is off, with the Overview at /", () => {
    const groups = nav({ game: false, home: "guild" });
    expect(groups.Overview).toEqual(["Overview /"]);
    const every = Object.values(groups).flat().join(", ");
    expect(every).not.toMatch(/Guild|Achievements/);
    expect(Object.keys(groups), "every other section stays").toEqual(NAV_GROUPS.map((g) => g.label));
  });

  it("moves the guild to its own path while the Overview is the home", () => {
    expect(nav({ game: true, home: "overview" }).Overview).toEqual([
      "Guild /guild",
      "Overview /",
      "Achievements /achievements",
    ]);
  });

  it("reads anything but game: false as the game on, and anything but overview as the guild", () => {
    expect(normalizeUiSettings({ game: false, home: "overview" })).toEqual({ game: false, home: "overview" });
    expect(normalizeUiSettings({})).toEqual(UI_DEFAULTS);
    expect(normalizeUiSettings(null)).toEqual(UI_DEFAULTS);
    expect(normalizeUiSettings({ game: "false", home: "map" })).toEqual(UI_DEFAULTS);
  });

  it("drops the Game content tab while the game is off, and opens General for a link to it", () => {
    expect(settingsTabs(true)).toEqual([...SETTINGS_TABS]);
    expect(settingsTabs(false).map((t) => t.value)).not.toContain("game");
    expect(settingsTab("game", true).value).toBe("game");
    expect(settingsTab("game", false).value).toBe("general");
    expect(settingsTab("policy", false).value).toBe("policy");
  });
});
