import { useState, useEffect, useMemo } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { ArrowLeft } from "lucide-react";
import { toast } from "sonner";
import { OperationGlyph } from "@/components/divine/OperationGlyph";
import {
  agentTypeToOperation,
  OPERATION_ELEMENT,
  OPERATION_NUMERAL,
} from "@/components/divine/operations";
import { Button, buttonVariants } from "@/components/ui/Button";
import { AdminButton } from "@/components/auth/AdminButton";
import { Badge } from "@/components/ui/Badge";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { Select } from "@/components/ui/Select";
import { Textarea } from "@/components/ui/Textarea";
import { AgentIdentityFields } from "@/components/agents/AgentIdentityFields";
import { ConfigSection } from "@/components/agents/ConfigSection";
import { SkillSheet } from "@/components/progression/SkillSheet";
import { gamePackOf, label, tierName } from "@/lib/gamePack";
import { ToolChecklist } from "@/components/agents/ToolChecklist";
import { SkillPicker } from "@/components/skills/SkillPicker";
import { useAgent, useCreateAgent, useUpdateAgent } from "@/hooks/useAgents";
import { useModels } from "@/hooks/useModels";
import { ModelSelect } from "@/components/models/ModelSelect";
import { useProgression } from "@/hooks/useProgression";
import { useSkills } from "@/hooks/useSkills";
import { useTools } from "@/hooks/useTools";
import { DEFAULT_AVATAR, DEFAULT_COLOR, identityOf, type AvatarId, type PaletteId } from "@/lib/agentIdentity";
import { runOverrideNote } from "@/lib/effectiveModel";
import { providerForModel } from "@/lib/providers";
import { providerLabel } from "@/lib/status";
import type { AgentCreateRequest, AgentDefinition, AgentUpdateRequest } from "@/lib/types/agents";
import { toastApiError } from "@/api/errorToast";
import { cn } from "@/lib/utils";

interface FormState {
  type: string;
  display_name: string;
  tagline: string;
  avatar: AvatarId;
  color: PaletteId;
  description: string;
  model: string;
  customModel: string;
  tools: string[];
  skills: string[];
  system_prompt: string;
  /** "" = inherit the resolver default (sent as null to clear). */
  execution_mode: "" | "sdk" | "cli";
  auth_mode: "" | "api_key" | "oauth";
  /** JSON text of the mcp_servers object; validated on save. */
  mcpServersText: string;
}

/** The page's parts, top to bottom: the character it has become, then what it is configured to be. */
const SECTIONS = ["character", "identity", "model", "tools", "packs", "mcp", "prompt"] as const;
type SectionId = (typeof SECTIONS)[number];

/** A new agent opens every part to fill in; a saved one opens on its character, its configuration closed to a line each. */
function initialSections(isEdit: boolean): Record<SectionId, boolean> {
  return Object.fromEntries(SECTIONS.map((id) => [id, !isEdit || id === "character"])) as Record<SectionId, boolean>;
}

/** The MCP servers text in a line: how many, none, or not JSON yet. */
function mcpSummary(text: string): string {
  if (!text.trim()) return "None";
  try {
    const parsed: unknown = JSON.parse(text);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return "Not a JSON object";
    const names = Object.keys(parsed);
    return names.length ? names.join(", ") : "None";
  } catch {
    return "Not valid JSON";
  }
}

const DEFAULT_MODEL = "claude-sonnet-5";

const EMPTY_FORM: FormState = {
  type: "",
  display_name: "",
  tagline: "",
  avatar: DEFAULT_AVATAR,
  color: DEFAULT_COLOR,
  description: "",
  model: DEFAULT_MODEL,
  customModel: "",
  tools: [],
  skills: [],
  system_prompt: "",
  execution_mode: "",
  auth_mode: "",
  mcpServersText: "",
};

