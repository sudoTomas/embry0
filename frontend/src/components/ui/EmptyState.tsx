import type { ReactNode } from "react";
import { clsx } from "clsx";
import { AlchemicalSigil } from "@/components/divine/AlchemicalSigil";
import type { Stage } from "@/lib/sigils";
import type { LucideIcon } from "lucide-react";

interface EmptyStateProps {
  /**
   * Optional alchemical-stage sigil rendered as a large faint glyph.
   * When the divine layer is off (`body[data-divine="off"]`), the sigil is
   * hidden — supply `icon` as the Lucide fallback.
   */
  stage?: Stage;
  /** Lucide fallback icon. Always rendered when no `stage` is supplied. */
  icon?: LucideIcon;
  /** Color for the glyph (CSS color value). Defaults to a stage-neutral muted color. */
  color?: string;
  title: string;
  description?: string;
  children?: ReactNode;
  className?: string;
}

/**
 * Empty-state surface with a large faint sigil + headline + sub-copy.
 * Honors the divine-layer escape hatch: `divine-element` hides the sigil
 * when `body[data-divine="off"]`; the Lucide `icon` becomes the fallback.
 */
export function EmptyState({
  stage,
  icon: Icon,
  color = "rgba(255,255,255,0.18)",
  title,
  description,
  children,
  className,
}: EmptyStateProps) {
  return (
    <div
      className={clsx(
        "flex flex-col items-center justify-center text-center py-16 px-4",
        className,
      )}
    >
      <div className="relative mb-4">
        <div
          className="absolute inset-0 blur-3xl rounded-full scale-150"
          style={{ background: color, opacity: 0.08 }}
          aria-hidden
        />
        {stage && (
          <span
            className="divine-element relative flex items-center justify-center"
            style={{ color }}
          >
            <AlchemicalSigil stage={stage} size={96} />
          </span>
        )}
        {Icon && (
          <Icon
            className={clsx(
              stage && "divine-fallback",
              "relative h-14 w-14",
            )}
            style={{ color }}
          />
        )}
      </div>
      <p className="text-white/55 text-sm font-medium">{title}</p>
      {description && (
        <p className="text-white/30 text-xs mt-1.5 max-w-md">{description}</p>
      )}
      {children && <div className="mt-4">{children}</div>}
    </div>
  );
}
