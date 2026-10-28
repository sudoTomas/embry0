import { useEffect, useState } from "react";
import type { RecentEvent } from "@/api/progression";
import { useIdentityLookup } from "@/hooks/useAgentIdentity";
import { useProgression } from "@/hooks/useProgression";
import { gamePackOf } from "@/lib/gamePack";
import { chatMessage, freshEvents, readSeen, writeSeen } from "./chatbox";

/** How long one message stays before the next. */
export const MESSAGE_MS = 6_000;

/**
 * Levels reached and achievements unlocked, as one-line messages in the
 * content pack's words in the chat box, bottom left, one at a time. A browser's first visit marks
 * everything so far as seen, so an operator is told what happens from then
 * on, not the whole history.
 */
export function Chatbox() {
  const { data } = useProgression();
  const lookup = useIdentityLookup();
  const [queue, setQueue] = useState<RecentEvent[]>([]);
  const [current, setCurrent] = useState<RecentEvent | null>(null);

  useEffect(() => {
    // Without the pack's words (a backend older than the pack) there is nothing to say: show nothing and
    // mark nothing seen, so the messages wait for an answer that has them.
    if (!data?.messages) return;
    const newest = data.recent[0]?.at ?? data.computed_at;
    const seen = readSeen();
    if (seen !== null) {
      const fresh = freshEvents(data.recent, seen);
      if (fresh.length) setQueue((q) => [...q, ...fresh]);
    }
    if (seen === null || Date.parse(newest) > Date.parse(seen)) writeSeen(newest);
  }, [data]);

  useEffect(() => {
    if (current || queue.length === 0) return;
    setCurrent(queue[0]);
    setQueue((q) => q.slice(1));
  }, [queue, current]);

  useEffect(() => {
    if (!current) return;
    const timer = window.setTimeout(() => setCurrent(null), MESSAGE_MS);
    return () => window.clearTimeout(timer);
  }, [current]);

  if (!current || !data?.messages) return null;
  const names = new Map(data.skills.map((s) => [s.id, s.name]));
  return (
    <div
      role="status"
      aria-live="polite"
      data-testid="chatbox-message"
      className="pointer-events-none fixed bottom-4 left-4 z-50 max-w-sm rounded-sm border-2 border-[#5d5447] bg-[#d8cba4] px-3 py-2 text-sm text-[#1a1208] shadow-lg"
    >
      {chatMessage(
        current,
        gamePackOf(data),
        (stat) => names.get(stat) ?? stat,
        (agentType) => lookup(agentType).displayName,
      )}
    </div>
  );
}
