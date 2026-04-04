import { useMemo, useState } from "react";
import type { Node } from "@xyflow/react";
import { X, ChevronDown, ChevronRight } from "lucide-react";
import { useAgentTypes } from "@/hooks/useAgentTypes";
import { getAgentColor, getAgentCategory } from "@/lib/graph-utils";
import { cn } from "@/lib/utils";
import type { SkillConfig } from "@/lib/types";
import { SANDBOX_IMAGE_PLACEHOLDER } from "@/lib/branding";

interface NodeInspectorProps {
  node: Node;
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

function FieldTextarea({
  id,
  ...props
}: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      id={id}
      {...props}
      className={cn(
        "w-full bg-white/[0.04] border border-white/[0.08] rounded-md px-2.5 py-1.5 text-xs text-white/80 outline-none focus:border-white/20 transition-colors font-mono resize-none min-h-[64px]",
        props.className,
      )}
    />
  );
}

interface TagPillProps {
  label: string;
  onRemove: () => void;
}

function TagPill({ label, onRemove }: TagPillProps) {
  return (
    <span className="inline-flex items-center gap-1 bg-white/[0.06] border border-white/[0.08] rounded-full px-2 py-0.5 text-[11px] text-white/60 group">
      {label}
      <button
        type="button"
        onClick={onRemove}
        className="text-white/20 hover:text-white/60 transition-colors ml-0.5"
        aria-label={`Remove ${label}`}
      >
        <X size={10} />
      </button>
    </span>
  );
}

interface TagInputProps {
  tags: string[];
  onChange: (tags: string[]) => void;
  placeholder?: string;
}

function TagInput({ tags, onChange, placeholder }: TagInputProps) {
  const [input, setInput] = useState("");

  const addTag = (value: string) => {
    const trimmed = value.trim();
    if (trimmed && !tags.includes(trimmed)) {
      onChange([...tags, trimmed]);
    }
    setInput("");
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      addTag(input);
    } else if (e.key === "Backspace" && !input && tags.length > 0) {
      onChange(tags.slice(0, -1));
    }
  };

  return (
    <div className="flex flex-wrap gap-1.5 bg-white/[0.04] border border-white/[0.08] rounded-md px-2.5 py-1.5 focus-within:border-white/20 transition-colors min-h-[36px]">
      {tags.map((tag) => (
        <TagPill
          key={tag}
          label={tag}
          onRemove={() => onChange(tags.filter((t) => t !== tag))}
        />
      ))}
      <input
        value={input}
        onChange={(e) => setInput(e.target.value)}
        onKeyDown={handleKeyDown}
        onBlur={() => { if (input) addTag(input); }}
        placeholder={tags.length === 0 ? placeholder : ""}
        className="flex-1 min-w-[80px] bg-transparent text-xs text-white/80 outline-none placeholder:text-white/20"
      />
    </div>
  );
}

function SkillAddInput({ onAdd }: { onAdd: (name: string) => void }) {
  const [input, setInput] = useState("");
  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      if (input.trim()) { onAdd(input); setInput(""); }
    }
  };
  return (
    <input
      value={input}
      onChange={(e) => setInput(e.target.value)}
      onKeyDown={handleKeyDown}
      onBlur={() => { if (input.trim()) { onAdd(input); setInput(""); } }}
      placeholder="Add skill..."
      className="mt-1.5 w-full bg-white/[0.04] border border-white/[0.08] rounded-md px-2.5 py-1.5 text-xs text-white/80 outline-none focus:border-white/20 transition-colors placeholder:text-white/20"
    />
  );
}

