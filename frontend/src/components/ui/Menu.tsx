import { useEffect, useId, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { MoreVertical } from "lucide-react";
import { cn } from "@/lib/utils";

export interface MenuItem {
  label: string;
  onSelect: () => void;
  icon?: ReactNode;
  destructive?: boolean;
  disabled?: boolean;
}

export interface MenuProps {
  /** Names the button and the list for assistive tech, e.g. "Actions for quick-fix". */
  label: string;
  items: MenuItem[];
  /** The edge of the button the list hangs from. */
  align?: "start" | "end";
  /** The button's content; a "more" glyph by default. */
  children?: ReactNode;
  className?: string;
  triggerClassName?: string;
}

/**
 * A button-anchored popover list (spec 3.9): role="menu", arrow keys with
 * wrap, Home and End, Escape back to the button, Tab or a press outside to
 * close. The list is positioned under its button, inside the row that holds
 * it, so a scrolling list clips it like any other row content. Every click
 * inside stops at the menu: a row that is itself a button never fires.
 */
export function Menu({ label, items, align = "end", children, className, triggerClassName }: MenuProps) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const listId = useId();

  const itemsOf = () =>
    Array.from(listRef.current?.querySelectorAll<HTMLElement>('[role="menuitem"]:not([disabled])') ?? []);

  useEffect(() => {
    if (!open) return;
    const onPress = (e: PointerEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", onPress);
    return () => document.removeEventListener("pointerdown", onPress);
  }, [open]);

  useEffect(() => {
    if (open) itemsOf()[0]?.focus();
  }, [open]);

  function close(refocus: boolean) {
    setOpen(false);
    if (refocus) triggerRef.current?.focus();
  }

  function onListKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    const all = itemsOf();
    const i = all.indexOf(document.activeElement as HTMLElement);
    if (e.key === "ArrowDown") {
      e.preventDefault();
      all[(i + 1) % all.length]?.focus();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      all[(i - 1 + all.length) % all.length]?.focus();
    } else if (e.key === "Home") {
      e.preventDefault();
      all[0]?.focus();
    } else if (e.key === "End") {
      e.preventDefault();
      all[all.length - 1]?.focus();
    } else if (e.key === "Escape") {
      e.stopPropagation(); // a Dialog around the menu stays open
      close(true);
    } else if (e.key === "Tab") {
      close(false);
    }
  }

  return (
    <div ref={rootRef} className={cn("relative inline-block", className)} onClick={(e) => e.stopPropagation()}>
      <button
        ref={triggerRef}
        type="button"
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        onClick={() => setOpen((o) => !o)}
        className={cn("rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground", triggerClassName)}
      >
        {children ?? <MoreVertical className="h-3.5 w-3.5" />}
      </button>
      {open && (
        <div
          ref={listRef}
          id={listId}
          role="menu"
          aria-label={label}
          onKeyDown={onListKeyDown}
          className={cn(
            "absolute z-40 mt-1 min-w-32 rounded-md border border-border bg-overlay py-1 shadow-xl",
            align === "end" ? "right-0" : "left-0",
          )}
        >
          {items.map((item) => (
            <button
              key={item.label}
              type="button"
              role="menuitem"
              disabled={item.disabled}
              onClick={() => {
                close(true);
                item.onSelect();
              }}
              className={cn(
                "flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs transition-colors disabled:opacity-50",
                item.destructive ? "text-destructive hover:bg-destructive/10" : "text-foreground/80 hover:bg-muted hover:text-foreground",
              )}
            >
              {item.icon}
              {item.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
