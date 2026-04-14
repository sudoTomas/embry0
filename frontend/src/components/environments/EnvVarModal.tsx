import { useState, useEffect } from "react";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { Button } from "@/components/ui/Button";
import { X } from "lucide-react";
import type { EnvVar } from "@/lib/types/environment";

interface EnvVarModalProps {
  variable?: EnvVar | null; // null = add mode, populated = edit mode
  onSave: (variable: EnvVar) => void;
  onClose: () => void;
}

export function EnvVarModal({ variable, onSave, onClose }: EnvVarModalProps) {
  const isEdit = !!variable;

  const [key, setKey] = useState(variable?.key ?? "");
  const [value, setValue] = useState(variable?.var_type === "secret" ? "" : (variable?.value ?? ""));
  const [varType, setVarType] = useState<"config" | "secret">(variable?.var_type ?? "config");
  const [description, setDescription] = useState(variable?.description ?? "");
  const [required, setRequired] = useState(variable?.required ?? false);

  useEffect(() => {
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

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!key.trim()) return;

    const trimmedKey = key.trim().toUpperCase();

    const finalValue =
      isEdit && varType === "secret" && value === "" ? variable!.value : value;

    onSave({
      key: trimmedKey,
      value: finalValue,
      var_type: varType,
      description: description.trim(),
      required,
    });
  };

  const canSave = key.trim().length > 0 && (isEdit || value.length > 0 || varType === "config");

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="relative w-full max-w-md rounded-lg border border-border bg-card p-6 shadow-xl">
        <button
          onClick={onClose}
          className="absolute top-4 right-4 text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
        >
          <X className="h-4 w-4" />
        </button>

        <h2 className="text-lg font-semibold mb-6">{isEdit ? "Edit Variable" : "Add Variable"}</h2>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <Label htmlFor="env-key">Key</Label>
            <Input
              id="env-key"
              value={key}
              onChange={(e) => setKey(e.target.value)}
              placeholder="MY_VARIABLE"
              className="font-mono mt-1"
              disabled={isEdit}
              autoFocus={!isEdit}
            />
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
                    ? "bg-green-500/15 text-green-400 ring-1 ring-green-500/30"
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
