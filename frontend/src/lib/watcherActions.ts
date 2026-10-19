import type { BadgeTone } from "@/components/ui/Badge";
import type { WatcherAction } from "@/lib/types";
import { statusLabel, statusTone } from "@/lib/status";

/** Words for a watcher tick action — shared by the Watcher page and the overview. */
export function watcherActionLabel(action: WatcherAction): string {
  return statusLabel(action);
}

/** Unknown actions (a newer backend) fall back to neutral instead of undefined. */
export function watcherActionTone(action: WatcherAction): BadgeTone {
  return statusTone(action);
}
