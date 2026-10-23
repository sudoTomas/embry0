import type { ComponentProps } from "react";
import { cn } from "@/lib/utils";

/** A key on the keyboard, as a hint next to the control it drives. */
export function Kbd({ className, ...props }: ComponentProps<"kbd">) {
  return (
    <kbd
      className={cn(
        "inline-flex items-center rounded border border-border bg-muted px-1.5 py-0.5 font-mono text-2xs text-muted-foreground",
        className,
      )}
      {...props}
    />
  );
}
