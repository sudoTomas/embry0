import { useMemo, useState, useEffect, useRef } from "react";
import type { Node } from "@xyflow/react";
import { X, ChevronDown, ChevronRight } from "lucide-react";
import { useAgents } from "@/hooks/useAgents";
import { getAgentColor, getAgentCategory } from "@/lib/graph-utils";
import { getAgentIcon } from "@/lib/agentIcons";
import { cn } from "@/lib/utils";
import type { SkillConfig } from "@/lib/types";
import { SANDBOX_IMAGE_PLACEHOLDER } from "@/lib/branding";
import { createFocusTrap } from "@/lib/focus-trap";

/* ------------------------------------------------------------------ */
/*  Props                                                              */
/* ------------------------------------------------------------------ */

interface AgentDetailPopupProps {
  node: Node;
  onUpdate: (data: Record<string, unknown>) => void;
  onClose: () => void;
}

/* ------------------------------------------------------------------ */
/*  Shared tiny helpers (extracted from NodeInspector pattern)          */
/* ------------------------------------------------------------------ */

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
        onBlur={() => {
          if (input) addTag(input);
        }}
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
      if (input.trim()) {
        onAdd(input);
        setInput("");
      }
    }
  };
  return (
    <input
      value={input}
      onChange={(e) => setInput(e.target.value)}
      onKeyDown={handleKeyDown}
      onBlur={() => {
        if (input.trim()) {
          onAdd(input);
          setInput("");
        }
      }}
      placeholder="Add skill..."
      className="mt-1.5 w-full bg-white/[0.04] border border-white/[0.08] rounded-md px-2.5 py-1.5 text-xs text-white/80 outline-none focus:border-white/20 transition-colors placeholder:text-white/20"
    />
  );
}

/* ------------------------------------------------------------------ */
/*  Read-mode pill for tools / skills                                  */
/* ------------------------------------------------------------------ */

function ReadPill({
  label,
  className,
}: {
  label: string;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-block font-mono text-[11px] px-2 py-0.5 rounded-full",
        className,
      )}
    >
      {label}
    </span>
  );
}

/* ------------------------------------------------------------------ */
/*  Main component                                                     */
/* ------------------------------------------------------------------ */

export function AgentDetailPopup({
  node,
  onUpdate,
  onClose,
}: AgentDetailPopupProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const d = node.data as Record<string, unknown>;
  const { data: agents } = useAgents();
  const [editing, setEditing] = useState(false);
  const [advancedOpen, setAdvancedOpen] = useState(false);

  useEffect(() => {
    if (!containerRef.current) return;
    document.body.style.overflow = "hidden";
    const cleanup = createFocusTrap(containerRef.current);
    return () => {
      cleanup();
      document.body.style.overflow = "";
    };
  }, []);

  /* ---- derived data ---- */
  const agentType = (d.agentType as string) ?? "custom";
  const color = getAgentColor(agentType);
  const category = getAgentCategory(agentType);
  const Icon = getAgentIcon(agentType);

  const agentDef = useMemo(
    () => agents?.find((a) => a.type === agentType),
    [agents, agentType],
  );

  const models = useMemo(
    () => [...new Set(agents?.map((a) => a.model) ?? [])],
    [agents],
  );

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
    onUpdate({
      skills: [...skillConfigs, { name: trimmed, mode: "autonomous" as const }],
    });
  };
  const removeSkill = (index: number) => {
    const updated = skillConfigs.filter((_, i) => i !== index);
    onUpdate({ skills: updated.length ? updated : null });
  };

  /* ---- prompt preview text ---- */
  const promptPreview = (() => {
    const raw = (d.promptPrepend as string) ?? "";
    if (!raw) return "Default system prompt";
    return raw.length > 80 ? `${raw.slice(0, 80)}...` : raw;
  })();

  const displayModel =
    (d.model as string) ?? agentDef?.model ?? "default";
  const displayLabel =
    (d.label as string) || agentType.charAt(0).toUpperCase() + agentType.slice(1);

  /* ================================================================ */
  /*  Render                                                           */
  /* ================================================================ */

  return (
    <div
      className="absolute inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        ref={containerRef}
        role="dialog"
        aria-modal="true"
        className="w-[560px] max-h-[85vh] overflow-y-auto rounded-2xl"
        onClick={(e) => e.stopPropagation()}
        style={{
          border: `1px solid ${color}33`,
          background: `linear-gradient(170deg, ${color}0A 0%, #0f1419 15%, #0f1419 100%)`,
          boxShadow: `0 0 60px ${color}10, 0 25px 50px rgba(0,0,0,0.5)`,
          animation: "popup-in 0.25s ease-out",
        }}
      >
        {/* -------- Header -------- */}
        <div className="flex items-center gap-3 px-5 py-4 border-b border-white/[0.06]">
          {/* Icon box */}
          <div
            className="shrink-0 w-[44px] h-[44px] rounded-xl flex items-center justify-center"
            style={{
              backgroundColor: `${color}26`,
              border: `1px solid ${color}4D`,
            }}
          >
            <Icon className="w-5 h-5" style={{ color }} />
          </div>

          {/* Title */}
          <div className="flex-1 min-w-0">
            <div className="text-[22px] font-bold leading-tight" style={{ color }}>
              {displayLabel}
            </div>
            <div className="text-[13px] text-white/40 mt-0.5">
              {editing ? "Editing" : category}
            </div>
          </div>

          {/* Close */}
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="shrink-0 w-8 h-8 flex items-center justify-center rounded-lg bg-white/[0.04] border border-white/[0.08] text-white/40 hover:text-white/80 transition-colors"
          >
            <X size={16} />
          </button>
        </div>

        {/* -------- Body -------- */}
        <div className="p-5">
          {editing ? (
            <EditMode
              d={d}
              color={color}
              models={models}
              tools={tools}
              skillConfigs={skillConfigs}
              onUpdate={onUpdate}
              updateSkillMode={updateSkillMode}
              addSkill={addSkill}
              removeSkill={removeSkill}
              advancedOpen={advancedOpen}
              setAdvancedOpen={setAdvancedOpen}
              onCancel={() => setEditing(false)}
            />
          ) : (
            <ReadMode
              color={color}
              description={agentDef?.description}
              displayModel={displayModel}
              promptPreview={promptPreview}
              tools={tools}
              skillNames={skillNames}
              onEdit={() => setEditing(true)}
            />
          )}
        </div>

        {/* -------- Footer -------- */}
        <div className="pb-4 text-center text-[12px] text-white/20">
          Click anywhere outside to close
        </div>
      </div>
    </div>
  );
}

