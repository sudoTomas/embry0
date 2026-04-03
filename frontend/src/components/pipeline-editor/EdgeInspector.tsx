import type { Edge } from "@xyflow/react";
import { cn } from "@/lib/utils";

interface EdgeInspectorProps {
  edge: Edge;
  onUpdate: (data: Record<string, unknown>) => void;
}

function SectionHeader({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-[10px] uppercase tracking-wider text-white/30 font-semibold mb-2 mt-4">
      {children}
    </div>
  );
}

function Divider() {
  return <div className="border-t border-white/[0.06] my-3" />;
}

function FieldLabel({ children }: { children: React.ReactNode }) {
  return (
    <label className="text-[11px] text-white/40 mb-1 block">{children}</label>
  );
}

function FieldInput({
  id,
  ...props
}: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      id={id}
      {...props}
      className={cn(
        "w-full bg-white/[0.04] border border-white/[0.08] rounded-md px-2.5 py-1.5 text-xs text-white/80 outline-none focus:border-white/20 transition-colors",
        props.className,
      )}
    />
  );
}

function FieldSelect({
  id,
  children,
  ...props
}: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      id={id}
      {...props}
      className={cn(
        "w-full bg-white/[0.04] border border-white/[0.08] rounded-md px-2.5 py-1.5 text-xs text-white/80 outline-none focus:border-white/20 transition-colors appearance-none cursor-pointer",
        props.className,
      )}
    >
      {children}
    </select>
  );
}

export function EdgeInspector({ edge, onUpdate }: EdgeInspectorProps) {
  const d = (edge.data ?? {}) as Record<string, unknown>;
  const loopConfig = (d.loopConfig as Record<string, unknown>) ?? {};
  const edgeType = (d.edgeType as string) ?? "flow";
  const isFeedback = edgeType === "feedback";

  return (
    <div className="p-4">
      {/* Header badge */}
      <div className="mb-1">
        <span
          className={cn(
            "inline-block px-2 py-0.5 rounded-full text-[10px] font-semibold uppercase tracking-wider",
            isFeedback
              ? "bg-red-500/10 text-red-400/80"
              : "bg-white/[0.06] text-white/40",
          )}
        >
          {isFeedback ? "Feedback Loop" : "Flow Edge"}
        </span>
      </div>

      {/* Source → Target */}
      <div className="flex items-center gap-1.5 mt-2 text-sm">
        <span className="font-mono text-[11px] bg-white/[0.06] border border-white/[0.08] rounded px-2 py-0.5 text-white/60 truncate max-w-[100px]">
          {edge.source}
        </span>
        <span className="text-white/20 text-xs shrink-0">→</span>
        <span className="font-mono text-[11px] bg-white/[0.06] border border-white/[0.08] rounded px-2 py-0.5 text-white/60 truncate max-w-[100px]">
          {edge.target}
        </span>
      </div>

      {!isFeedback && (
        <>
          <Divider />
          <p className="text-[11px] text-white/30 leading-relaxed">
            A flow edge passes the output of the source node as input to the
            target node. No additional configuration required.
          </p>
        </>
      )}

      {isFeedback && (
        <>
          <Divider />

          {/* Loop configuration */}
          <SectionHeader>Loop Configuration</SectionHeader>

          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-2">
              <div>
                <FieldLabel>Max Loops</FieldLabel>
                <FieldInput
                  id="ei-loops"
                  type="number"
                  min="1"
                  max="10"
                  value={(loopConfig.max_loops as number) ?? ""}
                  onChange={(e) =>
                    onUpdate({
                      loopConfig: {
                        ...loopConfig,
                        max_loops: e.target.value
                          ? Number(e.target.value)
                          : null,
                      },
                    })
                  }
                  placeholder="3"
                />
              </div>
              <div>
                <FieldLabel>Budget (USD)</FieldLabel>
                <FieldInput
                  id="ei-budget"
                  type="number"
                  step="0.5"
                  min="0"
                  value={(loopConfig.max_loop_budget_usd as number) ?? ""}
                  onChange={(e) =>
                    onUpdate({
                      loopConfig: {
                        ...loopConfig,
                        max_loop_budget_usd: e.target.value
                          ? Number(e.target.value)
                          : null,
                      },
                    })
                  }
                  placeholder="—"
                />
              </div>
            </div>

            <div>
              <FieldLabel>Feedback Mode</FieldLabel>
              <FieldSelect
                id="ei-mode"
                value={(loopConfig.feedback_mode as string) ?? "result"}
                onChange={(e) =>
                  onUpdate({
                    loopConfig: {
                      ...loopConfig,
                      feedback_mode: e.target.value,
                    },
                  })
                }
              >
                <option value="result">Result (full output)</option>
                <option value="summary">Summary</option>
                <option value="diff">Diff only</option>
              </FieldSelect>
            </div>
          </div>

          <Divider />

          <p className="text-[11px] text-white/25 leading-relaxed">
            Feedback edges create a cycle — the target node's output is fed
            back to the source until the loop condition resolves or limits
            are reached.
          </p>
        </>
      )}
    </div>
  );
}
