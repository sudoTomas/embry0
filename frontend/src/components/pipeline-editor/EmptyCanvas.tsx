import { Workflow } from "lucide-react";

export function EmptyCanvas() {
  return (
    <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none z-10">
      <div className="relative">
        <div className="absolute inset-0 bg-blue-500/10 blur-3xl rounded-full scale-150" />
        <Workflow size={48} className="text-white/15 relative" />
      </div>
      <p className="text-white/25 text-sm mt-4 font-medium">
        Drag agents from the bar below to build your pipeline
      </p>
      <p className="text-white/12 text-xs mt-1.5">
        Connect nodes by dragging between handles
      </p>
    </div>
  );
}
