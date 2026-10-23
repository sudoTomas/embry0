import { useEffect, useId, useRef, type CSSProperties, type ReactNode } from "react";
import { X } from "lucide-react";
import { Overlay } from "@/components/ui/Overlay";
import { Kbd } from "@/components/ui/Kbd";
import { createFocusTrap } from "@/lib/focus-trap";
import { cn } from "@/lib/utils";

export type DialogSide = "center" | "right" | "left";

export interface DialogProps {
  /** A caller that already decides when to render may omit it; `false` renders nothing. */
  open?: boolean;
  onClose: () => void;
  /** The dialog's accessible name. A `label` outranks a `title`; `labelledBy` points at a heading the caller draws. One of the three is required. */
  label?: string;
  labelledBy?: string;
  /** Drawn as the header: an h2 that names the dialog, the Esc hint and the Close button. Omit it to draw your own header (and your own Close). */
  title?: ReactNode;
  /** A card in the middle (default), or a full-height drawer at the right or left edge. */
  side?: DialogSide;
  /** A click on the backdrop closes (default true). A dialog that must be answered passes false. */
  dismissOnBackdrop?: boolean;
  /** Classes for the panel. It is `w-full` under `max-w-lg`, so widen or narrow it with a `max-w-*` class; a `w-*` above `max-w-lg` has no effect. */
  className?: string;
  style?: CSSProperties;
  children: ReactNode;
}

const LAYER: Record<DialogSide, string> = {
  center: "flex items-center justify-center",
  right: "flex justify-end p-0",
  left: "flex justify-start p-0",
};

const PANEL: Record<DialogSide, string> = {
  center: "dialog-in w-full max-w-lg max-h-[85vh] overflow-y-auto rounded-lg border border-border",
  right: "dialog-in-right h-full w-full max-w-lg overflow-y-auto border-l border-border",
  left: "dialog-in-left h-full w-full max-w-lg overflow-y-auto border-r border-border",
};

/**
 * The one modal shell (spec 3.9): an Overlay (the portal, the only box over
 * the whole window), a panel with role="dialog", Escape and the backdrop to
 * close, focus moved in and trapped (lib/focus-trap, which also gives focus
 * back to the opener), and the body's scroll held while it is open. Its
 * entrances are the dialog-in* keyframes in index.css; under reduced motion
 * it is simply there.
 *
 * A control inside that owns Escape (a menu, a field being renamed) calls
 * e.stopPropagation() on its keydown, so the dialog's document listener
 * never sees it.
 */
export function Dialog({
  open = true,
  onClose,
  label,
  labelledBy,
  title,
  side = "center",
  dismissOnBackdrop = true,
  className,
  style,
  children,
}: DialogProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  const titleId = useId();

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  useEffect(() => {
    if (!open || !panelRef.current) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const release = createFocusTrap(panelRef.current);
    return () => {
      release();
      document.body.style.overflow = previous;
    };
  }, [open]);

  if (!open) return null;
  const named = label === undefined && title !== undefined ? titleId : undefined;
  return (
    <Overlay className={cn(LAYER[side], "bg-black/60 backdrop-blur-sm")} onClick={dismissOnBackdrop ? onClose : undefined}>
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={label}
        aria-labelledby={labelledBy ?? named}
        className={cn("bg-surface shadow-2xl", PANEL[side], className)}
        style={style}
        onClick={(e) => e.stopPropagation()}
      >
        {title !== undefined && (
          <div className="flex items-center gap-3 border-b border-border px-5 py-4">
            <h2 id={titleId} className="min-w-0 flex-1 text-base font-semibold">
              {title}
            </h2>
            <Kbd className="hidden sm:inline-flex" aria-hidden="true">
              Esc
            </Kbd>
            <button
              type="button"
              onClick={onClose}
              aria-label="Close"
              className="shrink-0 rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        )}
        {children}
      </div>
    </Overlay>
  );
}
