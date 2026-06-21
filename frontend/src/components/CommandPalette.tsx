import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router";
import { useMutation } from "@tanstack/react-query";
import { Search, Loader2 } from "lucide-react";
import { interpretCommand, type InterpretResult } from "@/api/agent";
import { Input } from "@/components/ui/Input";
import { cn } from "@/lib/utils";

/**
 * Global Cmd+K palette. Translates natural-language queries to InterpretResult
 * via the agent's `/interpret` endpoint. On navigate-intent results, the
 * palette routes the operator to the suggested URL.
 */
export function CommandPalette() {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [result, setResult] = useState<InterpretResult | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const navigate = useNavigate();

  const interpret = useMutation({
    mutationFn: (q: string) => interpretCommand(q),
    onSuccess: (data) => {
      setResult(data);
      if (data.intent === "navigate" && data.url) {
        navigate(data.url);
        close();
      }
    },
  });

  const close = useCallback(() => {
    setOpen(false);
    setQuery("");
    setResult(null);
    interpret.reset();
  }, [interpret]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const isOpenChord = e.key === "k" && (e.metaKey || e.ctrlKey);
      if (isOpenChord) {
        e.preventDefault();
        setOpen((v) => !v);
        return;
      }
      if (open && e.key === "Escape") {
        e.preventDefault();
        close();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, close]);

  useEffect(() => {
    if (open) {
      // The input mounts inside the same render — focus after paint.
      queueMicrotask(() => inputRef.current?.focus());
    }
  }, [open]);

  if (!open) return null;

  function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const trimmed = query.trim();
    if (!trimmed) return;
    interpret.mutate(trimmed);
  }

  return (
    <div
      data-testid="command-palette"
      className="fixed inset-0 z-50 flex items-start justify-center p-4 pt-[10vh]"
      role="dialog"
      aria-label="Command palette"
    >
      <div
        aria-hidden
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={close}
      />
      <div className="relative w-full max-w-xl overflow-hidden rounded-lg border border-white/[0.08] bg-[#0c1015] shadow-[0_24px_80px_rgba(0,0,0,0.5)]">
        <form data-testid="command-palette-form" onSubmit={onSubmit}>
          <div className="flex items-center gap-2 border-b border-white/[0.06] px-3">
            <Search className="h-4 w-4 text-white/40" />
            <Input
              ref={inputRef}
              data-testid="command-palette-input"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Ask companion… (e.g. show running jobs)"
              className="h-11 border-0 bg-transparent shadow-none focus-visible:ring-0"
              aria-label="Command query"
            />
            {interpret.isPending && (
              <Loader2 className="h-4 w-4 animate-spin text-white/40" aria-hidden />
            )}
          </div>
        </form>
        {(result || interpret.isError) && (
          <div
            data-testid="command-palette-result"
            className={cn(
              "px-4 py-3 text-sm",
              interpret.isError ? "text-destructive" : "text-white/80",
            )}
          >
            {interpret.isError
              ? "companion could not interpret that query."
              : result?.message}
          </div>
        )}
        <div className="border-t border-white/[0.06] px-3 py-2 text-[10px] uppercase tracking-wide text-white/40">
          <kbd className="rounded border border-white/10 px-1">Esc</kbd> to close ·{" "}
          <kbd className="rounded border border-white/10 px-1">↵</kbd> to ask
        </div>
      </div>
    </div>
  );
}
