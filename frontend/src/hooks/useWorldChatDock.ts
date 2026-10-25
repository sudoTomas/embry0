import { useCallback, useState } from "react";

/** The guild's activity box: open, folded to a tab, or closed (reopened from the page header). */
export type ChatDock = "open" | "collapsed" | "closed";

export const CHAT_DOCK_KEY = "embry0.world.chat";

/** A phone's screen is too small to give the box room before anyone asked for it. */
function fallback(): ChatDock {
  return typeof window.matchMedia === "function" && window.matchMedia("(max-width: 639px)").matches ? "collapsed" : "open";
}

function read(): ChatDock {
  try {
    const stored = window.localStorage.getItem(CHAT_DOCK_KEY);
    return stored === "open" || stored === "collapsed" || stored === "closed" ? stored : fallback();
  } catch {
    return fallback();
  }
}

/** Where the activity box is, remembered per browser; until a choice, open, or folded on a phone. */
export function useWorldChatDock(): [ChatDock, (next: ChatDock) => void] {
  const [dock, setDock] = useState<ChatDock>(read);
  const set = useCallback((next: ChatDock) => {
    setDock(next);
    try {
      window.localStorage.setItem(CHAT_DOCK_KEY, next);
    } catch {
      // Storage refused (a private window): the choice holds for this visit only.
    }
  }, []);
  return [dock, set];
}