export function NodeInspector({ node, onUpdate }: NodeInspectorProps) {
  const d = node.data as Record<string, unknown>;
  const { data: agents } = useAgentTypes();
  const [advancedOpen, setAdvancedOpen] = useState(false);

  const models = useMemo(
    () => [...new Set(agents?.map((a) => a.model) ?? [])],
    [agents],
  );

  const agentType = (d.agentType as string) ?? "custom";
  const color = getAgentColor(agentType);
  const category = getAgentCategory(agentType);

  const tools = Array.isArray(d.tools) ? (d.tools as string[]) : [];

  const skillConfigs: SkillConfig[] = useMemo(() => {
    if (!Array.isArray(d.skills)) return [];
    return (d.skills as (string | SkillConfig)[]).map((s) =>
      typeof s === "string" ? { name: s, mode: "autonomous" as const } : s,
    );
  }, [d.skills]);
  const skillNames = skillConfigs.map((s) => s.name);
  const updateSkillMode = (index: number, mode: SkillConfig["mode"]) => {
    const updated = [...skillConfigs];
    updated[index] = { ...updated[index], mode };
    onUpdate({ skills: updated.length ? updated : null });
  };
  const addSkill = (name: string) => {
    const trimmed = name.trim();
    if (!trimmed || skillNames.includes(trimmed)) return;
    onUpdate({ skills: [...skillConfigs, { name: trimmed, mode: "autonomous" as const }] });
  };
  const removeSkill = (index: number) => {
    const updated = skillConfigs.filter((_, i) => i !== index);
    onUpdate({ skills: updated.length ? updated : null });
  };

  return (
    <div className="p-4 overflow-y-auto">
      {/* Header: colored badge + editable label */}
      <div className="flex items-start gap-2 mb-1">
        <span
          className="shrink-0 mt-0.5 px-2 py-0.5 rounded-full text-[10px] font-semibold uppercase tracking-wider"
          style={{ backgroundColor: `${color}20`, color: `${color}CC` }}
        >
          {category}
        </span>
      </div>
      <input
        value={(d.label as string) ?? ""}
        onChange={(e) => onUpdate({ label: e.target.value })}
        className="w-full bg-transparent text-base font-semibold text-white/90 outline-none border-b border-transparent focus:border-white/20 pb-0.5 transition-colors placeholder:text-white/25"
        placeholder="Node label…"
        aria-label="Node label"
      />

      <Divider />

      {/* Execution section */}
      <SectionHeader>Execution</SectionHeader>

      <div className="space-y-3">
        <div>
          <FieldLabel>Model</FieldLabel>
          <FieldSelect
            id="ni-model"
            value={(d.model as string) ?? ""}
            onChange={(e) => onUpdate({ model: e.target.value || null })}
          >
            <option value="">Default (from registry)</option>
            {models.map((m) => (
              <option key={m} value={m}>{m}</option>
            ))}
          </FieldSelect>
        </div>

        <div className="grid grid-cols-2 gap-2">
          <div>
            <FieldLabel>Budget (USD)</FieldLabel>
            <FieldInput
              id="ni-budget"
              type="number"
              step="0.5"
              min="0"
              value={(d.maxBudgetUsd as number) ?? ""}
              onChange={(e) =>
                onUpdate({
                  maxBudgetUsd: e.target.value ? Number(e.target.value) : null,
                })
              }
              placeholder="—"
            />
          </div>
          <div>
            <FieldLabel>Max Turns</FieldLabel>
            <FieldInput
              id="ni-turns"
              type="number"
              min="1"
              value={(d.maxTurns as number) ?? ""}
              onChange={(e) =>
                onUpdate({
                  maxTurns: e.target.value ? Number(e.target.value) : null,
                })
              }
              placeholder="—"
            />
          </div>
        </div>

        <div>
          <FieldLabel>Effort</FieldLabel>
          <FieldSelect
            id="ni-effort"
            value={(d.effort as string) ?? ""}
            onChange={(e) => onUpdate({ effort: e.target.value || null })}
          >
            <option value="">Default</option>
            <option value="low">Low</option>
            <option value="medium">Medium</option>
            <option value="high">High</option>
          </FieldSelect>
        </div>
      </div>

      <Divider />

      {/* Tools & Skills section */}
      <SectionHeader>Tools &amp; Skills</SectionHeader>

      <div className="space-y-3">
        <div>
          <FieldLabel>Tools</FieldLabel>
          <TagInput
            tags={tools}
            onChange={(t) => onUpdate({ tools: t.length ? t : null })}
            placeholder="Read, Write, Bash…"
          />
        </div>
        <div>
          <FieldLabel>Skills</FieldLabel>
          <div className="space-y-1.5">
            {skillConfigs.map((skill, idx) => (
              <div key={skill.name} className="flex items-center gap-1.5">
                <TagPill label={skill.name} onRemove={() => removeSkill(idx)} />
                <FieldSelect
                  id={`ni-skill-mode-${idx}`}
                  value={skill.mode}
                  onChange={(e) => updateSkillMode(idx, e.target.value as SkillConfig["mode"])}
                  className="!w-auto !px-1.5 !py-0.5 !text-[10px]"
                >
                  <option value="autonomous">Autonomous</option>
                  <option value="guided">Guided</option>
                  <option value="interactive">Interactive</option>
                </FieldSelect>
              </div>
            ))}
          </div>
          <SkillAddInput onAdd={addSkill} />
        </div>
      </div>

      <Divider />

      {/* Prompt section */}
      <SectionHeader>Prompt</SectionHeader>

      <div className="space-y-3">
        <div>
          <FieldLabel>Prepend</FieldLabel>
          <FieldTextarea
            id="ni-prepend"
            value={(d.promptPrepend as string) ?? ""}
            onChange={(e) =>
              onUpdate({ promptPrepend: e.target.value || null })
            }
            placeholder="Instructions before base prompt…"
          />
        </div>
        <div>
          <FieldLabel>Append</FieldLabel>
          <FieldTextarea
            id="ni-append"
            value={(d.promptAppend as string) ?? ""}
            onChange={(e) =>
              onUpdate({ promptAppend: e.target.value || null })
            }
            placeholder="Instructions after base prompt…"
          />
        </div>
      </div>

      <Divider />

      {/* Advanced section — collapsible */}
      <button
        type="button"
        onClick={() => setAdvancedOpen((v) => !v)}
        className="flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-white/30 font-semibold hover:text-white/50 transition-colors"
      >
        {advancedOpen ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        Advanced
      </button>

      {advancedOpen && (
        <div className="mt-2 space-y-3">
          <div>
            <FieldLabel>Sandbox Image</FieldLabel>
            <FieldInput
              id="ni-sandbox"
              value={
                (d.sandbox as Record<string, unknown> | null)?.image as string ?? ""
              }
              onChange={(e) =>
                onUpdate({
                  sandbox: e.target.value ? { image: e.target.value } : null,
                })
              }
              placeholder={SANDBOX_IMAGE_PLACEHOLDER}
            />
          </div>
        </div>
      )}
    </div>
  );
}
