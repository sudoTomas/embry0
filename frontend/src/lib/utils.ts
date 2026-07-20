import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";
import { format, formatDistanceToNow } from "date-fns";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatDate(iso: string): string {
  const d = new Date(iso);
  const now = new Date();
  const diffMs = now.getTime() - d.getTime();
  if (diffMs < 24 * 60 * 60 * 1000) {
    return formatDistanceToNow(d, { addSuffix: true });
  }
  return format(d, "MMM d, yyyy HH:mm");
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
      segments.push({ text: ": ", className: "text-muted-foreground" });
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