/* ================================================================== */
/*  Read mode                                                          */
/* ================================================================== */

function ReadMode({
  color,
  description,
  displayModel,
  promptPreview,
  tools,
  skillNames,
  onEdit,
}: {
  color: string;
  description?: string;
  displayModel: string;
  promptPreview: string;
  tools: string[];
  skillNames: string[];
  onEdit: () => void;
}) {
  return (
    <>
      {/* Description */}
      {description && (
        <p className="text-[14px] text-white/60 leading-relaxed mb-4">
          {description}
        </p>
      )}

      {/* Model badge */}
      <div
        className="inline-flex items-center gap-2 px-3 py-2 rounded-lg mb-4"
        style={{
          backgroundColor: `${color}0A`,
          border: `1px solid ${color}20`,
        }}
      >
        <span style={{ color }} className="text-sm">
          &#10022;
        </span>
        <div>
          <div className="text-[11px] text-white/40 leading-none">Model</div>
          <div className="text-[14px] font-bold leading-tight" style={{ color }}>
            {displayModel}
          </div>
        </div>
      </div>

      {/* System prompt preview */}
      <div className="rounded-lg bg-white/[0.03] border border-white/[0.06] px-3 py-2.5 mb-4">
        <p className="text-[13px] italic text-white/35 leading-relaxed">
          {promptPreview}
        </p>
      </div>

      {/* Tools & Skills side-by-side */}
      <div className="flex gap-3 mb-5">
        {/* Tools card */}
        <div className="flex-1 rounded-lg border border-cyan-500/15 bg-cyan-500/[0.02] px-3 py-2.5">
          <div className="text-[11px] font-semibold text-cyan-400 mb-1.5">
            &#9889; Tools
          </div>
          <div className="flex flex-wrap gap-1">
            {tools.length > 0 ? (
              tools.map((t) => (
                <ReadPill
                  key={t}
                  label={t}
                  className="bg-cyan-500/10 text-cyan-400/80 border border-cyan-500/20"
                />
              ))
            ) : (
              <span className="text-[11px] text-white/20">None</span>
            )}
          </div>
        </div>

        {/* Skills card */}
        <div className="flex-1 rounded-lg border border-purple-500/15 bg-purple-500/[0.02] px-3 py-2.5">
          <div className="text-[11px] font-semibold text-purple-400 mb-1.5">
            &#10023; Skills
          </div>
          <div className="flex flex-wrap gap-1">
            {skillNames.length > 0 ? (
              skillNames.map((s) => (
                <ReadPill
                  key={s}
                  label={s}
                  className="bg-purple-500/10 text-purple-400/80 border border-purple-500/20"
                />
              ))
            ) : (
              <span className="text-[11px] text-white/20">None</span>
            )}
          </div>
        </div>
      </div>

      {/* Edit Configuration button */}
      <div className="text-center">
        <button
          type="button"
          onClick={onEdit}
          className="px-4 py-2 rounded-lg text-sm font-medium transition-colors"
          style={{
            backgroundColor: `${color}1A`,
            border: `1px solid ${color}40`,
            color,
          }}
        >
          Edit Configuration
        </button>
      </div>
    </>
  );
}

/* ================================================================== */
/*  Edit mode                                                          */
/* ================================================================== */

