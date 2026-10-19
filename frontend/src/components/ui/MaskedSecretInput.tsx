import { useState } from "react";
import { Eye, EyeOff, Pencil } from "lucide-react";
import { Input } from "./Input";
import { Button } from "./Button";

interface MaskedSecretInputProps {
  /** The input's id, so a <Label htmlFor> names it. */
  id?: string;
  isSet: boolean;
  maskedValue?: string;
  onChange: (value: string) => void;
  placeholder?: string;
}

export function MaskedSecretInput({ id, isSet, maskedValue, onChange, placeholder }: MaskedSecretInputProps) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState("");
  const [visible, setVisible] = useState(false);

  if (!editing && isSet) {
    return (
      <div className="flex items-center gap-2">
        <span className="text-sm text-fg-muted font-mono">{maskedValue || "••••••••"}</span>
        <Button variant="ghost" size="sm" onClick={() => setEditing(true)}>
          <Pencil className="w-3.5 h-3.5" />
          Change
        </Button>
      </div>
    );
  }

  return (
    <div className="flex items-center gap-2">
      <div className="relative flex-1">
        <Input
          id={id}
          type={visible ? "text" : "password"}
          value={value}
          onChange={(e) => { setValue(e.target.value); onChange(e.target.value); }}
          placeholder={placeholder}
          className="pr-10"
        />
        <button
          type="button"
          onClick={() => setVisible(!visible)}
          aria-label={visible ? "Hide secret" : "Show secret"}
          className="absolute right-2 top-1/2 -translate-y-1/2 text-fg-muted hover:text-fg"
        >
          {visible ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
        </button>
      </div>
      {editing && (
        <Button variant="ghost" size="sm" onClick={() => { setEditing(false); setValue(""); onChange(""); }}>
          Cancel
        </Button>
      )}
    </div>
  );
}
