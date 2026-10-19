import { clsx, type ClassValue } from "clsx";
import { extendTailwindMerge } from "tailwind-merge";
import { format, formatDistanceToNow } from "date-fns";

// The theme's own font sizes (index.css @theme --text-*). Without them
// tailwind-merge reads `text-nav` as a colour and drops it next to `text-fg`.
const twMerge = extendTailwindMerge({
  extend: { theme: { text: ["nav", "section", "tile"] } },
});

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** A timestamp from the API as a Date. Python's `str(datetime)` puts a space
 * where ISO puts a `T`; not every browser parses that form. */
function parseTimestamp(value: string | Date): Date {
  if (value instanceof Date) return value;
  return new Date(/^\d{4}-\d{2}-\d{2} /.test(value) ? value.replace(" ", "T") : value);
}

/**
 * The one date-and-time format for table cells and meta lines, like the
 * sibling dashboards': short month, day and 24-hour time, with the year only
 * when it is not the current one ("Sep 8, 13:34", "Dec 30, 2025 09:05"). A
 * value that does not parse is shown as it came.
 */
export function formatDateTime(value: string | Date, now: Date = new Date()): string {
  const d = parseTimestamp(value);
  if (Number.isNaN(d.getTime())) return String(value);
  return format(d, d.getFullYear() === now.getFullYear() ? "MMM d, HH:mm" : "MMM d, yyyy HH:mm");
}

/** The date part of that format, for a meta line that carries no time
 * ("Sep 8", "Dec 30, 2025"). */
export function formatDay(value: string | Date, now: Date = new Date()): string {
  const d = parseTimestamp(value);
  if (Number.isNaN(d.getTime())) return String(value);
  return format(d, d.getFullYear() === now.getFullYear() ? "MMM d" : "MMM d, yyyy");
}

/** 24-hour clock time ("23:49"), for a time range inside one row. */
export function formatTime(value: string | Date): string {
  const d = parseTimestamp(value);
  return Number.isNaN(d.getTime()) ? String(value) : format(d, "HH:mm");
}

/** Prose time ("finished 3 minutes ago"): relative within a day, then the
 * table format. */
export function formatDate(iso: string): string {
  const d = parseTimestamp(iso);
  const now = new Date();
  const diffMs = now.getTime() - d.getTime();
  if (diffMs < 24 * 60 * 60 * 1000) {
    return formatDistanceToNow(d, { addSuffix: true });
  }
  return formatDateTime(d, now);
}

export function formatCost(usd: number): string {
  return `$${usd.toFixed(2)}`;
}

export function formatPercent(rate: number): string {
  return `${(rate * 100).toFixed(1)}%`;
}

/**
 * Compact token-count formatting for table cells and stat tiles:
 * 812 -> "812", 34_120 -> "34.1k", 5_600_000 -> "5.6M".
 */
export function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return `${n}`;
}

export function getCostColor(used: number, budget: number): string {
  const ratio = budget > 0 ? used / budget : 0;
  if (ratio < 0.5) return "text-success";
  if (ratio < 0.8) return "text-warning";
  return "text-destructive";
}

export function getCostBarColor(used: number, budget: number): string {
  const ratio = budget > 0 ? used / budget : 0;
  if (ratio < 0.5) return "bg-success";
  if (ratio < 0.8) return "bg-warning";
  return "bg-destructive";
}

/**
 * Extract a role key from a subagent name.
 * e.g. "developer_agent" -> "developer", "validator_v2" -> "validator"
 */
export function extractRole(agentName: string): string | undefined {
  const lower = agentName.toLowerCase();
  const roles = ["developer", "validator", "explorer", "triage"];
  return roles.find((r) => lower.includes(r));
}

/**
 * Syntax-highlight a JSON string with Tailwind text color spans.
 * Returns an array of React-renderable elements.
 */
export function highlightJson(json: string): Array<{ text: string; className: string }> {
  const segments: Array<{ text: string; className: string }> = [];
  // Regex to match JSON tokens: keys (quoted before colon), strings, numbers, booleans, null, punctuation
  const tokenRegex = /("(?:[^"\\]|\\.)*")\s*:|("(?:[^"\\]|\\.)*")|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)|(\btrue\b|\bfalse\b)|(\bnull\b)|([{}[\]:,])|(\s+)/g;
  let match: RegExpExecArray | null;
  let lastIndex = 0;

  while ((match = tokenRegex.exec(json)) !== null) {
    // Capture any unmatched text before this match
    if (match.index > lastIndex) {
      segments.push({ text: json.slice(lastIndex, match.index), className: "" });
    }
    lastIndex = tokenRegex.lastIndex;

    if (match[1] != null) {
      // Key (quoted string before colon)
      segments.push({ text: match[1], className: "text-primary" });
      segments.push({ text: ":", className: "text-muted-foreground" });
    } else if (match[2] != null) {
      // String value
      segments.push({ text: match[2], className: "text-success" });
    } else if (match[3] != null) {
      // Number
      segments.push({ text: match[3], className: "text-warning" });
    } else if (match[4] != null) {
      // Boolean
      segments.push({ text: match[4], className: "text-role-explorer" });
    } else if (match[5] != null) {
      // null
      segments.push({ text: match[5], className: "text-muted-foreground" });
    } else if (match[6] != null) {
      // Punctuation
      segments.push({ text: match[6], className: "text-muted-foreground" });
    } else if (match[7] != null) {
      // Whitespace
      segments.push({ text: match[7], className: "" });
    }
  }

  // Remaining text
  if (lastIndex < json.length) {
    segments.push({ text: json.slice(lastIndex), className: "" });
  }

  return segments;
}
