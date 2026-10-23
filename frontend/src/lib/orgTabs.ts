/** An organization page's tabs; the open one rides in the URL (`/orgs/acme?tab=settings`). */
export const ORG_TABS = [
  { value: "overview", label: "Overview" },
  { value: "context-audit", label: "Context audit" },
  { value: "settings", label: "Settings" },
] as const;

export type OrgTab = (typeof ORG_TABS)[number];

export function orgTab(value: string | null): OrgTab {
  return ORG_TABS.find((tab) => tab.value === value) ?? ORG_TABS[0];
}
