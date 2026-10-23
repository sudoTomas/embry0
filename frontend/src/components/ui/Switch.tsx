import { cn } from "@/lib/utils";

export interface SwitchProps {
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
  /** The switch's own name; omit it when a <Label htmlFor={id}> names it. */
  label?: string;
  id?: string;
  disabled?: boolean;
  className?: string;
}

/** An on/off control (spec 3.9): a button with role="switch", a thumb that slides. On is the success colour, a state, never the page's gold. */
export function Switch({ checked, onCheckedChange, label, id, disabled, className }: SwitchProps) {
  return (
    <button
      type="button"
      id={id}
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onCheckedChange(!checked)}
      className={cn(
        "relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors motion-reduce:transition-none focus:outline-none focus:ring-2 focus:ring-ring",
        "disabled:cursor-not-allowed disabled:opacity-50",
        checked ? "bg-success" : "bg-white/20",
        className,
      )}
    >
      <span
        className={cn(
          "inline-block h-4 w-4 rounded-full bg-white transition-transform motion-reduce:transition-none",
          checked ? "translate-x-4" : "translate-x-0.5",
        )}
      />
    </button>
  );
}
