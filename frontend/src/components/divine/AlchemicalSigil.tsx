import { STAGE_SIGILS, type Stage } from "@/lib/sigils";

interface AlchemicalSigilProps {
  stage: Stage;
  size?: number;
  className?: string;
  title?: string;
}

/**
 * Renders the classical alchemical glyph for a pipeline stage as a tiny SVG.
 * Stroke inherits the parent text color. No fill, no shadow, no animation.
 *
 * Hidden when the document body carries `data-divine="off"` — see
 * `frontend/src/components/divine/CLAUDE.md` for the layer's hard rules.
 */
export function AlchemicalSigil({ stage, size = 14, className, title }: AlchemicalSigilProps) {
  const path = STAGE_SIGILS[stage];
  if (!path) return null;
  return (
    <svg
      role={title ? "img" : undefined}
      aria-label={title}
      aria-hidden={title ? undefined : true}
      width={size}
      height={size}
      viewBox="0 0 64 64"
      className={`inline-block divine-element ${className ?? ""}`}
      dangerouslySetInnerHTML={{ __html: path }}
    />
  );
}