export function AgentFormPage() {
  const { type: typeParam } = useParams<{ type: string }>();
  const navigate = useNavigate();

  const isEdit = !!typeParam && typeParam !== "new";
  const { data: existing, isLoading } = useAgent(isEdit ? typeParam : null);
  const { models: knownModels, providers, isLoading: modelsLoading } = useModels();
  // The picker offers every tier and catalogue model, whatever credentials
  // this API process sees: they may be injected only where jobs run. A model
  // whose provider is not ready here is marked "(no credential)" and cannot be
  // picked anew; an agent already saved on one still opens on it.
  // Operator-disabled models stay out, as in useModels. Without the catalog
  // (older backend) the list stays as useModels built it.
  const offeredModels = useMemo(
    () =>
      providers.length === 0
        ? knownModels
        : [
            ...new Set([
              ...knownModels,
              ...providers.flatMap((p) => p.models.filter((m) => m.enabled !== false).map((m) => m.model)),
            ]),
          ],
    [knownModels, providers],
  );
  const notReadyModels = useMemo(
    () =>
      new Set(
        providers.length === 0
          ? []
          : offeredModels.filter((m) => providerForModel(m, providers)?.available !== true),
      ),
    [offeredModels, providers],
  );

  const { data: toolCatalog } = useTools();
  const { data: skillCatalog, isLoading: skillsLoading } = useSkills();

  const createAgent = useCreateAgent();
  const updateAgent = useUpdateAgent();

  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [sections, setSections] = useState(() => initialSections(isEdit));
  const openSection = (id: SectionId) => setSections((prev) => ({ ...prev, [id]: true }));
  const setAllSections = (open: boolean) =>
    setSections(Object.fromEntries(SECTIONS.map((id) => [id, open])) as Record<SectionId, boolean>);

  // What the agent has become (progression spec 2026-10-24): its level, total level and skills.
  const progression = useProgression();
  const character = isEdit ? progression.data?.characters.find((c) => c.agent_type === typeParam) : undefined;

  // Populate form when editing an existing agent
  useEffect(() => {
    if (!existing) return;
    const isCustomModel = !offeredModels.includes(existing.model);
    const mcp = existing.mcp_servers;
    // Avatar and colour through identityOf so a backend predating identity
    // still yields valid ids; the text fields as stored, so an empty display
    // name keeps showing the slug after a save.
    const identity = identityOf(existing);
    setForm({
      type: existing.type,
      display_name: existing.display_name ?? "",
      tagline: existing.tagline ?? "",
      avatar: identity.avatar,
      color: identity.color,
      description: existing.description ?? "",
      model: isCustomModel ? "__custom__" : existing.model,
      customModel: isCustomModel ? existing.model : "",
      tools: existing.tools ?? [],
      skills: existing.skills ?? [],
      system_prompt: existing.system_prompt ?? "",
      execution_mode: existing.execution_mode ?? "",
      auth_mode: existing.auth_mode ?? "",
      mcpServersText: mcp && Object.keys(mcp).length > 0 ? JSON.stringify(mcp, null, 2) : "",
    });
  }, [existing, offeredModels]);

  // A new agent starts on DEFAULT_MODEL. When the picker does not offer it as
  // a choice, start on the first model it does, so the select shows what will
  // be saved; Custom... when none is ready.
  useEffect(() => {
    if (isEdit || modelsLoading) return;
    const selectable = (m: string) => offeredModels.includes(m) && !notReadyModels.has(m);
    setForm((prev) =>
      prev.model === "__custom__" || selectable(prev.model)
        ? prev
        : { ...prev, model: offeredModels.find(selectable) ?? "__custom__" },
    );
  }, [isEdit, modelsLoading, offeredModels, notReadyModels]);

  const resolvedModel = form.model === "__custom__" ? form.customModel : form.model;

  // Which provider serves the chosen model and whether it can run it now: from
  // the catalog by id prefix, as the backend routes, so it follows the picker
  // and the custom id. Without the catalog, the saved model's answer from
  // GET /agents/{type}.
  const chosenProvider: { name: string | null; ready: boolean } | null = (() => {
    if (!resolvedModel.trim()) return null;
    if (providers.length > 0) {
      const provider = providerForModel(resolvedModel, providers);
      return { name: provider?.name ?? null, ready: provider?.available === true };
    }
    if (isEdit && existing && resolvedModel === existing.model && existing.provider_ready !== undefined) {
      return { name: existing.provider ?? null, ready: existing.provider_ready };
    }
    return null;
  })();

  // When jobs will not run the model being edited: the default model or a
  // per-job tier replaces it (GET /agents/{type}, effective_model).
  const runNote = isEdit ? runOverrideNote(existing, resolvedModel) : null;

  const handleToolToggle = (tool: string) => {
    setForm((prev) => ({
      ...prev,
      tools: prev.tools.includes(tool)
        ? prev.tools.filter((t) => t !== tool)
        : [...prev.tools, tool],
    }));
  };

  const handleSave = () => {
    if (!isEdit && !form.type.trim()) {
      openSection("identity");
      toast.error("Agent type is required");
      return;
    }
    // A definition no job runs keeps naming no model: the brief writer's voices (runs_in_jobs false),
    // whose briefs run on the model background_models names for them.
    const namesNoModel = isEdit && existing !== undefined && existing.runs_in_jobs === false && !existing.model;
    if (!resolvedModel.trim() && !namesNoModel) {
      openSection("model");
      toast.error("Model is required");
      return;
    }

    // mcp_servers rides as JSON — validate before submitting. Empty text
    // means "no MCP servers" ({}; null on update clears the column).
    let mcpServers: Record<string, unknown> = {};
    if (form.mcpServersText.trim()) {
      try {
        const parsed: unknown = JSON.parse(form.mcpServersText);
        if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
          openSection("mcp");
          toast.error("MCP servers must be a JSON object mapping name → config");
          return;
        }
        mcpServers = parsed as Record<string, unknown>;
      } catch {
        openSection("mcp");
        toast.error("MCP servers is not valid JSON");
        return;
      }
    }

    const identity = {
      display_name: form.display_name,
      tagline: form.tagline,
      avatar: form.avatar,
      color: form.color,
    };
    const onSaved = (message: string) => (saved: AgentDefinition) => {
      saved.warnings?.forEach((w) => toast.warning(w));
      toast.success(message);
      navigate("/agents");
    };

    if (isEdit) {
      // PUT semantics: explicit null clears (execution/auth mode "inherit").
      const update: AgentUpdateRequest & { type: string } = {
        type: typeParam!,
        ...identity,
        description: form.description,
        model: resolvedModel,
        tools: form.tools,
        skills: form.skills,
        system_prompt: form.system_prompt,
        execution_mode: form.execution_mode || null,
        auth_mode: form.auth_mode || null,
        mcp_servers: mcpServers,
      };
      updateAgent.mutate(update, {
        onSuccess: onSaved("Agent updated"),
        onError: (e) => toastApiError(e, "Failed"),
      });
    } else {
      const create: AgentCreateRequest = {
        type: form.type.trim(),
        ...identity,
        description: form.description,
        model: resolvedModel,
        tools: form.tools,
        skills: form.skills,
        system_prompt: form.system_prompt,
        ...(form.execution_mode ? { execution_mode: form.execution_mode } : {}),
        ...(form.auth_mode ? { auth_mode: form.auth_mode } : {}),
        ...(Object.keys(mcpServers).length > 0 ? { mcp_servers: mcpServers } : {}),
      };
      createAgent.mutate(create, {
        onSuccess: onSaved("Agent created"),
        onError: (e) => toastApiError(e, "Failed"),
      });
    }
  };

  const orphanSkills = existing?.orphan_skills ?? [];
  const isPending = createAgent.isPending || updateAgent.isPending;

  if (isEdit && isLoading) {
    return <div className="text-white/40">Loading...</div>;
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Link
            to="/agents"
            aria-label="Back to agents"
            className={cn(buttonVariants({ variant: "ghost", size: "icon" }))}
          >
            <ArrowLeft className="h-4 w-4" />
          </Link>
          {(() => {
            const op = agentTypeToOperation(typeParam);
            return op ? (
              <span className="flex items-center justify-center w-9 h-9 shrink-0">
                <OperationGlyph operation={op} size={36} titled />
              </span>
            ) : null;
          })()}
          <div className="flex flex-col">
            <h1 className="title-page text-xl font-bold leading-tight">
              {isEdit ? `Edit agent: ${typeParam}` : "New agent"}
            </h1>
            {(() => {
              const op = agentTypeToOperation(typeParam);
              if (!op) return null;
              return (
                <span className="text-[10px] uppercase tracking-[0.18em] text-fg-muted font-mono mt-0.5">
                  Op {OPERATION_NUMERAL[op]} · {op} · {OPERATION_ELEMENT[op]}
                </span>
              );
            })()}
          </div>
        </div>
        <div className="flex gap-2">
          <Link to="/agents">
            <Button variant="outline">Cancel</Button>
          </Link>
          <AdminButton onClick={handleSave} disabled={isPending}>
            {isPending ? "Saving..." : "Save Agent"}
          </AdminButton>
        </div>
      </div>

      {/* Every part closes to a line saying what it holds; a save error opens the part it names. */}
      <div className="flex justify-end gap-2 text-xs">
        <Button variant="ghost" size="sm" onClick={() => setAllSections(true)}>
          Expand all
        </Button>
        <Button variant="ghost" size="sm" onClick={() => setAllSections(false)}>
          Collapse all
        </Button>
      </div>

      {character && progression.data && (
        <ConfigSection
          id="character"
          title="Character"
          summary={`Level ${character.level} · total level ${character.total_level.toLocaleString("en-US")} · ${label(gamePackOf(progression.data), "gear", { tier: tierName(gamePackOf(progression.data), character.tier) })}`}
          open={sections.character}
          onToggle={(open) => setSections((prev) => ({ ...prev, character: open }))}
        >
          <SkillSheet character={character} skills={progression.data.skills} identity={identityOf({ ...form, type: typeParam ?? "" })} pack={gamePackOf(progression.data)} />
        </ConfigSection>
      )}

      <ConfigSection
        id="identity"
        title="Identity"
        summary={`${form.display_name.trim() || form.type || "New agent"} · ${form.avatar} · ${form.color}`}
        open={sections.identity}
        onToggle={(open) => setSections((prev) => ({ ...prev, identity: open }))}
      >
        <div className="space-y-4">
          <div>
            <Label htmlFor="agent-type">Type (slug)</Label>
            <Input
              id="agent-type"
              placeholder="e.g. code-reviewer"
              value={form.type}
              onChange={(e) => setForm((prev) => ({ ...prev, type: e.target.value }))}
              disabled={isEdit}
              className="mt-1 font-mono"
            />
            {isEdit && (
              <p className="text-xs text-white/30 mt-1">Agent type cannot be changed after creation.</p>
            )}
          </div>
          <AgentIdentityFields
            type={form.type}
            value={form}
            onChange={(patch) => setForm((prev) => ({ ...prev, ...patch }))}
          />
          <div>
            <Label htmlFor="agent-description">Description</Label>
            <Textarea
              id="agent-description"
              placeholder="Describe what this agent does..."
              value={form.description}
              onChange={(e) => setForm((prev) => ({ ...prev, description: e.target.value }))}
              className="mt-1 min-h-[80px] bg-overlay border-white/[0.08]"
            />
          </div>
        </div>
      </ConfigSection>

      <ConfigSection
        id="model"
        title="Model and runtime"
        summary={[resolvedModel || "No model", form.execution_mode || "inherited execution", form.auth_mode || "inherited auth"].join(" · ")}
        open={sections.model}
        onToggle={(open) => setSections((prev) => ({ ...prev, model: open }))}
      >
        <div className="space-y-5">
          {/* Model */}
          <div>
            <Label htmlFor="agent-model">Model</Label>
            <ModelSelect
              id="agent-model"
              value={form.model}
              onChange={(e) => setForm((prev) => ({ ...prev, model: e.target.value }))}
              className="mt-1"
              disabled={modelsLoading}
              models={offeredModels}
              providers={providers}
              labelOf={(m) => (notReadyModels.has(m) ? `${m} (no credential)` : m)}
              disabledOf={(m) => notReadyModels.has(m) && m !== existing?.model}
              trailing={<option value="__custom__">Custom...</option>}
            />
            {form.model === "__custom__" && (
              <Input
                className="mt-2 font-mono"
                placeholder="Enter model ID"
                value={form.customModel}
                onChange={(e) => setForm((prev) => ({ ...prev, customModel: e.target.value }))}
              />
            )}
            {chosenProvider && (
              <p
                className="mt-1 flex items-center gap-2 text-xs text-white/40"
                data-testid="model-provider-readiness"
              >
                {chosenProvider.name ? (
                  <span>
                    Provider: {providerLabel(chosenProvider.name)}
                  </span>
                ) : (
                  <span>No configured provider serves this model</span>
                )}
                {chosenProvider.ready ? (
                  <Badge tone="success">Ready</Badge>
                ) : (
                  <Badge
                    tone="error"
                    title={
                      chosenProvider.name
                        ? "No credential is configured for this provider"
                        : "No configured provider serves this model"
                    }
                  >
                    Not ready
                  </Badge>
                )}
              </p>
            )}
            {runNote && (
              <p className="mt-1 text-xs text-warning" data-testid="model-run-override">
                {runNote}
              </p>
            )}
          </div>

          {/* Execution and auth mode */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <Label htmlFor="agent-execution-mode">Execution mode</Label>
              <Select
                id="agent-execution-mode"
                value={form.execution_mode}
                onChange={(e) =>
                  setForm((prev) => ({ ...prev, execution_mode: e.target.value as FormState["execution_mode"] }))
                }
                className="mt-1"
              >
                <option value="">Inherit default</option>
                <option value="sdk">sdk — Agent SDK executor</option>
                <option value="cli">cli — Claude CLI subprocess</option>
              </Select>
            </div>
            <div>
              <Label htmlFor="agent-auth-mode">Auth mode</Label>
              <Select
                id="agent-auth-mode"
                value={form.auth_mode}
                onChange={(e) =>
                  setForm((prev) => ({ ...prev, auth_mode: e.target.value as FormState["auth_mode"] }))
                }
                className="mt-1"
              >
                <option value="">Inherit default</option>
                <option value="api_key">api_key — Anthropic API key</option>
                <option value="oauth">oauth — Claude Code OAuth token</option>
              </Select>
            </div>
          </div>
        </div>
      </ConfigSection>

      <ConfigSection
        id="tools"
        title="Tools"
        summary={form.tools.length ? `${form.tools.length} tools: ${form.tools.join(", ")}` : "No tools"}
        open={sections.tools}
        onToggle={(open) => setSections((prev) => ({ ...prev, tools: open }))}
      >
        {/* Tools */}
        <div>
          <Label className="sr-only">Tools</Label>
          <ToolChecklist value={form.tools} onToggle={handleToolToggle} catalog={toolCatalog ?? []} />
        </div>
      </ConfigSection>

      <ConfigSection
        id="packs"
        title="Skill packs"
        summary={form.skills.length ? form.skills.join(", ") : "None"}
        open={sections.packs}
        onToggle={(open) => setSections((prev) => ({ ...prev, packs: open }))}
      >
        {/* Skills */}
        <div>
          <Label htmlFor="agent-skills">Skill packs</Label>
          <div className="mt-1">
            <SkillPicker
              id="agent-skills"
              value={form.skills}
              onChange={(skills) => setForm((prev) => ({ ...prev, skills }))}
              catalog={skillCatalog ?? []}
              isLoading={skillsLoading}
            />
          </div>
          {isEdit && orphanSkills.length > 0 && (
            <p className="text-xs text-amber-300/80 mt-1">
              No source lists: {orphanSkills.join(", ")}. They keep running; adding a new unknown name is refused.
            </p>
          )}
        </div>
      </ConfigSection>

      <ConfigSection
        id="mcp"
        title="MCP servers"
        summary={mcpSummary(form.mcpServersText)}
        open={sections.mcp}
        onToggle={(open) => setSections((prev) => ({ ...prev, mcp: open }))}
      >
        
        <div>
          <Label htmlFor="agent-mcp-servers">MCP servers (JSON)</Label>
          <Textarea
            id="agent-mcp-servers"
            placeholder={'{\n  "my-server": { "type": "stdio", "command": "my-mcp", "args": ["--flag"] }\n}'}
            value={form.mcpServersText}
            onChange={(e) => setForm((prev) => ({ ...prev, mcpServersText: e.target.value }))}
            className="mt-1 font-mono text-sm min-h-[120px] bg-overlay border-white/[0.08]"
          />
          <p className="text-xs text-white/30 mt-1">
            Object mapping server name → config, injected into the sandbox agent. Leave empty for none. The QA
            agent's browser server (Playwright or Vibium) is selected by the repo's qa.yaml{' '}
            <span className="font-mono">browser_backend</span> field at dispatch — not edited here.
          </p>
        </div>
      </ConfigSection>

      <ConfigSection
        id="prompt"
        title="Prompt"
        summary={form.system_prompt.trim() ? `${form.system_prompt.length.toLocaleString("en-US")} characters` : "Empty"}
        open={sections.prompt}
        onToggle={(open) => setSections((prev) => ({ ...prev, prompt: open }))}
      >
        <div>
          <Label htmlFor="agent-system-prompt">System prompt</Label>
          <Textarea
            id="agent-system-prompt"
            placeholder="Enter the system prompt for this agent..."
            value={form.system_prompt}
            onChange={(e) => setForm((prev) => ({ ...prev, system_prompt: e.target.value }))}
            className="mt-1 font-mono text-sm min-h-[160px] bg-overlay border-white/[0.08]"
          />
        </div>
      </ConfigSection>
    </div>
  );
}
