import { useId, useState, type KeyboardEvent } from "react";
import { Link } from "react-router";
import { X } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Input } from "@/components/ui/Input";
import { isRepositorySkill } from "@/lib/skills";
import type { SkillCatalogEntry } from "@/lib/types";
import { cn } from "@/lib/utils";

interface SkillPickerProps {
  value: string[];
  onChange: (next: string[]) => void;
  catalog: SkillCatalogEntry[];
  isLoading?: boolean;
  disabled?: boolean;
  /** Admit a typed name without a colon (a repository's own `.claude/skills` entry). */
  allowRepositorySkills?: boolean;
  /** Id of the search input, for an outside `<label htmlFor>`. */
  id?: string;
}

/**
 * Pick skill names from the catalog. A typed name is admitted only when the
 * catalog lists it or, with `allowRepositorySkills`, when it has no colon.
 */
export function SkillPicker({
  value,
  onChange,
  catalog,
  isLoading = false,
  disabled = false,
  allowRepositorySkills = true,
  id,
}: SkillPickerProps) {
  const [query, setQuery] = useState("");
  const [error, setError] = useState<string | null>(null);
  // Index into `filtered` of the option the arrow keys highlight; -1 for none.
  const [active, setActive] = useState(-1);
  const listboxId = useId();
  const optionId = (i: number) => `${listboxId}-option-${i}`;

  const add = (name: string) => {
    if (disabled || value.includes(name)) return;
    onChange([...value, name]);
    setQuery("");
    setError(null);
    setActive(-1);
  };

  const remove = (name: string) => {
    if (disabled) return;
    onChange(value.filter((v) => v !== name));
  };

  const needle = query.toLowerCase();
  const filtered = catalog.filter((c) => `${c.name} ${c.description}`.toLowerCase().includes(needle));
  const listboxShown = !isLoading;
  const highlighted = listboxShown && active >= 0 && active < filtered.length ? active : -1;

  const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (!listboxShown || filtered.length === 0) return;
      const step = e.key === "ArrowDown" ? 1 : -1;
      const from = highlighted === -1 ? (step === 1 ? -1 : filtered.length) : highlighted;
      setActive(Math.min(filtered.length - 1, Math.max(0, from + step)));
      return;
    }
    if (e.key === "Escape") {
      setActive(-1);
      return;
    }
    if (e.key !== "Enter") return;
    e.preventDefault();
    if (highlighted !== -1) {
      add(filtered[highlighted].name);
      return;
    }
    const name = query.trim();
    if (!name) return;
    if (catalog.some((c) => c.name === name) || (allowRepositorySkills && isRepositorySkill(name))) {
      add(name);
    } else {
      setError(`Unknown skill "${name}": pick one from the list`);
    }
  };

  return (
    <div className={cn("space-y-2", disabled && "opacity-60")}>
      {value.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {value.map((name) => (
            <span
              key={name}
              className="inline-flex items-center gap-1 rounded bg-white/[0.06] px-2 py-0.5 font-mono text-xs text-white/80"
            >
              {name}
              {!disabled && (
                <button
                  type="button"
                  aria-label={`Remove ${name}`}
                  onClick={() => remove(name)}
                  className="hover:text-destructive"
                >
                  <X className="h-3 w-3" />
                </button>
              )}
            </span>
          ))}
        </div>
      )}
      <Input
        id={id}
        role="combobox"
        aria-label="Search skills"
        aria-expanded={listboxShown && filtered.length > 0}
        aria-controls={listboxShown ? listboxId : undefined}
        aria-activedescendant={highlighted !== -1 ? optionId(highlighted) : undefined}
        aria-autocomplete="list"
        value={query}
        onChange={(e) => { setQuery(e.target.value); setError(null); setActive(-1); }}
        onKeyDown={handleKeyDown}
        placeholder="Search skills or type a name"
        disabled={disabled}
      />
      {error && (
        <p role="alert" className="text-xs text-destructive">{error}</p>
      )}
      {isLoading ? (
        <p className="text-xs text-white/40">Loading catalog...</p>
      ) : (
        <ul
          id={listboxId}
          role="listbox"
          aria-label="Skills"
          className="max-h-60 overflow-auto rounded-md border border-white/[0.08] bg-[#0c1015] py-1"
        >
          {filtered.map((c, i) => {
            const selected = value.includes(c.name);
            return (
              <li
                key={c.name}
                id={optionId(i)}
                role="option"
                // aria-selected is the keyboard highlight; a name already
                // picked is disabled (it shows as a pill above).
                aria-selected={i === highlighted}
                aria-disabled={disabled || selected || undefined}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => add(c.name)}
                className={cn(
                  "flex cursor-pointer items-start justify-between gap-2 px-3 py-1.5 hover:bg-white/[0.04]",
                  i === highlighted && "bg-white/[0.08]",
                  selected && "cursor-default opacity-50",
                )}
              >
                <span className="min-w-0">
                  <span className="block font-mono text-xs text-white/80">{c.name}</span>
                  {c.description && <span className="block text-xs text-white/40">{c.description}</span>}
                </span>
                <Badge tone={c.available ? "success" : "warning"}>{c.available ? "available" : c.status}</Badge>
              </li>
            );
          })}
        </ul>
      )}
      <p className="text-xs text-white/40">
        A name without a colon is a skill the repository ships under .claude/skills. When this list is
        non-empty it is the allowlist, so list those too. Manage sources on the{" "}
        <Link to="/skills" className="text-cyan-300 hover:underline">Skills page</Link>.
      </p>
    </div>
  );
}
