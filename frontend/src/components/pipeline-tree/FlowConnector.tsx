import { cn } from "@/lib/utils";

interface FlowConnectorProps {
  active?: boolean;
  className?: string;
}

export function FlowConnector({ active = false, className }: FlowConnectorProps) {
  return (
    <div className={cn("relative mx-auto", active ? "opacity-100" : "opacity-20", className)} style={{ width: 2, height: 32 }}>
      <div className="absolute inset-0" style={{
        background: "repeating-linear-gradient(to bottom, rgba(6,182,212,0.25) 0px, rgba(6,182,212,0.25) 4px, transparent 4px, transparent 8px)",
      }} />
      {active && (
        <div className="absolute w-2 h-2 rounded-full" style={{
          left: -3, backgroundColor: "#06b6d4",
          boxShadow: "0 0 8px rgba(6,182,212,0.6)",
          animation: "athanor-flow-down 1.5s infinite",
        }} />
      )}
    </div>
  );
}
