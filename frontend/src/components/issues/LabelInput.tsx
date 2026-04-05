import { useState } from "react";
import { Input } from "@/components/ui/Input";
import { X } from "lucide-react";

interface LabelInputProps {
  value: string[];
  onChange: (labels: string[]) => void;
  suggestions?: string[];
}

export function LabelInput({ value, onChange, suggestions = [] }: LabelInputProps) {
  const [input, setInput] = useState("");
  const [showSuggestions, setShowSuggestions] = useState(false);

  const addLabel = (label: string) => {
    const trimmed = label.trim().toLowerCase();
    if (trimmed && !value.includes(trimmed)) { onChange([...value, trimmed]); }
    setInput("");
    setShowSuggestions(false);
  };

  const removeLabel = (label: string) => { onChange(value.filter((l) => l !== label)); };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" || e.key === ",") { e.preventDefault(); addLabel(input); }
    if (e.key === "Backspace" && input === "" && value.length > 0) { removeLabel(value[value.length - 1]); }
  };

  const filtered = suggestions.filter((s) => s.toLowerCase().includes(input.toLowerCase()) && !value.includes(s));

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-1.5">
        {value.map((label) => (
          <span key={label} className="inline-flex items-center gap-1 rounded-md bg-zinc-700/50 px-2 py-0.5 text-xs text-zinc-300">
            {label}
            <button type="button" onClick={() => removeLabel(label)} className="hover:text-white"><X size={12} /></button>
          </span>
        ))}
      </div>
      <div className="relative">
        <Input value={input} onChange={(e) => { setInput(e.target.value); setShowSuggestions(true); }}
          onKeyDown={handleKeyDown} onFocus={() => setShowSuggestions(true)}
          onBlur={() => setTimeout(() => setShowSuggestions(false), 150)} placeholder="Type and press Enter..." />
        {showSuggestions && filtered.length > 0 && (
          <div className="absolute z-50 mt-1 max-h-32 w-full overflow-auto rounded-md border border-border bg-card shadow-lg">
            {filtered.map((s) => (
              <button key={s} type="button" className="w-full px-3 py-1.5 text-left text-sm hover:bg-accent"
                onMouseDown={() => addLabel(s)}>{s}</button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
