import { useState, type KeyboardEvent } from "react";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";

interface TagInputProps {
  value: string[];
  onChange: (tags: string[]) => void;
  placeholder?: string;
  suggestions?: string[];
  className?: string;
  disabled?: boolean;
}

export function TagInput({ value, onChange, placeholder = "Add...", suggestions, className, disabled = false }: TagInputProps) {
  const [input, setInput] = useState("");
  const [showSuggestions, setShowSuggestions] = useState(false);

  const addTag = (tag: string) => {
    if (disabled) return;
    const trimmed = tag.trim();
    if (trimmed && !value.includes(trimmed)) {
      onChange([...value, trimmed]);
    }
    setInput("");
    setShowSuggestions(false);
  };

  const removeTag = (index: number) => {
    if (disabled) return;
    onChange(value.filter((_, i) => i !== index));
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (disabled) return;
    if (e.key === "Enter" && input.trim()) {
      e.preventDefault();
      addTag(input);
    } else if (e.key === "Backspace" && !input && value.length > 0) {
      removeTag(value.length - 1);
    }
  };

  const filtered = suggestions?.filter(
    (s) => s.toLowerCase().includes(input.toLowerCase()) && !value.includes(s),
  );

  return (
    <div className={cn("relative", className, disabled && "opacity-60")}>
      <div className="flex flex-wrap gap-1.5 rounded-md border border-white/[0.08] bg-[#0c1015] px-2 py-1.5 min-h-[36px]">
        {value.map((tag, i) => (
          <span
            key={tag}
            className="inline-flex items-center gap-1 rounded bg-white/[0.06] px-2 py-0.5 text-xs text-white/80"
          >
            {tag}
            {!disabled && (
              <button type="button" onClick={() => removeTag(i)} className="hover:text-destructive">
                <X className="w-3 h-3" />
              </button>
            )}
          </span>
        ))}
        <input
          value={input}
          onChange={(e) => { setInput(e.target.value); setShowSuggestions(true); }}
          onKeyDown={handleKeyDown}
          onFocus={() => setShowSuggestions(true)}
          onBlur={() => setTimeout(() => setShowSuggestions(false), 150)}
          placeholder={value.length === 0 ? placeholder : ""}
          disabled={disabled}
          className="flex-1 min-w-[80px] bg-transparent text-sm outline-none text-white/80 placeholder:text-white/30 disabled:cursor-not-allowed"
        />
      </div>
      {!disabled && showSuggestions && filtered && filtered.length > 0 && (
        <div className="absolute z-50 mt-1 w-full rounded-md border border-white/[0.08] bg-[#0c1015] py-1 shadow-lg max-h-40 overflow-auto">
          {filtered.map((s) => (
            <button
              key={s}
              type="button"
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => addTag(s)}
              className="w-full px-3 py-1.5 text-left text-sm text-white/70 hover:bg-white/[0.04] hover:text-white"
            >
              {s}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
