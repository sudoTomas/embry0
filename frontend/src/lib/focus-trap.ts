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
 * Also saves document.activeElement, the control that opened the modal, and
 * restores it on cleanup. Focus moves in to the element marked
 * `data-autofocus`, or else the first focusable one. Mark a field that way
 * rather than with React's autoFocus: React focuses an autoFocus field while
 * it commits the modal, before this effect runs, so the trap would save the
 * field instead of the opener and on close restore focus to a removed node.
 */
export function createFocusTrap(container: HTMLElement): () => void {
  const saved = document.activeElement as HTMLElement | null;

  // Move focus into the container on the next tick (avoids flicker).
  const initial = container.querySelector<HTMLElement>("[data-autofocus]") ?? getFocusable(container)[0];
  if (initial) {
    setTimeout(() => initial.focus(), 0);
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
