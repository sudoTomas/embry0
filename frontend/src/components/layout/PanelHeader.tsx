import type { ReactNode } from "react";
import { OperationGlyph } from "@/components/divine/OperationGlyph";
import type { Operation } from "@/components/divine/operations";

interface PanelHeaderProps {
  title: string;
  trailing?: ReactNode;
  className?: string;
  /**
   * Optional alchemical operation. When set, the lead glyph is replaced
   * by an `<OperationGlyph>` for that operation — the panel "belongs to"
   * that step in the magnum opus. Default is undefined (the generic
   * equator-only lead glyph).
   */
  operation?: Operation;
}

/**
 * Section panel header — double-rule trim (gold + muted echo) with an
 * equator-only lead glyph (default) or an operation-specific glyph
 * (when `operation` prop is set). The double rule echoes the equator
 * across the panel chrome.
 *
 * See `docs/superpowers/specs/2026-05-04-geodesic-identity-design.md` §3.4
 * and `2026-05-04-divine-operations-design.md` (PR B — static glyphs).
 */
export function PanelHeader({ title, trailing, className, operation }: PanelHeaderProps) {
  return (
    <div
      className={
        `divine-element flex items-center gap-2.5 px-2.5 py-2 ` +
        `border-y border-primary/30 [border-bottom-color:rgb(212_175_55_/_0.15)] ` +
        `font-mono text-[11px] tracking-[0.12em] ${className ?? ""}`
      }
    >
      {operation ? (
        <OperationGlyph operation={operation} size={16} titled />
      ) : (
        <svg width="14" height="14" viewBox="0 0 64 64" className="text-primary" aria-hidden="true">
          <circle cx="32" cy="32" r="22" fill="none" stroke="currentColor" strokeWidth="3" />
          <line
            x1="14"
            y1="32"
            x2="50"
            y2="32"
            stroke="currentColor"
            strokeWidth="2"
            opacity="0.7"
          />
        </svg>
      )}
      <span className="opacity-85">{title}</span>
      {trailing !== undefined && <span className="ml-auto opacity-50">{trailing}</span>}
    </div>
  );
}
