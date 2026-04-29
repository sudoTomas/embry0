const FOCUSABLE = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
].join(", ");

function getFocusable(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE));
}

/**
 * Trap keyboard focus inside `container`. Returns a cleanup function that
 * removes the listener. Call inside a useEffect that runs when the modal opens.
 *
 * Also saves document.activeElement and restores it on cleanup.
 */
export function createFocusTrap(container: HTMLElement): () => void {
  const saved = document.activeElement as HTMLElement | null;

  // Move focus into the container on the next tick (avoids flicker).
  const firstFocusable = getFocusable(container)[0];
  if (firstFocusable) {
    setTimeout(() => firstFocusable.focus(), 0);
  } else if (import.meta.env.DEV) {
    console.warn("createFocusTrap: no focusable elements found in container");
  }

  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key !== "Tab") return;
    const focusable = getFocusable(container);
    if (focusable.length === 0) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (e.shiftKey) {
      if (document.activeElement === first) {
        e.preventDefault();
        last.focus();
      }
    } else {
      if (document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
  };

  container.addEventListener("keydown", onKeyDown);

  return () => {
    container.removeEventListener("keydown", onKeyDown);
    if (saved && typeof saved.focus === "function") {
      saved.focus();
    }
  };
}
