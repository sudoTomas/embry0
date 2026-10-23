/** The Watcher page's tabs; the open one rides in the URL (`/watcher?tab=stacks`). */
export const WATCHER_TABS = [
  { value: "ticks", label: "Ticks" },
  { value: "stacks", label: "Stacks" },
] as const;

export type WatcherTab = (typeof WATCHER_TABS)[number];

export function watcherTab(value: string | null): WatcherTab {
  return WATCHER_TABS.find((tab) => tab.value === value) ?? WATCHER_TABS[0];
}
