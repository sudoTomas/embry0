/** The Settings page's tabs, in order. The open one rides in the URL
 * (`/settings?tab=context`), so the breadcrumb line can name it and a tab can
 * be linked to. A tab of the game layer shows only while the game is on
 * (the settings document's `ui.game`, #177). */
export const SETTINGS_TABS = [
  { value: "general", label: "General" },
  { value: "policy", label: "Policy" },
  { value: "context", label: "Context" },
  { value: "integrations", label: "Integrations" },
  { value: "apikeys", label: "API keys" },
  { value: "game", label: "Game content", game: true },
] as const;

export type SettingsTab = (typeof SETTINGS_TABS)[number];

/** The tabs shown: every one while the game is on, all but the game's while it is off. */
export function settingsTabs(game = true): SettingsTab[] {
  return SETTINGS_TABS.filter((tab) => game || !("game" in tab));
}

/** The tab a `?tab=` value opens; a missing or unknown one, or the game's while the game is off, opens General. */
export function settingsTab(value: string | null, game = true): SettingsTab {
  return settingsTabs(game).find((tab) => tab.value === value) ?? SETTINGS_TABS[0];
}
