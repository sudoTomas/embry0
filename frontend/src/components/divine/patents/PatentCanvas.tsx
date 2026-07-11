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
 * surfaces. Renders the gold-on-dark patent chrome (header, title,
 * footer) and lets children own the figures inside.
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
        `divine-element relative rounded font-mono text-primary ` +
        `border border-primary/20 ${padding} ${className ?? ""}`
      }
      style={{
        background:
          "linear-gradient(180deg, rgba(20,16,11,0.6) 0%, rgba(12,11,9,0.85) 100%)",
      }}
    >
      {(date || inventor || patentNo) && (
        <div
          className={
            `flex justify-between items-baseline border-b border-primary/35 ` +
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
            `mb-3 text-primary/95`
          }
        >
          {title}
        </div>
      )}

      {children}

      {(elements || epigraph) && (
        <div
          className={
            `flex justify-between items-baseline border-t border-primary/25 ` +
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
