import { useState, useEffect, useRef } from "react";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { Button } from "@/components/ui/Button";
import { X } from "lucide-react";
import type { EnvVar, EnvVarScope } from "@/lib/types/environment";
import { createFocusTrap } from "@/lib/focus-trap";
import { envErrorText } from "./envErrorText";

const QA_KEY_PATTERN = /^QA_[A-Z0-9_]+$/;

function validateQaKey(key: string): string | null {
  if (!QA_KEY_PATTERN.test(key)) {
    return `QA test credentials must use keys starting with "QA_" (uppercase letters, digits, underscores). Got: "${key}".`;
  }
  return null;
}

interface EnvVarModalProps {
  variable?: EnvVar | null; // null = add mode, populated = edit mode
  /**
   * Scope partition the modal is operating in. New variables are tagged with this value;
   * QA-section keys are validated against ^QA_[A-Z0-9_]+$ before submit.
   */
  envScope?: EnvVarScope;
  /** Rejects with the API error when the save is refused; the modal shows why. */
  onSave: (variable: EnvVar) => Promise<void>;
  onClose: () => void;
}

export function EnvVarModal({ variable, envScope = "app", onSave, onClose }: EnvVarModalProps) {
  const isEdit = !!variable;
  const containerRef = useRef<HTMLDivElement>(null);

  // For edits, prefer the variable's own scope; for adds, fall back to the section's scope.
  const effectiveScope: EnvVarScope = variable?.scope ?? envScope;

  const [key, setKey] = useState(variable?.key ?? "");
  const [value, setValue] = useState(variable?.var_type === "secret" ? "" : (variable?.value ?? ""));
  const [varType, setVarType] = useState<"config" | "secret">(variable?.var_type ?? "config");
  const [description, setDescription] = useState(variable?.description ?? "");
  const [required, setRequired] = useState(variable?.required ?? false);
  const [keyError, setKeyError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setKeyError(null);
    setKey(variable?.key ?? "");
    setValue(variable?.var_type === "secret" ? "" : (variable?.value ?? ""));
    setVarType(variable?.var_type ?? "config");
    setDescription(variable?.description ?? "");
    setRequired(variable?.required ?? false);
  }, [variable]);

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  useEffect(() => {
    if (!containerRef.current) return;
    document.body.style.overflow = "hidden";
    const cleanup = createFocusTrap(containerRef.current);
    return () => {
      cleanup();
      document.body.style.overflow = "";
    };
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!key.trim()) return;

    const trimmedKey = key.trim().toUpperCase();
    setKeyError(null);

    // Key problems belong under the Key field, not in a corner toast: the toast
    // lands on top of this dialog's own buttons on short viewports, and sonner
    // holds it there for as long as the pointer rests on it (RAV-1289).
    if (effectiveScope === "qa" && !isEdit) {
      const err = validateQaKey(trimmedKey);
      if (err) {
        setKeyError(err);
        return;
      }
    }

    const finalValue =
      isEdit && varType === "secret" && value === "" ? variable!.value : value;

    // The API's refusal (a reserved key, a value a key may not carry) lands
    // under the Key field like the checks above.
    setSaving(true);
    try {
      await onSave({
        key: trimmedKey,
        value: finalValue,
        var_type: varType,
        description: description.trim(),
        required,
        scope: effectiveScope,
      });
    } catch (err) {
      setKeyError(envErrorText(err));
    } finally {
      setSaving(false);
    }
  };

  const canSave = !saving && key.trim().length > 0 && (isEdit || value.length > 0 || varType === "config");

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={containerRef}
        role="dialog"
        aria-modal="true"
        aria-label={isEdit ? "Edit Variable" : "Add Variable"}
        className="relative w-full max-w-md rounded-lg border border-border bg-card p-6 shadow-xl"
      >
        <button
          onClick={onClose}
          className="absolute top-4 right-4 text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
        >
          <X className="h-4 w-4" />
        </button>

        <h2 className="text-section font-semibold mb-1">
          {isEdit ? "Edit Variable" : "Add Variable"}
          {effectiveScope === "qa" && (
            <span className="ml-2 inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium bg-purple/10 text-purple ring-1 ring-inset ring-purple/20 align-middle">
              QA
            </span>
          )}
        </h2>
        {effectiveScope === "qa" && !isEdit && (
          <p className="text-xs text-muted-foreground mb-5">
            QA test credentials must use keys starting with <code className="font-mono">QA_</code>{" "}
            (uppercase letters, digits, underscores).
          </p>
        )}
        {effectiveScope !== "qa" && <div className="mb-5" />}

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <Label htmlFor="env-key">Key</Label>
            <Input
              id="env-key"
              value={key}
              onChange={(e) => {
                setKey(e.target.value);
                if (keyError) setKeyError(null);
              }}
              placeholder={effectiveScope === "qa" ? "QA_TEST_USER" : "MY_VARIABLE"}
              className="font-mono mt-1"
              disabled={isEdit}
              autoFocus={!isEdit}
              aria-invalid={keyError ? true : undefined}
              aria-describedby={keyError ? "env-key-error" : undefined}
            />
            {keyError && (
              <p id="env-key-error" role="alert" className="mt-1 text-sm text-destructive">
                {keyError}
              </p>
            )}
          </div>

          <div>
            <Label htmlFor="env-value">Value</Label>
            <Input
              id="env-value"
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder={isEdit && varType === "secret" ? "Enter new value..." : ""}
              className="font-mono mt-1"
              type={varType === "secret" ? "password" : "text"}
              autoFocus={isEdit}
            />
          </div>

          <div>
            <Label>Type</Label>
            <div className="flex gap-2 mt-1">
              <button
                type="button"
                onClick={() => setVarType("config")}
                className={`flex-1 px-3 py-2 rounded-md text-sm font-medium transition-colors cursor-pointer ${
                  varType === "config"
                    ? "bg-success/15 text-success ring-1 ring-success/30"
                    : "bg-white/[0.03] text-muted-foreground hover:bg-white/[0.06]"
                }`}
              >
                Config
              </button>
              <button
                type="button"
                onClick={() => setVarType("secret")}
                className={`flex-1 px-3 py-2 rounded-md text-sm font-medium transition-colors cursor-pointer ${
                  varType === "secret"
                    ? "bg-amber-500/15 text-amber-400 ring-1 ring-amber-500/30"
                    : "bg-white/[0.03] text-muted-foreground hover:bg-white/[0.06]"
                }`}
              >
                Secret
              </button>
            </div>
          </div>

          <div>
            <Label htmlFor="env-desc">Description</Label>
            <Input
              id="env-desc"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="What this variable is used for..."
              className="mt-1"
            />
          </div>

          <div className="flex items-center gap-2">
            <input
              type="checkbox"
              id="env-required"
              checked={required}
              onChange={(e) => setRequired(e.target.checked)}
              className="h-4 w-4 rounded border-input"
            />
            <Label htmlFor="env-required">Required</Label>
          </div>

          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="outline" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" disabled={!canSave}>
              {isEdit ? "Update" : "Add"}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
