import { useState } from "react";
import {
  ChevronRight,
  ChevronDown,
  CheckCircle,
  XCircle,
} from "lucide-react";
import { cn, highlightJson } from "@/lib/utils";
import { ROLE_COLORS, ROLE_BORDER_COLORS, ROLE_LABELS } from "@/lib/constants";
import { ROLE_ICON_MAP } from "@/lib/roleIcons";

interface ToolCallCardProps {
  tool: string;
  input: Record<string, unknown>;
  output?: string;
  error?: boolean;
  isActive: boolean;
  role?: string;
}

function HighlightedJson({ value }: { value: string }) {
  const segments = highlightJson(value);
  return (
    <>
      {segments.map((seg, i) => (
        <span key={i} className={seg.className}>
          {seg.text}
        </span>
      ))}
    </>
  );
}

export function ToolCallCard({ tool, input, output, error, isActive, role }: ToolCallCardProps) {
  const [expanded, setExpanded] = useState(false);

  const borderColor = role ? ROLE_BORDER_COLORS[role] : undefined;
  const textColor = role ? ROLE_COLORS[role] : undefined;
  const label = role ? ROLE_LABELS[role] : undefined;
  const IconComponent = role ? ROLE_ICON_MAP[role] : undefined;

  const jsonInput = JSON.stringify(input, null, 2);

  return (
    <div
      className={cn(
        "border rounded-md overflow-hidden",
        error ? "border-destructive/50" : "border-border",
        isActive && "border-primary/50",
        borderColor && "border-l-[3px]",
        borderColor,
      )}
    >
      <button
        className="flex items-center gap-2 w-full px-3 py-2 text-sm hover:bg-muted/30 cursor-pointer"
        onClick={() => setExpanded(!expanded)}
      >
        {expanded ? (
          <ChevronDown className="h-3.5 w-3.5 shrink-0" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5 shrink-0" />
        )}
        <span className="font-mono font-medium text-primary">{tool}</span>

        {role && label && (
          <span className={cn("inline-flex items-center gap-1 text-xs px-1.5 py-0.5 rounded-sm bg-muted/50", textColor)}>
            {IconComponent && <IconComponent className="h-3 w-3" />}
            {label}
          </span>
        )}

        {!isActive &&
          (error ? (
            <XCircle className="h-3.5 w-3.5 text-destructive ml-auto" />
          ) : (
            <CheckCircle className="h-3.5 w-3.5 text-success ml-auto" />
          ))}
        {isActive && <span className="ml-auto text-xs text-muted-foreground animate-pulse">Running...</span>}
      </button>

      {expanded && (
        <div className="border-t border-border">
          <div className="px-3 py-2">
            <p className="text-xs text-muted-foreground mb-1">Input</p>
            <pre className="text-xs font-mono bg-background rounded p-2 overflow-x-auto max-h-40 overflow-y-auto">
              <HighlightedJson value={jsonInput} />
            </pre>
          </div>
          {output != null && (
            <div className="px-3 py-2 border-t border-border/50">
              <p className="text-xs text-muted-foreground mb-1">Output</p>
              <pre
                className={cn(
                  "text-xs font-mono bg-background rounded p-2 overflow-x-auto max-h-60 overflow-y-auto whitespace-pre-wrap",
                  error && "text-destructive",
                )}
              >
                {error ? output : <HighlightedOutput value={output} />}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * Attempt to syntax-highlight output as JSON; fall back to plain text.
 */
function HighlightedOutput({ value }: { value: string }) {
  // Try to detect if the output looks like JSON
  const trimmed = value.trim();
  if ((trimmed.startsWith("{") && trimmed.endsWith("}")) || (trimmed.startsWith("[") && trimmed.endsWith("]"))) {
    try {
      // Re-format for consistent highlighting
      const formatted = JSON.stringify(JSON.parse(trimmed), null, 2);
      return <HighlightedJson value={formatted} />;
    } catch {
      // Not valid JSON, fall through
    }
  }
  return <>{value}</>;
}
