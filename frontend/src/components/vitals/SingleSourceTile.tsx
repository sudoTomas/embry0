import { VitalsTile } from "./VitalsTile";

export interface SingleSourceQuery<T> {
  isError: boolean;
  isPending: boolean;
  data: T | undefined;
}

interface SingleSourceTileProps<T> {
  label: string;
  query: SingleSourceQuery<T>;
  format: (value: T) => string;
  trend?: string;
  className?: string;
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
  trend,
  className,
}: SingleSourceTileProps<T>) {
  const state = resolveState(query);
  let value: string;
  if (state === "error") value = "Unavailable";
  else if (state === "loading") value = "…";
  else value = format(query.data as T);

  return (
    <div data-testid={`tile-${label}`} data-state={state}>
      <VitalsTile
        label={label}
        value={value}
        trend={state === "ready" ? trend : undefined}
        className={className}
      />
    </div>
  );
}
