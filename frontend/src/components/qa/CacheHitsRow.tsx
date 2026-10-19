import type { CacheHits } from "@/lib/types";

interface Props {
  hits: CacheHits;
}

/**
 * The run's three caches in words: prebaked image, shared volume and turbo
 * remote (hits out of lookups). Emoji used to mark a hit and drew empty boxes
 * on a machine without a colour emoji font.
 */
export function CacheHitsRow({ hits }: Props) {
  const turboTotal = hits.turbo_remote_hits.length + hits.turbo_remote_misses.length;
  const turbo = turboTotal > 0 ? `turbo ${hits.turbo_remote_hits.length}/${turboTotal}` : "turbo none";
  const text =
    `Cache: image ${hits.prebaked_image ? "hit" : "miss"} · ` +
    `volume ${hits.shared_volume ? "hit" : "miss"} · ${turbo}`;

  return <span className="text-xs text-fg-muted">{text}</span>;
}
