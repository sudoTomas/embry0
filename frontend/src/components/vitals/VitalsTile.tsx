import { cn } from "@/lib/utils";

interface VitalsTileProps {
  label: string;
  value: string;
  trend?: string;
  className?: string;
}

export function VitalsTile({ label, value, trend, className }: VitalsTileProps) {
  return (
    <div
      className={cn(
        "rounded-lg border border-border bg-card px-4 py-3",
        className,
      )}
    >
      <p className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
        {label}
      </p>
      <p className="mt-1 font-display text-2xl font-semibold text-foreground">
        {value}
      </p>
      {trend !== undefined && (
        <p
          data-slot="trend"
          className="mt-1 text-xs text-muted-foreground"
        >
          {trend}
        </p>
      )}
    </div>
  );
}
