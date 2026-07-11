import type { ReactNode } from "react";

interface PatentFigureProps {
  number: string;
  caption?: string;
  inset?: boolean;
  children: ReactNode;
  className?: string;
}

/**
 * Single figure inside a patent canvas. Centers the children, captions
 * "FIG. <number> — <caption>" below in monospace small-caps. `inset`
 * shrinks the caption for sub-figures.
 */
export function PatentFigure({ number, caption, inset = false, children, className }: PatentFigureProps) {
  return (
    <figure
      className={`flex flex-col items-center text-center ${className ?? ""}`}
    >
      <div className="flex items-center justify-center text-primary">
        {children}
      </div>
      <figcaption
        className={
          inset
            ? "text-[9px] tracking-[0.18em] uppercase mt-1.5 opacity-70 text-primary/85"
            : "text-[10px] tracking-[0.2em] uppercase mt-2 opacity-80 text-primary/90"
        }
      >
        FIG. {number}
        {caption ? ` — ${caption}` : ""}
      </figcaption>
    </figure>
  );
}
