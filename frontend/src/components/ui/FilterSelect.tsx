/**
 * A filter dropdown. `label` is the plural noun ("Statuses"); the empty
 * option reads "All statuses" in sentence case. `optionLabel` turns a raw
 * option value (`awaiting_input`) into the words a person reads.
 */
export function FilterSelect({
  label,
  value,
  options,
  onChange,
  optionLabel,
}: {
  label: string;
  value: string | undefined;
  options: string[];
  onChange: (v: string | undefined) => void;
  optionLabel?: (value: string) => string;
}) {
  return (
    <select
      value={value ?? ""}
      onChange={(e) => onChange(e.target.value || undefined)}
      className="h-8 max-w-full rounded-md border border-input bg-overlay px-2 text-sm text-fg focus:outline-none focus:ring-1 focus:ring-ring"
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
