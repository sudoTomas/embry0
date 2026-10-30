import { useState, useRef, useEffect } from "react";
import { Input } from "@/components/ui/Input";
import { useComponentTimeout } from "@/hooks/useComponentTimeout";

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
  const later = useComponentTimeout();
  // The text and the value as they are now, for the commit a blur makes 150 ms
  // later. A click on an option lands inside those 150 ms and sets both to
  // the option; compared as they were at the blur, the commit would put the
  // filter typed to find the option back (#267).
  const latest = useRef({ search, value });
  useEffect(() => {
    latest.current = { search, value };
  });

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
          later(() => {
            setOpen(false);
            const { search: typed, value: chosen } = latest.current;
            if (typed !== chosen) {
              onChange(typed);
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
