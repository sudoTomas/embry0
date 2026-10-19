import { useState } from "react";
import { Link, useNavigate } from "react-router";
import { Bot, Plus, RotateCcw, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { Card, CardContent } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { providerLabel } from "@/lib/status";
import { IconBox } from "@/components/ui/IconBox";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/Tabs";
import { useAgents, useDeleteAgent, useResetAgent } from "@/hooks/useAgents";
import type { AgentDefinition } from "@/lib/types/agents";
import { OperationGlyph } from "@/components/divine/OperationGlyph";
import { agentTypeToOperation } from "@/components/divine/operations";
import { specialistVisual } from "@/lib/specialistAgents";
import { cn } from "@/lib/utils";

type Filter = "all" | "builtin" | "custom";

const FILTER_LABELS: Record<Filter, string> = { all: "All", builtin: "Built-in", custom: "Custom" };

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
    <Tabs
      defaultValue="all"
      value={filter}
      onValueChange={(v) => setFilter(v as Filter)}
      className="space-y-6"
    >
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold">Agents</h1>
        <Link to="/agents/new">
          <Button>
            <Plus className="w-4 h-4" />
            New agent
          </Button>
        </Link>
      </div>

      {/* Filter: the shared tab bar, so the selected state matches every other tab set. */}
      <TabsList>
        {(Object.keys(FILTER_LABELS) as Filter[]).map((f) => (
          <TabsTrigger key={f} value={f}>
            {FILTER_LABELS[f]}
          </TabsTrigger>
        ))}
      </TabsList>

      {/* Table: the panel of the selected filter tab. Below sm only the type,
          the provider's readiness and the action stay, so nothing important
          scrolls out of a phone screen. */}
      <TabsContent value={filter} className="mt-0">
        <Card>
          <CardContent className="p-0">
            {isLoading ? (
              <div className="py-12 text-center text-white/40 text-sm">Loading...</div>
            ) : filtered.length === 0 ? (
              <div className="py-12 text-center text-white/40 text-sm">No agents found</div>
            ) : (
              <div className="overflow-x-auto">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Type</th>
                      <th className="hidden sm:table-cell">Description</th>
                      <th className="hidden sm:table-cell">Model</th>
                      <th>Provider</th>
                      <th className="hidden sm:table-cell">Tools</th>
                      <th className="hidden sm:table-cell">Skills</th>
                      <th className="text-right">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filtered.map((agent: AgentDefinition) => (
                      <tr
                        key={agent.type}
                        onClick={() => handleRowClick(agent.type)}
                        className="cursor-pointer"
                      >
                        {/* Type */}
                        <td>
                          <div className="flex items-center gap-2.5">
                            {(() => {
                              const specialist = specialistVisual(agent.type);
                              if (specialist) {
                                const Icon = specialist.icon;
                                return (
                                  <span
                                    data-testid={`agent-mark-${agent.type}`}
                                    className={cn(
                                      "hidden sm:flex items-center justify-center w-8 h-8 shrink-0 rounded-lg border",
                                      specialist.tone,
                                    )}
                                  >
                                    <Icon className="w-4 h-4" aria-hidden="true" />
                                  </span>
                                );
                              }
                              const op = agentTypeToOperation(agent.type);
                              return op ? (
                                <span className="hidden sm:flex items-center justify-center w-8 h-8 shrink-0">
                                  <OperationGlyph operation={op} size={28} titled />
                                </span>
                              ) : (
                                <IconBox icon={Bot} color="var(--color-primary)" size="sm" className="hidden sm:flex" />
                              );
                            })()}
                            <div>
                              <div className="whitespace-nowrap text-sm font-medium text-white/90">{agent.type}</div>
                              {agent.is_builtin && <Badge tone="neutral">Built-in</Badge>}
                            </div>
                          </div>
                        </td>

                        {/* Description */}
                        <td className="hidden max-w-xs sm:table-cell">
                          <p className="text-sm text-white/60 truncate">{agent.description || "—"}</p>
                        </td>

                        {/* Model */}
                        <td className="hidden sm:table-cell">
                          <span className="text-sm font-mono text-white/60">{agent.model}</span>
                        </td>

                        {/* Provider: the catalog provider the model routes to, and
                            whether it has a credential now (GET /agents). */}
                        <td>
                          {/* A phone stacks the provider's name over its pill, so the
                              column says whose readiness it is. */}
                          <div className="flex flex-col items-start gap-1 sm:flex-row sm:items-center sm:gap-2">
                            <span className="whitespace-nowrap text-sm text-white/60">{agent.provider ? providerLabel(agent.provider) : "None"}</span>
                            {agent.provider_ready === true && <Badge tone="success">Ready</Badge>}
                            {agent.provider_ready === false && (
                              <Badge
                                tone="error"
                                title={
                                  agent.provider
                                    ? "No credential is configured for this provider"
                                    : "No configured provider serves this model"
                                }
                              >
                                Not ready
                              </Badge>
                            )}
                          </div>
                        </td>

                        {/* Tools count */}
                        <td className="hidden sm:table-cell">
                          <span className="text-sm text-white/60">{agent.tools?.length ?? 0}</span>
                        </td>

                        {/* Skills count */}
                        <td className="hidden sm:table-cell">
                          <span className="text-sm text-white/60">{agent.skills?.length ?? 0}</span>
                        </td>

                        {/* Actions */}
                        <td>
                          <div
                            className="flex items-center justify-end gap-2"
                            onClick={(e) => e.stopPropagation()}
                          >
                            {agent.is_builtin ? (
                              <Button
                                variant="outline"
                                size="xs"
                                onClick={() => handleReset(agent.type)}
                                disabled={resetAgent.isPending}
                                className={confirmReset === agent.type ? "border-warning/40 text-warning hover:bg-warning/10" : ""}
                              >
                                <RotateCcw className="w-3.5 h-3.5" />
                                {confirmReset === agent.type ? "Confirm reset" : "Reset"}
                              </Button>
                            ) : (
                              <Button
                                variant="outline"
                                size="xs"
                                onClick={() => handleDelete(agent.type)}
                                disabled={deleteAgent.isPending}
                                className={confirmDelete === agent.type ? "border-destructive/40 text-destructive hover:bg-destructive/10" : ""}
                              >
                                <Trash2 className="w-3.5 h-3.5" />
                                {confirmDelete === agent.type ? "Confirm delete" : "Delete"}
                              </Button>
                            )}
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </CardContent>
        </Card>
      </TabsContent>
    </Tabs>
  );
}
