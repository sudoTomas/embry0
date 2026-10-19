/** The Settings page's tabs, in order. The open one rides in the URL
 * (`/settings?tab=context`), so the breadcrumb line can name it and a tab can
 * be linked to. */
export const SETTINGS_TABS = [
  { value: "general", label: "General" },
  { value: "context", label: "Context" },
  { value: "integrations", label: "Integrations" },
  { value: "apikeys", label: "API keys" },
] as const;

export type SettingsTab = (typeof SETTINGS_TABS)[number];

/** The tab a `?tab=` value opens; a missing or unknown one opens General. */
export function settingsTab(value: string | null): SettingsTab {
  return SETTINGS_TABS.find((tab) => tab.value === value) ?? SETTINGS_TABS[0];
}
