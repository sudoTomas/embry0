import { cn } from "@/lib/utils";

interface ConnectionStatusProps {
  configured: boolean;
  label: string;
  className?: string;
}

export function ConnectionStatus({ configured, label, className }: ConnectionStatusProps) {
  return (
    <div className={cn("flex items-center gap-2", className)}>
      <div className={cn("w-2 h-2 rounded-full", configured ? "bg-success" : "bg-destructive")} />
      <span className="text-sm text-white/60">{label}</span>
    </div>
  );
}
