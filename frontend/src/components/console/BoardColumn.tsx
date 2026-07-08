import type { ReactNode } from "react";
import { cn } from "@/lib/utils";
import { Skeleton } from "@/components/ui/PageSkeleton";
import type { BoardColumnConfig, BoardColumnTint } from "@/lib/boardColumns";

const TINT_CLASSES: Record<BoardColumnTint, { label: string; pill: string }> = {
  amber: { label: "text-amber-400", pill: "bg-amber-400/10 text-amber-400" },
  success: { label: "text-success", pill: "bg-success/10 text-success" },
  neutral: { label: "text-white/60", pill: "bg-white/[0.06] text-white/60" },
};

interface BoardColumnProps {
  column: BoardColumnConfig;
  count: number;
  /** First-load skeleton — true until the first successful jobs poll. */
  isLoading?: boolean;
  children?: ReactNode;
}

/**
 * One board lane. Lowercase label + count pill (dimming at zero), tinted per
 * the lane config (Needs You amber, Done success — the Speculum lane-tint
 * idiom in embry0's own tokens). Renders skeleton cards on first load and a
 * quiet dashed placeholder when empty, so the actionable Needs You lane
 * stays visible even with nothing in it.
 */
export function BoardColumn({ column, count, isLoading = false, children }: BoardColumnProps) {
  const tint = TINT_CLASSES[column.tint];
  return (
    <div className="flex min-w-0 flex-col gap-2" data-testid={`board-column-${column.id}`}>
      <div className="flex items-center gap-2 px-1">
        <span className={cn("text-xs font-semibold lowercase tracking-wide", tint.label)}>
          {column.label}
        </span>
        <span
          data-testid="count-pill"
          className={cn(
            "rounded-full px-1.5 py-0.5 text-[10px] font-mono",
            tint.pill,
            count === 0 && "opacity-40",
          )}
        >
          {count}
        </span>
      </div>
      <div className="flex flex-col gap-2">
        {isLoading ? (
          <>
            <Skeleton className="h-24 rounded-lg" />
            <Skeleton className="h-24 rounded-lg" />
          </>
        ) : count === 0 ? (
          <div
            data-testid="empty-lane"
            className="rounded-lg border border-dashed border-white/[0.06] py-6 text-center text-xs text-white/20"
          >
            empty
          </div>
        ) : (
          children
        )}
      </div>
    </div>
  );
}
