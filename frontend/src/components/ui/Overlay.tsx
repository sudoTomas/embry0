import type { ComponentProps } from "react";
import { createPortal } from "react-dom";
import { cn } from "@/lib/utils";

/**
 * The layer a dialog, drawer or lightbox sits on: a box over the whole
 * window, rendered as a child of document.body.
 *
 * Rendered inline, a `fixed inset-0` box answers to its ancestors as well as
 * to the window. Tailwind's space-y gives every child but the last a bottom
 * margin, which shortens it; an ancestor with a transform becomes its
 * containing block; an ancestor's stacking context caps its z-index. Under
 * body none of them can reach it. React still treats it as part of the
 * component that renders it, so its events bubble there as before.
 *
 * The layer keeps a 16 px gutter round what it holds, so a dialog as wide
 * as a phone still stops short of the screen's edges. A drawer or a
 * full-window editor that belongs at the edge passes p-0.
 */
export function Overlay({ className, ...props }: ComponentProps<"div">) {
  return createPortal(<div className={cn("fixed inset-0 z-50 p-4", className)} {...props} />, document.body);
}
