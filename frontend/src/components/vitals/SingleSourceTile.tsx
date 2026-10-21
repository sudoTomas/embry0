import { useRef } from "react";
import { useTweenedNumber } from "@/hooks/useTweenedNumber";
import { motionIndex } from "@/lib/motion";
import { cn } from "@/lib/utils";
import { VitalsTile, type TileTone } from "./VitalsTile";

export interface SingleSourceQuery<T> {
  isError: boolean;
  isPending: boolean;
  data: T | undefined;
}

interface SingleSourceTileProps<T> {
  label: string;
  query: SingleSourceQuery<T>;
  format: (value: T) => string;
  /** The status colour, chosen from the value once it is known. */
  tone?: (value: T) => TileTone;
  /** A second fact for the line under the label, from the same source. */
  detail?: string;
  trend?: string;
  className?: string;
  /** Its turn among tiles arriving together (index.css `.motion-enter`).
   * Without one the tile is simply there. */
  turn?: number;
}

// Order is load-bearing: error > loading > ready. React-query can report
// isError=true while still pending on retry, and stale data can persist
// after a refetch failure; surfacing the failure beats showing a busy
// spinner or a known-stale number.
type TileState = "error" | "loading" | "ready";

function resolveState<T>(query: SingleSourceQuery<T>): TileState {
  if (query.isError) return "error";
  if (query.isPending || query.data === undefined) return "loading";
  return "ready";
}

export function SingleSourceTile<T>({
  label,
  query,
  format,
  tone,
  detail,
  trend,
  className,
  turn,
}: SingleSourceTileProps<T>) {
  const state = resolveState(query);
  const valueRef = useRef<HTMLParagraphElement>(null);
  // A number counts up from 0 when it first loads and travels to each later
  // value (the queue polls every 5 s), so a change is seen as one; format()
  // draws every step. Anything that is not a number is drawn as it is.
  const live = state === "ready" && typeof query.data === "number" ? query.data : undefined;
  const shown = useTweenedNumber(live, { countUp: true, tickRef: valueRef });
  let value: string;
  if (state === "error") value = "Unavailable";
  else if (state === "loading") value = "…";
  else value = format((shown ?? query.data) as T);

  return (
    <div
      data-testid={`tile-${label}`}
      data-state={state}
      className={cn("h-full", turn !== undefined && "motion-enter")}
      style={turn !== undefined ? motionIndex(turn) : undefined}
    >
      <VitalsTile
        label={label}
        value={value}
        valueRef={valueRef}
        detail={state === "ready" ? detail : undefined}
        tone={state === "ready" && tone ? tone(query.data as T) : state === "error" ? "bad" : "neutral"}
        trend={state === "ready" ? trend : undefined}
        className={className}
      />
    </div>
  );
}
