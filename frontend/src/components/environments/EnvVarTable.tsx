import type { ReactNode } from "react";
import { Eye, EyeOff, KeyRound, Pencil, X } from "lucide-react";
import type { EnvVar } from "@/lib/types/environment";

interface EnvVarTableProps {
  variables: EnvVar[];
  onEdit: (variable: EnvVar) => void;
  onDelete: (key: string) => void;
  onReveal?: (key: string) => void;
  revealedValues?: Record<string, string>;
  showSource?: boolean;
  showRequired?: boolean;
  sourceMap?: Record<string, "global" | "repo">;
  overrides?: Record<string, string>;
  emptyState?: ReactNode;
}

export function EnvVarTable({
  variables,
  onEdit,
  onDelete,
  onReveal,
  revealedValues = {},
  showSource = false,
  showRequired = false,
  sourceMap = {},
  overrides = {},
  emptyState,
}: EnvVarTableProps) {
  if (variables.length === 0) {
    if (emptyState !== undefined) {
      return <>{emptyState}</>;
    }
    return (
      <div className="flex flex-col items-center justify-center py-16 text-center">
        <div className="relative">
          <div className="absolute inset-0 bg-blue-500/5 blur-2xl rounded-full scale-150" />
          <KeyRound size={40} className="text-white/10 relative" />
        </div>
        <p className="text-white/25 text-sm mt-4 font-medium">No environment variables configured</p>
        <p className="text-white/[0.12] text-xs mt-1">Click &ldquo;Add Variable&rdquo; to set global defaults</p>
      </div>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-white/[0.04]">
            <th className="text-left px-4 py-3 text-[11px] uppercase tracking-wider text-white/35 font-medium">
              Key
            </th>
            <th className="text-left px-4 py-3 text-[11px] uppercase tracking-wider text-white/35 font-medium">
              Value
            </th>
            <th className="text-left px-4 py-3 text-[11px] uppercase tracking-wider text-white/35 font-medium">
              Type
            </th>
            <th className="text-left px-4 py-3 text-[11px] uppercase tracking-wider text-white/35 font-medium">
              Description
            </th>
            {showSource && (
              <th className="text-left px-4 py-3 text-[11px] uppercase tracking-wider text-white/35 font-medium">
                Source
              </th>
            )}
            {showRequired && (
              <th className="text-left px-4 py-3 text-[11px] uppercase tracking-wider text-white/35 font-medium">
                Required
              </th>
            )}
            <th className="text-right px-4 py-3 text-[11px] uppercase tracking-wider text-white/35 font-medium">
              Actions
            </th>
          </tr>
        </thead>
        <tbody>
          {variables.map((v) => {
            const isRevealed = v.key in revealedValues;
            const hasOverride = v.key in overrides;

            return (
              <tr
                key={v.key}
                className="border-b border-white/[0.04] hover:bg-white/[0.02] transition-colors"
              >
                <td className="px-4 py-3 font-mono text-xs text-foreground">{v.key}</td>

                <td className="px-4 py-3 font-mono text-xs text-muted-foreground max-w-[280px]">
                  <div className="flex items-center gap-2">
                    {v.var_type === "secret" ? (
                      <>
                        <span className="truncate">
                          {isRevealed ? revealedValues[v.key] : "\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022"}
                        </span>
                        {onReveal && (
                          <button
                            onClick={() => onReveal(v.key)}
                            className="shrink-0 text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
                            title={isRevealed ? "Hide value" : "Reveal value"}
                          >
                            {isRevealed ? (
                              <EyeOff className="h-3.5 w-3.5" />
                            ) : (
                              <Eye className="h-3.5 w-3.5" />
                            )}
                          </button>
                        )}
                      </>
                    ) : (
                      <span className="truncate">
                        {hasOverride && (
                          <span className="line-through text-white/20 mr-2">{overrides[v.key]}</span>
                        )}
                        {v.value}
                      </span>
                    )}
                  </div>
                </td>

                <td className="px-4 py-3">
                  {v.var_type === "secret" ? (
                    <span className="inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium bg-amber-500/10 text-amber-400 ring-1 ring-inset ring-amber-500/20">
                      secret
                    </span>
                  ) : (
                    <span className="inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium bg-green-500/10 text-green-400 ring-1 ring-inset ring-green-500/20">
                      config
                    </span>
                  )}
                </td>

                <td className="px-4 py-3 text-muted-foreground text-xs max-w-[240px] truncate">
                  {v.description || "\u2014"}
                </td>

                {showSource && (
                  <td className="px-4 py-3">
                    {sourceMap[v.key] === "repo" ? (
                      <span className="inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium bg-blue-500/10 text-blue-400 ring-1 ring-inset ring-blue-500/20">
                        repo
                      </span>
                    ) : (
                      <span className="inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium bg-white/5 text-white/40 ring-1 ring-inset ring-white/10">
                        global
                      </span>
                    )}
                  </td>
                )}

                {showRequired && (
                  <td className="px-4 py-3 text-center">
                    {v.required && <span className="text-amber-400 text-xs">*</span>}
                  </td>
                )}

                <td className="px-4 py-3 text-right">
                  <div className="flex items-center justify-end gap-1">
                    <button
                      onClick={() => onEdit(v)}
                      className="p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-white/[0.05] transition-colors cursor-pointer"
                      title="Edit variable"
                    >
                      <Pencil className="h-3.5 w-3.5" />
                    </button>
                    <button
                      onClick={() => onDelete(v.key)}
                      className="p-1.5 rounded-md text-muted-foreground hover:text-destructive hover:bg-destructive/10 transition-colors cursor-pointer"
                      title="Delete variable"
                    >
                      <X className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
