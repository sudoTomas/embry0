import { useState } from "react";
import { Link, useNavigate } from "react-router";
import { Bot, Plus, RotateCcw, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { Card, CardContent } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { IconBox } from "@/components/ui/IconBox";
import { useAgents, useDeleteAgent, useResetAgent } from "@/hooks/useAgents";
import type { AgentDefinition } from "@/lib/types/agents";
import { OperationGlyph } from "@/components/divine/OperationGlyph";
import { agentTypeToOperation } from "@/components/divine/operations";

type Filter = "all" | "builtin" | "custom";

export function AgentsPage() {
  const { data: agents, isLoading } = useAgents();
  const deleteAgent = useDeleteAgent();
  const resetAgent = useResetAgent();
  const navigate = useNavigate();

  const [filter, setFilter] = useState<Filter>("all");
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [confirmReset, setConfirmReset] = useState<string | null>(null);

  const filtered = (agents ?? []).filter((a: AgentDefinition) => {
    if (filter === "builtin") return a.is_builtin;
    if (filter === "custom") return !a.is_builtin;
    return true;
  });

  const handleDelete = (type: string) => {
    if (confirmDelete !== type) {
      setConfirmDelete(type);
      return;
    }
    deleteAgent.mutate(type, {
      onSuccess: () => toast.success(`Agent "${type}" deleted`),
      onError: (e) => toast.error(`Failed to delete: ${e.message}`),
    });
    setConfirmDelete(null);
  };

  const handleReset = (type: string) => {
    if (confirmReset !== type) {
      setConfirmReset(type);
      return;
    }
    resetAgent.mutate(type, {
      onSuccess: () => toast.success(`Agent "${type}" reset to defaults`),
      onError: (e) => toast.error(`Failed to reset: ${e.message}`),
    });
    setConfirmReset(null);
  };

  const handleRowClick = (type: string) => {
    navigate(`/agents/${type}`);
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Agents</h1>
        <Link to="/agents/new">
          <Button>
            <Plus className="w-4 h-4" />
            New Agent
          </Button>
        </Link>
      </div>

      {/* Filter bar */}
      <div className="flex gap-1 p-1 rounded-lg bg-white/[0.03] border border-white/[0.06] w-fit">
        {(["all", "builtin", "custom"] as Filter[]).map((f) => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={`px-3 py-1.5 rounded-md text-sm font-medium transition-colors capitalize ${
              filter === f
                ? "bg-white/[0.08] text-white"
                : "text-white/40 hover:text-white/70"
            }`}
          >
            {f === "all" ? "All" : f === "builtin" ? "Built-in" : "Custom"}
          </button>
        ))}
      </div>

      {/* Table */}
      <Card>
        <CardContent className="p-0">
          {isLoading ? (
            <div className="py-12 text-center text-white/40 text-sm">Loading...</div>
          ) : filtered.length === 0 ? (
            <div className="py-12 text-center text-white/40 text-sm">No agents found</div>
          ) : (
            <table className="w-full">
              <thead>
                <tr className="border-b border-white/[0.06]">
                  <th className="text-left px-6 py-3 text-xs font-medium text-white/40 uppercase tracking-wider">Type</th>
                  <th className="text-left px-6 py-3 text-xs font-medium text-white/40 uppercase tracking-wider">Description</th>
                  <th className="text-left px-6 py-3 text-xs font-medium text-white/40 uppercase tracking-wider">Model</th>
                  <th className="text-left px-6 py-3 text-xs font-medium text-white/40 uppercase tracking-wider">Tools</th>
                  <th className="text-left px-6 py-3 text-xs font-medium text-white/40 uppercase tracking-wider">Skills</th>
                  <th className="text-right px-6 py-3 text-xs font-medium text-white/40 uppercase tracking-wider">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/[0.04]">
                {filtered.map((agent: AgentDefinition) => (
                  <tr
                    key={agent.type}
                    onClick={() => handleRowClick(agent.type)}
                    className="cursor-pointer hover:bg-white/[0.02] transition-colors"
                  >
                    {/* Type */}
                    <td className="px-6 py-3">
                      <div className="flex items-center gap-2.5">
                        {(() => {
                          const op = agentTypeToOperation(agent.type);
                          return op ? (
                            <span className="flex items-center justify-center w-8 h-8 shrink-0">
                              <OperationGlyph operation={op} size={28} titled />
                            </span>
                          ) : (
                            <IconBox icon={Bot} color="#d4af37" size="sm" />
                          );
                        })()}
                        <div>
                          <div className="text-sm font-medium text-white/90">{agent.type}</div>
                          {agent.is_builtin && (
                            <span className="text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded bg-cyan-500/10 text-cyan-500 border border-cyan-500/20">
                              built-in
                            </span>
                          )}
                        </div>
                      </div>
                    </td>

                    {/* Description */}
                    <td className="px-6 py-3 max-w-xs">
                      <p className="text-sm text-white/60 truncate">{agent.description || "—"}</p>
                    </td>

                    {/* Model */}
                    <td className="px-6 py-3">
                      <span className="text-sm font-mono text-white/60">{agent.model}</span>
                    </td>

                    {/* Tools count */}
                    <td className="px-6 py-3">
                      <span className="text-sm text-white/60">{agent.tools?.length ?? 0}</span>
                    </td>

                    {/* Skills count */}
                    <td className="px-6 py-3">
                      <span className="text-sm text-white/60">{agent.skills?.length ?? 0}</span>
                    </td>

                    {/* Actions */}
                    <td className="px-6 py-3">
                      <div
                        className="flex items-center justify-end gap-2"
                        onClick={(e) => e.stopPropagation()}
                      >
                        {agent.is_builtin ? (
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => handleReset(agent.type)}
                            disabled={resetAgent.isPending}
                            className={confirmReset === agent.type ? "border-orange-500/40 text-orange-400 hover:bg-orange-500/10" : ""}
                          >
                            <RotateCcw className="w-3.5 h-3.5" />
                            {confirmReset === agent.type ? "Confirm Reset" : "Reset"}
                          </Button>
                        ) : (
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => handleDelete(agent.type)}
                            disabled={deleteAgent.isPending}
                            className={confirmDelete === agent.type ? "border-destructive/40 text-destructive hover:bg-destructive/10" : ""}
                          >
                            <Trash2 className="w-3.5 h-3.5" />
                            {confirmDelete === agent.type ? "Confirm Delete" : "Delete"}
                          </Button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
