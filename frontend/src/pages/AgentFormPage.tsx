import { useState, useEffect } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { ArrowLeft } from "lucide-react";
import { toast } from "sonner";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { Select } from "@/components/ui/Select";
import { Textarea } from "@/components/ui/Textarea";
import { TagInput } from "@/components/ui/TagInput";
import { useAgent, useCreateAgent, useUpdateAgent } from "@/hooks/useAgents";
import { useModels } from "@/hooks/useModels";
import type { AgentCreateRequest, AgentUpdateRequest } from "@/lib/types/agents";

const AVAILABLE_TOOLS = ["Read", "Write", "Edit", "Bash", "Glob", "Grep"];

const SUGGESTED_SKILLS = [
  "superpowers:subagent-driven-development",
  "superpowers:verification-before-completion",
  "superpowers:test-driven-development",
];

interface FormState {
  type: string;
  description: string;
  model: string;
  customModel: string;
  tools: string[];
  skills: string[];
  system_prompt: string;
}

const DEFAULT_MODEL = "claude-sonnet-4-6";

const EMPTY_FORM: FormState = {
  type: "",
  description: "",
  model: DEFAULT_MODEL,
  customModel: "",
  tools: [],
  skills: [],
  system_prompt: "",
};

export function AgentFormPage() {
  const { type: typeParam } = useParams<{ type: string }>();
  const navigate = useNavigate();

  const isEdit = !!typeParam && typeParam !== "new";
  const { data: existing, isLoading } = useAgent(isEdit ? typeParam : null);
  const { models: knownModels, isLoading: modelsLoading } = useModels();

  const createAgent = useCreateAgent();
  const updateAgent = useUpdateAgent();

  const [form, setForm] = useState<FormState>(EMPTY_FORM);

  // Populate form when editing an existing agent
  useEffect(() => {
    if (!existing) return;
    const isCustomModel = !knownModels.includes(existing.model);
    setForm({
      type: existing.type,
      description: existing.description ?? "",
      model: isCustomModel ? "__custom__" : existing.model,
      customModel: isCustomModel ? existing.model : "",
      tools: existing.tools ?? [],
      skills: existing.skills ?? [],
      system_prompt: existing.system_prompt ?? "",
    });
  }, [existing, knownModels]);

  const resolvedModel = form.model === "__custom__" ? form.customModel : form.model;

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
      toast.error("Agent type is required");
      return;
    }
    if (!resolvedModel.trim()) {
      toast.error("Model is required");
      return;
    }

    if (isEdit) {
      const update: AgentUpdateRequest & { type: string } = {
        type: typeParam!,
        description: form.description,
        model: resolvedModel,
        tools: form.tools,
        skills: form.skills,
        system_prompt: form.system_prompt,
      };
      updateAgent.mutate(update, {
        onSuccess: () => {
          toast.success("Agent updated");
          navigate("/agents");
        },
        onError: (e) => toast.error(`Failed: ${e.message}`),
      });
    } else {
      const create: AgentCreateRequest = {
        type: form.type.trim(),
        description: form.description,
        model: resolvedModel,
        tools: form.tools,
        skills: form.skills,
        system_prompt: form.system_prompt,
      };
      createAgent.mutate(create, {
        onSuccess: () => {
          toast.success("Agent created");
          navigate("/agents");
        },
        onError: (e) => toast.error(`Failed: ${e.message}`),
      });
    }
  };

  const isPending = createAgent.isPending || updateAgent.isPending;

  if (isEdit && isLoading) {
    return <div className="text-white/40">Loading...</div>;
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Link to="/agents">
            <Button variant="ghost" size="icon">
              <ArrowLeft className="h-4 w-4" />
            </Button>
          </Link>
          <h1 className="text-2xl font-bold">
            {isEdit ? `Edit Agent: ${typeParam}` : "New Agent"}
          </h1>
        </div>
        <div className="flex gap-2">
          <Link to="/agents">
            <Button variant="outline">Cancel</Button>
          </Link>
          <Button onClick={handleSave} disabled={isPending}>
            {isPending ? "Saving..." : "Save Agent"}
          </Button>
        </div>
      </div>

      {/* Identity */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Identity</CardTitle>
        </CardHeader>
        <CardContent>
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
            <div>
              <Label htmlFor="agent-description">Description</Label>
              <Textarea
                id="agent-description"
                placeholder="Describe what this agent does..."
                value={form.description}
                onChange={(e) => setForm((prev) => ({ ...prev, description: e.target.value }))}
                className="mt-1 min-h-[80px] bg-[#0c1015] border-white/[0.08]"
              />
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Execution */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Execution</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-5">
            {/* Model */}
            <div>
              <Label htmlFor="agent-model">Model</Label>
              <Select
                id="agent-model"
                value={form.model}
                onChange={(e) => setForm((prev) => ({ ...prev, model: e.target.value }))}
                className="mt-1"
                disabled={modelsLoading}
              >
                {knownModels.map((m) => (
                  <option key={m} value={m}>{m}</option>
                ))}
                <option value="__custom__">Custom...</option>
              </Select>
              {form.model === "__custom__" && (
                <Input
                  className="mt-2 font-mono"
                  placeholder="Enter model ID"
                  value={form.customModel}
                  onChange={(e) => setForm((prev) => ({ ...prev, customModel: e.target.value }))}
                />
              )}
            </div>

            {/* Tools */}
            <div>
              <Label>Tools</Label>
              <div className="mt-2 grid grid-cols-2 sm:grid-cols-3 gap-2">
                {AVAILABLE_TOOLS.map((tool) => (
                  <label
                    key={tool}
                    className="flex items-center gap-2.5 px-3 py-2 rounded-md border border-white/[0.06] bg-white/[0.02] cursor-pointer hover:bg-white/[0.04] transition-colors"
                  >
                    <input
                      type="checkbox"
                      checked={form.tools.includes(tool)}
                      onChange={() => handleToolToggle(tool)}
                      className="w-4 h-4 rounded accent-cyan-500"
                    />
                    <span className="text-sm text-white/70 font-mono">{tool}</span>
                  </label>
                ))}
              </div>
            </div>

            {/* Skills */}
            <div>
              <Label>Skills</Label>
              <TagInput
                value={form.skills}
                onChange={(skills) => setForm((prev) => ({ ...prev, skills }))}
                placeholder="Add skill..."
                suggestions={SUGGESTED_SKILLS}
                className="mt-1"
              />
              <p className="text-xs text-white/30 mt-1">Press Enter to add. Type to filter suggestions.</p>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Prompt */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Prompt</CardTitle>
        </CardHeader>
        <CardContent>
          <div>
            <Label htmlFor="agent-system-prompt">System Prompt</Label>
            <Textarea
              id="agent-system-prompt"
              placeholder="Enter the system prompt for this agent..."
              value={form.system_prompt}
              onChange={(e) => setForm((prev) => ({ ...prev, system_prompt: e.target.value }))}
              className="mt-1 font-mono text-sm min-h-[160px] bg-[#0c1015] border-white/[0.08]"
            />
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
