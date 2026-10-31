import { useState } from "react";
import { Link, useNavigate } from "react-router";
import { Plus, RotateCcw, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { Card, CardContent } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { providerLabel } from "@/lib/status";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/Tabs";
import { useAgents, useDeleteAgent, useResetAgent } from "@/hooks/useAgents";
import { useIdentityLookup } from "@/hooks/useAgentIdentity";
import { AgentAvatar } from "@/components/agents/AgentAvatar";
import { AdminButton } from "@/components/auth/AdminButton";
import { useIsAdmin } from "@/stores/authStore";
import type { AgentDefinition } from "@/lib/types/agents";
import { runModelOf } from "@/lib/effectiveModel";
import { OperationGlyph } from "@/components/divine/OperationGlyph";
import { agentTypeToOperation } from "@/components/divine/operations";
import { toastApiError } from "@/api/errorToast";
import { useProgression } from "@/hooks/useProgression";
import { LevelBadge } from "@/components/progression/LevelBadge";
import { gamePackOf } from "@/lib/gamePack";

type Filter = "all" | "builtin" | "custom";

const FILTER_LABELS: Record<Filter, string> = { all: "All", builtin: "Built-in", custom: "Custom" };

export function AgentsPage() {
  const { data: agents, isLoading } = useAgents();
  const deleteAgent = useDeleteAgent();
  const resetAgent = useResetAgent();
  const navigate = useNavigate();
  const lookup = useIdentityLookup();
  const isAdmin = useIsAdmin();
  // Each character's level and best skill (spec 2026-10-24); a page without them still lists the agents.
  const progression = useProgression();
  const skillName = new Map((progression.data?.skills ?? []).map((s) => [s.id, s.name]));
  const characterOf = new Map((progression.data?.characters ?? []).map((c) => [c.agent_type, c]));
  const pack = progression.data ? gamePackOf(progression.data) : null;

  const [filter, setFilter] = useState<Filter>("all");
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [confirmReset, setConfirmReset] = useState<string | null>(null);

  const filtered = (agents ?? []).filter((a: AgentDefinition) => {
    if (filter === "builtin") return a.is_builtin;
    if (filter === "custom") return !a.is_builtin;
    return true;
  });
  // A definition no job runs (the brief writer's voices) is listed apart, under Background writers.
  const jobAgents = filtered.filter((a) => a.runs_in_jobs !== false);
  const writers = filtered.filter((a) => a.runs_in_jobs === false);

  const handleDelete = (type: string) => {
    if (confirmDelete !== type) {
      setConfirmDelete(type);
      return;
    }
    deleteAgent.mutate(type, {
      onSuccess: () => toast.success(`Agent "${type}" deleted`),
      onError: (e) => toastApiError(e, "Failed to delete"),
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
      onError: (e) => toastApiError(e, "Failed to reset"),
    });
    setConfirmReset(null);
  };

  const handleRowClick = (type: string) => {
    navigate(`/agents/${type}`);
  };

  /** One agent's row; a background writer's (runs_in_jobs false) names where its model comes from instead. */
  const renderRow = (agent: AgentDefinition, background: boolean) => {
    const identity = lookup(agent.type);
    const op = agentTypeToOperation(agent.type);
    const run = runModelOf(agent);
    const character = characterOf.get(agent.type);
    const best = character?.skills.find((s) => s.stat === character.best_skill);
    return (
      <tr
        key={agent.type}
        onClick={() => handleRowClick(agent.type)}
        className="cursor-pointer"
      >
        {/* Type: the row's identity (its sprite and display name), the slug when it differs. */}
        <td>
          <div className="flex items-center gap-2.5">
            <AgentAvatar identity={identity} size={32} />
            <div className="min-w-0">
              <div className="whitespace-nowrap text-sm font-semibold text-white/90">{identity.displayName}</div>
              {identity.displayName !== agent.type && (
                <div className="font-mono text-xs text-white/40">{agent.type}</div>
              )}
              {character && (
                <LevelBadge
                  agentType={agent.type}
                  level={character.level}
                  totalLevel={character.total_level}
                  bestSkill={skillName.get(character.best_skill) ?? character.best_skill}
                  bestLevel={best?.level ?? 1}
                  tier={character.tier}
                  pack={pack}
                />
              )}
              {agent.is_builtin && <Badge tone="neutral">Built-in</Badge>}
            </div>
            {op && (
              <span className="hidden sm:flex items-center justify-center w-8 h-8 shrink-0">
                <OperationGlyph operation={op} size={28} titled />
              </span>
            )}
          </div>
        </td>

        {/* Description */}
        <td className="hidden max-w-xs sm:table-cell">
          {identity.tagline && (
            <p className="text-sm italic text-white/50 truncate">{identity.tagline}</p>
          )}
          <p className="text-sm text-white/60 truncate">{agent.description || "—"}</p>
        </td>

        {/* Model: what a job that names none runs, and what chose it
            when that is not the agent's own model. A background writer runs in no job: its model is
            background_models'. */}
        <td className="hidden sm:table-cell">
          {background ? (
            <span className="text-sm text-white/60" data-testid="background-model">
              from <span className="font-mono">background_models</span>
            </span>
          ) : (
            <span className="text-sm font-mono text-white/60">{run.model}</span>
          )}
          {!background && run.reason && (
            <p className="text-xs text-white/40" data-testid="run-model-reason">
              {run.reason}
              {run.model !== agent.model && agent.model && (
                <> · stored <span className="whitespace-nowrap font-mono">{agent.model}</span></>
              )}
            </p>
          )}
        </td>

        {/* Provider: the catalog provider that model routes to, and
            whether it has a credential now (GET /agents). */}
        <td>
          {/* A phone stacks the provider's name over its pill, so the
              column says whose readiness it is. */}
          {background ? (
            <span className="text-sm text-white/40">—</span>
          ) : (
            <div className="flex flex-col items-start gap-1 sm:flex-row sm:items-center sm:gap-2">
              <span className="whitespace-nowrap text-sm text-white/60">{run.provider ? providerLabel(run.provider) : "None"}</span>
              {run.ready === true && <Badge tone="success">Ready</Badge>}
              {run.ready === false && (
                <Badge
                  tone="error"
                  title={
                    run.provider
                      ? "No credential is configured for this provider"
                      : "No configured provider serves this model"
                  }
                >
                  Not ready
                </Badge>
              )}
            </div>
          )}
        </td>

        {/* Tools count */}
        <td className="hidden sm:table-cell">
          <span className="text-sm text-white/60">{agent.tools?.length ?? 0}</span>
        </td>

        {/* Skills count, the names on hover */}
        <td className="hidden sm:table-cell">
          <span className="text-sm text-white/60" title={(agent.skills ?? []).join(", ")}>
            {agent.skills?.length ?? 0}
          </span>
        </td>

        {/* Actions */}
        <td>
          <div
            className="flex items-center justify-end gap-2"
            onClick={(e) => e.stopPropagation()}
          >
            {agent.is_builtin ? (
              <AdminButton
                variant="outline"
                size="xs"
                onClick={() => handleReset(agent.type)}
                disabled={resetAgent.isPending}
                className={confirmReset === agent.type ? "border-warning/40 text-warning hover:bg-warning/10" : ""}
              >
                <RotateCcw className="w-3.5 h-3.5" />
                {confirmReset === agent.type ? "Confirm reset" : "Reset"}
              </AdminButton>
            ) : (
              <AdminButton
                variant="outline"
                size="xs"
                onClick={() => handleDelete(agent.type)}
                disabled={deleteAgent.isPending}
                className={confirmDelete === agent.type ? "border-destructive/40 text-destructive hover:bg-destructive/10" : ""}
              >
                <Trash2 className="w-3.5 h-3.5" />
                {confirmDelete === agent.type ? "Confirm delete" : "Delete"}
              </AdminButton>
            )}
          </div>
        </td>
      </tr>
    );
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
        <h1 className="title-page text-xl font-bold">Agents</h1>
        {isAdmin ? (
          <Link to="/agents/new">
            <Button>
              <Plus className="w-4 h-4" />
              New agent
            </Button>
          </Link>
        ) : (
          <AdminButton>
            <Plus className="w-4 h-4" />
            New agent
          </AdminButton>
        )}
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
                      <th className="hidden sm:table-cell" title="The model a job that names none runs this agent on">Model</th>
                      <th>Provider</th>
                      <th className="hidden sm:table-cell">Tools</th>
                      <th className="hidden sm:table-cell">Skills</th>
                      <th className="text-right">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {jobAgents.map((agent: AgentDefinition) => renderRow(agent, false))}
                  </tbody>
                </table>
              </div>
            )}
          </CardContent>
        </Card>
        {!isLoading && writers.length > 0 && (
          <section className="mt-6 space-y-2" data-testid="background-writers" aria-labelledby="background-writers-title">
            <h2 id="background-writers-title" className="text-sm font-semibold text-white/80">
              Background writers
            </h2>
            <p className="text-xs text-white/40">
              The voices of the plain-words briefs: no job runs them. Each writes on the model background_models
              names for it, and its answer's format is embry0's.
            </p>
            <Card>
              <CardContent className="p-0">
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
                    <tbody>{writers.map((agent: AgentDefinition) => renderRow(agent, true))}</tbody>
                  </table>
                </div>
              </CardContent>
            </Card>
          </section>
        )}
      </TabsContent>
    </Tabs>
  );
}
