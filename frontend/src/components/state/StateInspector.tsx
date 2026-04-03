import { ArrowUp } from "lucide-react";

interface StateField {
  name: string;
  type: string;
  value: string | null;
}

interface StateInspectorProps {
  fields: StateField[];
  activeField?: string; // field currently being written to
}

export function StateInspector({ fields, activeField }: StateInspectorProps) {
  return (
    <div className="rounded-xl p-4" style={{ backgroundColor: "rgba(6,182,212,0.03)", border: "1px solid rgba(6,182,212,0.1)" }}>
      <div className="flex items-center gap-2 mb-3.5">
        <div className="w-6 h-6 rounded-md flex items-center justify-center text-cyan-400" style={{ backgroundColor: "rgba(6,182,212,0.12)", border: "1px solid rgba(6,182,212,0.25)" }}>
          <span className="text-xs">S</span>
        </div>
        <span className="text-sm font-bold text-cyan-400">STATE</span>
        <span className="ml-auto text-[11px] text-white/30 font-mono">JobState (TypedDict)</span>
      </div>

      <div className="flex gap-2.5 overflow-x-auto pb-1">
        {fields.map((field) => (
          <div
            key={field.name}
            className="min-w-[140px] shrink-0 rounded-lg p-2.5"
            style={{
              backgroundColor: "rgba(0,0,0,0.3)",
              border: field.name === activeField ? "1px solid rgba(6,182,212,0.3)" : "1px solid rgba(6,182,212,0.08)",
            }}
          >
            <div className="text-[13px] font-mono font-semibold text-cyan-400">{field.name}</div>
            <div className="text-[10px] text-white/30 mt-0.5">{field.type}</div>
            {field.value !== null ? (
              <div className="mt-2 px-2 py-1 rounded text-xs font-mono bg-cyan-500/10 text-cyan-400 truncate">
                {field.value}
              </div>
            ) : (
              <div className="mt-2 px-2 py-1 rounded text-xs font-mono text-white/20">None</div>
            )}
          </div>
        ))}
      </div>

      {activeField && (
        <div className="flex justify-center mt-2.5">
          <div className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-[11px] font-semibold text-cyan-400" style={{ backgroundColor: "rgba(6,182,212,0.12)", border: "1px solid rgba(6,182,212,0.25)" }}>
            <ArrowUp className="w-3 h-3" />
            write — {activeField}
          </div>
        </div>
      )}
    </div>
  );
}
