import type { ReactNode } from "react";

interface PatentCanvasProps {
  patentNo?: string;
  date?: string;
  inventor?: string;
  title?: string;
  epigraph?: string;
  elements?: string;
  children: ReactNode;
  className?: string;
  /** Padding density. Compact = small chrome for inline use; hero = full splash treatment. */
  density?: "compact" | "hero";
}

/**
 * Patent-drawing-style framing component for hero / lore / completion
 * surfaces. Renders the patent chrome (header, title, footer) in the
 * neutral text tones on the raised surface and lets children own the
 * figures inside. Decoration, so never gold: the gold marks only the one
 * thing on a view that needs a person.
 */
export function PatentCanvas({
  patentNo,
  date,
  inventor,
  title,
  epigraph,
  elements,
  children,
  className,
  density = "hero",
}: PatentCanvasProps) {
  const padding = density === "hero" ? "px-7 py-8" : "px-5 py-5";
  const titleSize = density === "hero" ? "text-[13px]" : "text-[11px]";
  const headerSize = density === "hero" ? "text-[10px]" : "text-[9px]";
  return (
    <div
      className={
        `divine-element relative rounded font-mono text-fg-secondary bg-surface ` +
        `border border-border ${padding} ${className ?? ""}`
      }
    >
      {(date || inventor || patentNo) && (
        <div
          className={
            `flex justify-between items-baseline border-b border-border ` +
            `pb-1.5 mb-4 ${headerSize} tracking-[0.15em] opacity-85 uppercase`
          }
        >
          <span>{date}</span>
          <span>{inventor}</span>
          <span>{patentNo}</span>
        </div>
      )}

      {title && (
        <div
          className={
            `text-center ${titleSize} tracking-[0.25em] uppercase ` +
            `mb-3 text-foreground`
          }
        >
          {title}
        </div>
      )}

      {children}

      {(elements || epigraph) && (
        <div
          className={
            `flex justify-between items-baseline border-t border-border ` +
            `pt-1.5 mt-4 text-[9px] tracking-[0.12em] opacity-55 italic`
          }
        >
          <span>{elements}</span>
          <span>{epigraph}</span>
        </div>
      )}
    </div>
  );
}
