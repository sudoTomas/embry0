import { useState } from "react";
import { ChevronDown, ChevronRight, Brain } from "lucide-react";

interface ThinkingBlockProps {
  blocks: string[];
  isStreaming?: boolean;
}

export function ThinkingBlock({ blocks, isStreaming = false }: ThinkingBlockProps) {
  const [expanded, setExpanded] = useState(false);

  if (blocks.length === 0 && !isStreaming) return null;

  return (
    <div className="border-l-2 border-violet-500/30 bg-violet-500/[0.03]">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-2 px-4 py-2 text-xs text-violet-300/70 hover:text-violet-300 transition-colors"
      >
        <Brain className="w-3 h-3" />
        {isStreaming && !expanded ? (
          <span className="flex items-center gap-2">
            Thinking
            <span className="flex gap-0.5">
              <span className="h-1 w-1 rounded-full bg-violet-400 animate-bounce" style={{ animationDelay: "0ms" }} />
              <span className="h-1 w-1 rounded-full bg-violet-400 animate-bounce" style={{ animationDelay: "150ms" }} />
              <span className="h-1 w-1 rounded-full bg-violet-400 animate-bounce" style={{ animationDelay: "300ms" }} />
            </span>
          </span>
        ) : (
          <span>{blocks.length} thinking block{blocks.length !== 1 ? "s" : ""}</span>
        )}
        <span className="ml-auto">
          {expanded ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
        </span>
      </button>
      {expanded && (
        <div className="px-4 pb-3 space-y-2">
          {blocks.map((text, idx) => (
            <div key={idx} className="text-xs text-white/40 italic leading-relaxed whitespace-pre-wrap max-h-60 overflow-y-auto">
              {text}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
