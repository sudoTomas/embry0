import { useState } from "react";
import type { Edge } from "@xyflow/react";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";

interface EdgeDetailPopupProps {
  edge: Edge;
  onUpdate: (data: Record<string, unknown>) => void;
  onClose: () => void;
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

export function EdgeDetailPopup({ edge, onUpdate, onClose }: EdgeDetailPopupProps) {
  const d = (edge.data ?? {}) as Record<string, unknown>;
  const initialLoopConfig = (d.loopConfig as Record<string, unknown>) ?? {};
  const edgeType = (d.edgeType as string) ?? "flow";
  const isFeedback = edgeType === "feedback";

  const [loopConfig, setLoopConfig] = useState<Record<string, unknown>>(initialLoopConfig);

  function handleSave() {
    onUpdate({ loopConfig });
    onClose();
  }

  return (
    <>
      <style>{`
        @keyframes popup-in {
          from {
            opacity: 0;
            transform: scale(0.95) translateY(8px);
          }
          to {
            opacity: 1;
            transform: scale(1) translateY(0);
          }
        }
        .edge-detail-popup-card {
          animation: popup-in 0.25s ease-out;
        }
      `}</style>

      {/* Overlay */}
      <div
        className="absolute inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm"
        onClick={onClose}
      >
        {/* Card */}
        <div
          className={cn(
            "edge-detail-popup-card w-[400px] rounded-2xl bg-[#0f1419] overflow-y-auto",
            isFeedback
              ? "border border-red-400/20"
              : "border border-white/[0.08]",
          )}
          style={{
            boxShadow:
              "0 0 40px rgba(0,0,0,0.5), 0 15px 30px rgba(0,0,0,0.4)",
          }}
          onClick={(e) => e.stopPropagation()}
        >
          {/* Header */}
          <div className="p-5 pb-4 border-b border-white/[0.06] flex items-start justify-between gap-3">
            <div className="flex flex-col gap-2 min-w-0">
              {/* Edge type badge */}
              <span
                className={cn(
                  "inline-block px-2.5 py-1 rounded-full text-[10px] font-semibold uppercase tracking-wider",
                  isFeedback
                    ? "bg-red-500/10 text-red-400/80 border border-red-500/20"
                    : "bg-white/[0.06] text-white/40 border border-white/[0.08]",
                )}
              >
                {isFeedback ? "Feedback Loop" : "Flow Edge"}
              </span>

              {/* Source → Target */}
              <div className="flex items-center gap-1.5">
                <span className="font-mono text-[11px] bg-white/[0.06] border border-white/[0.08] rounded px-2 py-0.5 text-white/60 truncate max-w-[120px]">
                  {edge.source}
                </span>
                <span className="text-white/20 text-xs shrink-0">→</span>
                <span className="font-mono text-[11px] bg-white/[0.06] border border-white/[0.08] rounded px-2 py-0.5 text-white/60 truncate max-w-[120px]">
                  {edge.target}
                </span>
              </div>
            </div>

            {/* Close button */}
            <button
              onClick={onClose}
              className="shrink-0 p-1 rounded-md text-white/30 hover:text-white/60 hover:bg-white/[0.06] transition-colors"
            >
              <X size={14} />
            </button>
          </div>

          {/* Body */}
          <div className="p-5">
            {!isFeedback && (
              <p className="text-[11px] text-white/30 leading-relaxed">
                A flow edge passes the output of the source node as input to the
                target node. No additional configuration required.
              </p>
            )}

            {isFeedback && (
              <div className="space-y-3">
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <FieldLabel>Max Loops</FieldLabel>
                    <FieldInput
                      id="edp-loops"
                      type="number"
                      min="1"
                      max="10"
                      value={(loopConfig.max_loops as number) ?? ""}
                      onChange={(e) =>
                        setLoopConfig((prev) => ({
                          ...prev,
                          max_loops: e.target.value
                            ? Number(e.target.value)
                            : null,
                        }))
                      }
                      placeholder="3"
                    />
                  </div>
                  <div>
                    <FieldLabel>Loop Budget (USD)</FieldLabel>
                    <FieldInput
                      id="edp-budget"
                      type="number"
                      step="0.5"
                      min="0"
                      value={(loopConfig.max_loop_budget_usd as number) ?? ""}
                      onChange={(e) =>
                        setLoopConfig((prev) => ({
                          ...prev,
                          max_loop_budget_usd: e.target.value
                            ? Number(e.target.value)
                            : null,
                        }))
                      }
                      placeholder="—"
                    />
                  </div>
                </div>

                <div>
                  <FieldLabel>Feedback Mode</FieldLabel>
                  <FieldSelect
                    id="edp-mode"
                    value={(loopConfig.feedback_mode as string) ?? "result"}
                    onChange={(e) =>
                      setLoopConfig((prev) => ({
                        ...prev,
                        feedback_mode: e.target.value,
                      }))
                    }
                  >
                    <option value="result">Result (full output)</option>
                    <option value="summary">Summary</option>
                    <option value="diff">Diff only</option>
                  </FieldSelect>
                </div>

                <p className="text-[11px] text-white/25 leading-relaxed pt-1">
                  Feedback edges create a cycle — the target node&apos;s output is fed
                  back to the source until the loop resolves or limits are reached.
                </p>
              </div>
            )}
          </div>

          {/* Footer */}
          <div className="p-5 pt-3 border-t border-white/[0.06] flex items-center justify-end gap-2">
            {isFeedback ? (
              <>
                <button
                  onClick={onClose}
                  className="px-3 py-1.5 rounded-lg text-xs text-white/40 hover:text-white/60 hover:bg-white/[0.06] transition-colors"
                >
                  Cancel
                </button>
                <button
                  onClick={handleSave}
                  className="px-3 py-1.5 rounded-lg text-xs bg-white/[0.08] text-white/80 hover:bg-white/[0.12] border border-white/[0.1] transition-colors"
                >
                  Save
                </button>
              </>
            ) : (
              <button
                onClick={onClose}
                className="px-3 py-1.5 rounded-lg text-xs bg-white/[0.08] text-white/80 hover:bg-white/[0.12] border border-white/[0.1] transition-colors"
              >
                Close
              </button>
            )}
          </div>
        </div>
      </div>
    </>
  );
}
