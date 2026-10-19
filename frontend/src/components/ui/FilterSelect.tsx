import { cn } from "@/lib/utils";

/**
 * A filter dropdown. `label` is the plural noun ("Statuses"); the empty
 * option reads "All statuses" in sentence case. `optionLabel` turns a raw
 * option value (`awaiting_input`) into the words a person reads.
 *
 * A select is as wide as its longest option, and a flex item never shrinks
 * below that on its own, so a long repository name pushed a row of filters
 * past a phone screen. min-w-0 lets it give way; max-w-full caps it alone.
 */
export function FilterSelect({
  label,
  value,
  options,
  onChange,
  optionLabel,
  className,
}: {
  label: string;
  value: string | undefined;
  options: string[];
  onChange: (v: string | undefined) => void;
  optionLabel?: (value: string) => string;
  className?: string;
}) {
  return (
    <select
      value={value ?? ""}
      onChange={(e) => onChange(e.target.value || undefined)}
      className={cn(
        "h-8 min-w-0 max-w-full rounded-md border border-input bg-overlay px-2 text-sm text-fg focus:outline-none focus:ring-1 focus:ring-ring",
        className,
      )}
      aria-label={label}
    >
      <option value="">All {label.toLowerCase()}</option>
      {options.map((o) => (
        <option key={o} value={o}>
          {optionLabel ? optionLabel(o) : o}
        </option>
      ))}
    </select>
  );
}