function EditMode({
  d,
  color,
  models,
  tools,
  skillConfigs,
  onUpdate,
  updateSkillMode,
  addSkill,
  removeSkill,
  advancedOpen,
  setAdvancedOpen,
  onCancel,
}: {
  d: Record<string, unknown>;
  color: string;
  models: string[];
  tools: string[];
  skillConfigs: SkillConfig[];
  onUpdate: (data: Record<string, unknown>) => void;
  updateSkillMode: (index: number, mode: SkillConfig["mode"]) => void;
  addSkill: (name: string) => void;
  removeSkill: (index: number) => void;
  advancedOpen: boolean;
  setAdvancedOpen: React.Dispatch<React.SetStateAction<boolean>>;
  onCancel: () => void;
}) {
  return (
    <>
      {/* Label */}
      <SectionHeader>Label</SectionHeader>
      <FieldInput
        id="adp-label"
        value={(d.label as string) ?? ""}
        onChange={(e) => onUpdate({ label: e.target.value })}
        placeholder="Node label..."
        aria-label="Node label"
      />

      <Divider />

      {/* Execution section */}
      <SectionHeader>Execution</SectionHeader>
      <div className="space-y-3">
        <div>
          <FieldLabel>Model</FieldLabel>
          <FieldSelect
            id="adp-model"
            value={(d.model as string) ?? ""}
            onChange={(e) => onUpdate({ model: e.target.value || null })}
          >
            <option value="">Default (from registry)</option>
            {models.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </FieldSelect>
        </div>

        <div className="grid grid-cols-2 gap-2">
          <div>
            <FieldLabel>Budget (USD)</FieldLabel>
            <FieldInput
              id="adp-budget"
              type="number"
              step="0.5"
              min="0"
              value={(d.maxBudgetUsd as number) ?? ""}
              onChange={(e) =>
                onUpdate({
                  maxBudgetUsd: e.target.value ? Number(e.target.value) : null,
                })
              }
              placeholder="--"
            />
          </div>
          <div>
            <FieldLabel>Max Turns</FieldLabel>
            <FieldInput
              id="adp-turns"
              type="number"
              min="1"
              value={(d.maxTurns as number) ?? ""}
              onChange={(e) =>
                onUpdate({
                  maxTurns: e.target.value ? Number(e.target.value) : null,
                })
              }
              placeholder="--"
            />
          </div>
        </div>

        <div>
          <FieldLabel>Effort</FieldLabel>
          <FieldSelect
            id="adp-effort"
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

      {/* Tools & Skills */}
      <SectionHeader>Tools &amp; Skills</SectionHeader>
      <div className="space-y-3">
        <div>
          <FieldLabel>Tools</FieldLabel>
          <TagInput
            tags={tools}
            onChange={(t) => onUpdate({ tools: t.length ? t : null })}
            placeholder="Read, Write, Bash..."
          />
        </div>
        <div>
          <FieldLabel>Skills</FieldLabel>
          <div className="space-y-1.5">
            {skillConfigs.map((skill, idx) => (
              <div key={skill.name} className="flex items-center gap-1.5">
                <TagPill label={skill.name} onRemove={() => removeSkill(idx)} />
                <FieldSelect
                  id={`adp-skill-mode-${idx}`}
                  value={skill.mode}
                  onChange={(e) =>
                    updateSkillMode(idx, e.target.value as SkillConfig["mode"])
                  }
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

      {/* Prompt */}
      <SectionHeader>Prompt</SectionHeader>
      <div className="space-y-3">
        <div>
          <FieldLabel>Prepend</FieldLabel>
          <FieldTextarea
            id="adp-prepend"
            value={(d.promptPrepend as string) ?? ""}
            onChange={(e) =>
              onUpdate({ promptPrepend: e.target.value || null })
            }
            placeholder="Instructions before base prompt..."
          />
        </div>
        <div>
          <FieldLabel>Append</FieldLabel>
          <FieldTextarea
            id="adp-append"
            value={(d.promptAppend as string) ?? ""}
            onChange={(e) =>
              onUpdate({ promptAppend: e.target.value || null })
            }
            placeholder="Instructions after base prompt..."
          />
        </div>
      </div>

      <Divider />

      {/* Advanced (collapsible) */}
      <button
        type="button"
        onClick={() => setAdvancedOpen((v) => !v)}
        className="flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-white/30 font-semibold hover:text-white/50 transition-colors"
      >
        {advancedOpen ? (
          <ChevronDown size={12} />
        ) : (
          <ChevronRight size={12} />
        )}
        Advanced
      </button>

      {advancedOpen && (
        <div className="mt-2 space-y-3">
          <div>
            <FieldLabel>Sandbox Image</FieldLabel>
            <FieldInput
              id="adp-sandbox"
              value={
                ((d.sandbox as Record<string, unknown> | null)?.image as string) ??
                ""
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

      <Divider />

      <div className="flex items-center justify-end gap-2 mt-1">
        <button
          type="button"
          onClick={onCancel}
          className="px-4 py-2 rounded-lg text-sm font-medium transition-colors"
          style={{
            backgroundColor: `${color}1A`,
            border: `1px solid ${color}40`,
            color,
          }}
        >
          Done
        </button>
      </div>
    </>
  );
}
