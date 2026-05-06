/**
 * Phase 5B: dashboard panel that lists the browser-console log files an
 * exploratory testing pass captured for a sub-task. Each file is rendered
 * as a `<details>` block — the first interaction expands and fetches the
 * text body via the artifact passthrough. Closed-by-default keeps the
 * card compact when most logs are uninteresting.
 *
 * Fetches go through the authenticated axios client so the configured
 * Bearer token is attached. A bare `fetch(url)` would NOT carry the auth
 * header and would 401 against `AUTH_DEV_MODE=false` in production.
 */
import { useEffect, useState } from "react";
import type { JSX } from "react";
import { useAppArtifacts } from "@/hooks/useQaDashboard";
import { api } from "@/api/client";
import { artifactPath } from "@/api/qaDashboard";

interface Props {
  runId: string;
  app: string;
}

interface ConsoleLogEntryProps {
  runId: string;
  app: string;
  filename: string;
}

function ConsoleLogEntry({
  runId,
  app,
  filename,
}: ConsoleLogEntryProps): JSX.Element {
  const [open, setOpen] = useState(false);
  const [body, setBody] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!open || body !== null || err !== null) return;
    let cancelled = false;
    api
      .get<string>(artifactPath(runId, app, "console", filename), {
        responseType: "text",
        // axios tries to JSON-parse text/* by default; force the raw string.
        transformResponse: [(data) => data],
      })
      .then((r) => {
        if (!cancelled) setBody(typeof r.data === "string" ? r.data : String(r.data));
      })
      .catch((e: Error) => {
        if (!cancelled) setErr(e.message);
      });
    return () => {
      cancelled = true;
    };
  }, [open, runId, app, filename, body, err]);

  return (
    <details
      data-testid="qa-console-log-entry"
      data-filename={filename}
      className="rounded border border-white/10 bg-white/5 px-3 py-2"
      onToggle={(e) => setOpen((e.currentTarget as HTMLDetailsElement).open)}
    >
      <summary className="cursor-pointer font-mono text-xs text-white/80">
        {filename}
      </summary>
      {open && (
        <div className="mt-2">
          {err && (
            <div className="text-xs text-destructive">Failed to load: {err}</div>
          )}
          {!err && body === null && (
            <div className="text-xs text-white/40">Loading…</div>
          )}
          {!err && body !== null && (
            <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-sm bg-black/40 px-2 py-1 text-xs text-white/70">
              {body}
            </pre>
          )}
        </div>
      )}
    </details>
  );
}

export function QaConsoleLogPanel({ runId, app }: Props): JSX.Element {
  const { data, isLoading, isError } = useAppArtifacts(runId, app, "console");

  if (isLoading) {
    return <div className="text-sm text-white/40">Loading console logs…</div>;
  }
  if (isError) {
    return (
      <div className="text-sm text-destructive">Failed to load console logs.</div>
    );
  }
  const filenames = data ?? [];
  if (filenames.length === 0) {
    return (
      <div className="text-sm text-white/40" data-testid="qa-console-log-empty">
        No console logs captured.
      </div>
    );
  }

  return (
    <div className="space-y-2" data-testid="qa-console-log-panel">
      {filenames.map((fn) => (
        <ConsoleLogEntry key={fn} runId={runId} app={app} filename={fn} />
      ))}
    </div>
  );
}
