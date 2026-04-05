import { useState, useRef, useEffect } from "react";
import { Input } from "@/components/ui/Input";

interface RepoSelectorProps {
  value: string;
  onChange: (value: string) => void;
  repos: string[];
  placeholder?: string;
}

export function RepoSelector({ value, onChange, repos, placeholder = "Select repository..." }: RepoSelectorProps) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState(value);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => { setSearch(value); }, [value]);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const filtered = repos.filter((r) => r.toLowerCase().includes(search.toLowerCase()));

  return (
    <div ref={ref} className="relative">
      <Input
        value={search}
        onChange={(e) => { setSearch(e.target.value); setOpen(true); }}
        onFocus={() => setOpen(true)}
        onBlur={() => {
          setTimeout(() => {
            setOpen(false);
            if (search !== value) {
              onChange(search);
            }
          }, 150);
        }}
        placeholder={placeholder}
      />
      {open && filtered.length > 0 && (
        <div className="absolute z-50 mt-1 max-h-48 w-full overflow-auto rounded-md border border-border bg-card shadow-lg">
          {filtered.map((repo) => (
            <button key={repo} type="button" className="w-full px-3 py-2 text-left text-sm hover:bg-accent"
              onClick={() => { onChange(repo); setSearch(repo); setOpen(false); }}>
              {repo}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
