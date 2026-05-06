/**
 * Phase 5B: dashboard panel that surfaces network failures the QA agent
 * captured during exploratory testing.
 *
 * Each artifact under `network/` is fetched and parsed as JSON. A HAR file
 * has a top-level `log.entries[]` array with `request.url`, `request.method`,
 * and `response.status` per entry; a sidecar `failures.json` may have a
 * flatter `[{url, method, status}, ...]` shape. We try both.
 *
 * If parsing fails, we fall back to a `<pre>` of the raw body so the user can
 * still triage rather than seeing a generic "couldn't parse" message.
 */
import { useEffect, useState } from "react";
import type { JSX } from "react";
import { useAppArtifacts } from "@/hooks/useQaDashboard";
import { artifactUrl } from "@/api/qaDashboard";

interface NetworkEntry {
  url: string;
  method: string;
  status: number;
}

function parseNetworkBody(raw: string): NetworkEntry[] | null {
  let json: unknown;
  try {
    json = JSON.parse(raw);
  } catch {
    return null;
  }
  // HAR shape: { log: { entries: [{ request: {url, method}, response: {status} }, ...] } }
  if (
    json &&
    typeof json === "object" &&
    "log" in json &&
    json.log &&
    typeof json.log === "object" &&
    "entries" in (json.log as Record<string, unknown>) &&
    Array.isArray((json.log as { entries: unknown }).entries)
  ) {
    const entries = (json.log as { entries: unknown[] }).entries;
    return entries.flatMap((e) => {
      if (!e || typeof e !== "object") return [];
      const request = (e as Record<string, unknown>).request as
        | Record<string, unknown>
        | undefined;
      const response = (e as Record<string, unknown>).response as
        | Record<string, unknown>
        | undefined;
      const url = typeof request?.url === "string" ? request.url : "";
      const method = typeof request?.method === "string" ? request.method : "?";
      const status = typeof response?.status === "number" ? response.status : 0;
      return url ? [{ url, method, status }] : [];
    });
  }
  // Flat-array shape
  if (Array.isArray(json)) {
    return (json as unknown[]).flatMap((e) => {
      if (!e || typeof e !== "object") return [];
      const obj = e as Record<string, unknown>;
      const url = typeof obj.url === "string" ? obj.url : "";
      const method = typeof obj.method === "string" ? obj.method : "?";
      const status = typeof obj.status === "number" ? obj.status : 0;
      return url ? [{ url, method, status }] : [];
    });
  }
  return null;
}

interface NetworkEntryPanelProps {
  url: string;
  filename: string;
}

function NetworkEntryPanel({ url, filename }: NetworkEntryPanelProps): JSX.Element {
  const [raw, setRaw] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch(url)
      .then(async (r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.text();
      })
      .then((text) => {
        if (!cancelled) setRaw(text);
      })
      .catch((e: Error) => {
        if (!cancelled) setErr(e.message);
      });
    return () => {
      cancelled = true;
    };
  }, [url]);

  if (err) {
    return (
      <div className="rounded border border-white/10 bg-white/5 px-3 py-2 text-xs text-destructive">
        {filename}: failed to load — {err}
      </div>
    );
  }
  if (raw === null) {
    return (
      <div className="rounded border border-white/10 bg-white/5 px-3 py-2 text-xs text-white/40">
        {filename}: loading…
      </div>
    );
  }
  const parsed = parseNetworkBody(raw);
  return (
    <div
      data-testid="qa-network-entry"
      data-filename={filename}
      className="rounded border border-white/10 bg-white/5 px-3 py-2"
    >
      <div className="mb-1 font-mono text-xs text-white/80">{filename}</div>
      {parsed === null ? (
        <>
          <div className="mb-1 text-xs text-yellow-300">
            Unrecognised JSON shape — showing raw body.
          </div>
          <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-sm bg-black/40 px-2 py-1 text-xs text-white/70">
            {raw}
          </pre>
        </>
      ) : parsed.length === 0 ? (
        <div className="text-xs text-white/40">No entries.</div>
      ) : (
        <table className="w-full table-fixed text-xs">
          <thead>
            <tr className="text-left text-white/50">
              <th className="w-16">Method</th>
              <th>URL</th>
              <th className="w-16">Status</th>
            </tr>
          </thead>
          <tbody>
            {parsed.map((row, idx) => (
              <tr key={idx} className="border-t border-white/10">
                <td className="font-mono text-white/80">{row.method}</td>
                <td className="truncate font-mono text-white/70" title={row.url}>
                  {row.url}
                </td>
                <td
                  className={
                    row.status >= 400 ? "text-destructive" : "text-white/70"
                  }
                >
                  {row.status}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

interface Props {
  runId: string;
  app: string;
}

export function QaNetworkFailuresPanel({ runId, app }: Props): JSX.Element {
  const { data, isLoading, isError } = useAppArtifacts(runId, app, "network");

  if (isLoading) {
    return <div className="text-sm text-white/40">Loading network failures…</div>;
  }
  if (isError) {
    return (
      <div className="text-sm text-destructive">
        Failed to load network failures.
      </div>
    );
  }
  const filenames = data ?? [];
  if (filenames.length === 0) {
    return (
      <div className="text-sm text-white/40" data-testid="qa-network-empty">
        No network failures captured.
      </div>
    );
  }

  return (
    <div className="space-y-2" data-testid="qa-network-panel">
      {filenames.map((fn) => (
        <NetworkEntryPanel
          key={fn}
          filename={fn}
          url={artifactUrl(runId, app, "network", fn)}
        />
      ))}
    </div>
  );
}
