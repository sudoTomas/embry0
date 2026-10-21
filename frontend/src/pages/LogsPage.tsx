import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { ChevronDown, Pause, Play, ScrollText, Search } from "lucide-react";
import { isAxiosError } from "axios";
import { fetchLogContainers, queryLogs } from "@/api/logs";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { EmptyState } from "@/components/ui/EmptyState";
import type { LogsQueryResponse } from "@/lib/types";

const WINDOW_OPTIONS = [
  { label: "Last 15 minutes", minutes: 15 },
  { label: "Last hour", minutes: 60 },
  { label: "Last 6 hours", minutes: 360 },
  { label: "Last 24 hours", minutes: 1440 },
] as const;

const TAIL_INTERVAL_MS = 5000;
// Newest lines render at the top — once the operator scrolls down to read
// older lines, a refresh would yank the viewport. Pause past this offset.
const TAIL_PAUSE_SCROLL_PX = 40;

/**
 * Container multi-select (RAV-1295): checkbox dropdown fed by
 * GET /logs/containers. Empty selection = every container.
 */
function ContainerMultiSelect({
  containers,
  selected,
  onToggle,
  onClear,
}: {
  containers: string[];
  selected: string[];
  onToggle: (name: string) => void;
  onClear: () => void;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onDocClick(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [open]);

  const summary =
    selected.length === 0
      ? "All containers"
      : selected.length === 1
        ? selected[0]
        : `${selected.length} containers`;

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="listbox"
        aria-expanded={open}
        className="flex h-10 w-full items-center justify-between rounded-md border border-input bg-background px-3 py-2 text-sm"
      >
        <span className="truncate">{summary}</span>
        <ChevronDown className="h-4 w-4 shrink-0 opacity-50" />
      </button>
      {open && (
        <div
          role="listbox"
          aria-multiselectable="true"
          className="absolute z-20 mt-1 max-h-64 w-64 overflow-auto rounded-md border border-border bg-overlay p-2 shadow-lg"
        >
          <button
            type="button"
            onClick={onClear}
            className="mb-1 w-full rounded px-2 py-1 text-left text-xs text-muted-foreground hover:bg-white/[0.05]"
          >
            All containers (clear selection)
          </button>
          {containers.map((name) => (
            <label
              key={name}
              className="flex cursor-pointer items-center gap-2 rounded px-2 py-1 text-xs font-mono hover:bg-white/[0.05]"
            >
              <input
                type="checkbox"
                checked={selected.includes(name)}
                onChange={() => onToggle(name)}
                className="h-3.5 w-3.5 accent-primary"
              />
              <span className="truncate">{name}</span>
            </label>
          ))}
          {containers.length === 0 && (
            <p className="px-2 py-1 text-xs text-muted-foreground">No containers reported by Loki.</p>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * Log explorer (RAV-1058 increment 2, exposes RAV-1057; RAV-1295 adds the
 * container dropdown, the all-containers query on mount, and live tail).
 * Results are a newest-first tail straight from Loki via the orchestrator.
 */
export function LogsPage() {
  const [selectedContainers, setSelectedContainers] = useState<string[]>([]);
  const [search, setSearch] = useState("");
  const [sinceMinutes, setSinceMinutes] = useState(60);
  const [result, setResult] = useState<LogsQueryResponse | null>(null);
  const [tail, setTail] = useState(false);
  const [tailPaused, setTailPaused] = useState(false);

  const { data: containersData } = useQuery({
    queryKey: ["logs", "containers"],
    queryFn: fetchLogContainers,
    staleTime: 60_000,
    retry: 1,
  });
  const containers = containersData?.containers ?? [];

  const query = useMutation({
    mutationFn: () =>
      queryLogs({
        containers: selectedContainers.length ? selectedContainers.join(",") : undefined,
        search: search.trim() || undefined,
        since_minutes: sinceMinutes,
        limit: 500,
      }),
    onSuccess: setResult,
  });
  // The mutate fn is recreated per render; keep a ref so the mount/interval
  // effects don't re-fire on every keystroke.
  const queryRef = useRef(query.mutate);
  queryRef.current = query.mutate;

  // RAV-1295: no empty landing state — the backend already defaults to
  // every container, so run that query immediately.
  useEffect(() => {
    queryRef.current();
  }, []);

  // Live tail: cheap polling with the same window/limit clamps as a manual
  // query. Paused while the operator reads older lines (scrolled down).
  useEffect(() => {
    if (!tail || tailPaused) return;
    const id = setInterval(() => queryRef.current(), TAIL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [tail, tailPaused]);

  const onTailScroll = useCallback(
    (e: React.UIEvent<HTMLDivElement>) => {
      if (!tail) return;
      setTailPaused(e.currentTarget.scrollTop > TAIL_PAUSE_SCROLL_PX);
    },
    [tail],
  );

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    query.mutate();
  }

  const errorMessage =
    query.isError && isAxiosError(query.error) && query.error.response?.status === 503
      ? "Loki is not configured on the orchestrator (WATCHER_LOKI_URL)."
      : query.isError
        ? "Log query failed — check the orchestrator logs."
        : null;

  return (
    <div className="space-y-6">
      <h1 className="title-pixel text-xl font-bold">Logs</h1>

      <form onSubmit={onSubmit} className="flex flex-wrap items-end gap-2">
        <div className="w-64">
          <label className="mb-1 block text-xs text-muted-foreground">Containers</label>
          <ContainerMultiSelect
            containers={containers}
            selected={selectedContainers}
            onToggle={(name) =>
              setSelectedContainers((prev) =>
                prev.includes(name) ? prev.filter((n) => n !== name) : [...prev, name],
              )
            }
            onClear={() => setSelectedContainers([])}
          />
        </div>
        <div className="w-72">
          <label htmlFor="logs-search" className="mb-1 block text-xs text-muted-foreground">
            Line filter (regex)
          </label>
          <Input
            id="logs-search"
            placeholder='e.g. error|traceback'
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <div className="w-44">
          <label htmlFor="logs-window" className="mb-1 block text-xs text-muted-foreground">
            Window
          </label>
          <Select
            id="logs-window"
            value={String(sinceMinutes)}
            onChange={(e) => setSinceMinutes(Number(e.target.value))}
          >
            {WINDOW_OPTIONS.map((o) => (
              <option key={o.minutes} value={o.minutes}>
                {o.label}
              </option>
            ))}
          </Select>
        </div>
        <Button type="submit" disabled={query.isPending}>
          <Search className="mr-2 h-4 w-4" />
          {query.isPending ? "Querying…" : "Query"}
        </Button>
        <Button
          type="button"
          variant={tail ? "default" : "outline"}
          onClick={() => {
            setTail((t) => !t);
            setTailPaused(false);
          }}
          title={tail ? "Stop live tail" : "Refresh every 5s"}
        >
          {tail ? <Pause className="mr-2 h-4 w-4" /> : <Play className="mr-2 h-4 w-4" />}
          {tail ? "Tail on" : "Live tail"}
        </Button>
      </form>

      {tail && tailPaused && (
        <p className="text-xs text-amber-400">
          Tail paused while you read — scroll back to the top to resume.
        </p>
      )}

      {errorMessage && <p className="text-sm text-destructive">{errorMessage}</p>}

      {result && !query.isError && (
        <div className="space-y-2">
          <p className="text-xs text-muted-foreground">
            <span className="font-mono">{result.query}</span> · {result.count} lines, newest first
          </p>
          {result.lines.length === 0 ? (
            <EmptyState
              icon={ScrollText}
              title="No matching log lines"
              description="Widen the window or loosen the filters."
            />
          ) : (
            <div
              data-testid="logs-tail"
              onScroll={onTailScroll}
              className="max-h-[65vh] overflow-auto rounded-lg border border-border bg-overlay p-3 font-mono text-xs leading-5"
            >
              {result.lines.map((line, i) => (
                <div key={`${line.ts}-${i}`} className="flex gap-3 whitespace-pre-wrap break-all">
                  <span className="shrink-0 text-white/30">
                    {new Date(line.ts).toLocaleTimeString()}
                  </span>
                  <span className="shrink-0 text-fg-secondary">{line.container}</span>
                  <span className="text-white/80">{line.line}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {!result && !query.isError && (
        <EmptyState
          icon={ScrollText}
          title="Loading production container logs"
          description="Querying all containers over the last hour…"
        />
      )}
    </div>
  );
}
