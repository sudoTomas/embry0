import type { CacheHits } from "@/lib/types";

interface Props {
  hits: CacheHits;
}

/**
 * Three-glyph cache-hit indicator: prebaked image / shared volume / turbo remote.
 * 🔥 = hit, · = miss/none. Title attribute exposes the raw counts on hover.
 */
export function CacheHitsRow({ hits }: Props) {
  const image = hits.prebaked_image ? "🔥" : "·";
  const volume = hits.shared_volume ? "🔥" : "·";

  const turboTotal = hits.turbo_remote_hits.length + hits.turbo_remote_misses.length;
  let turbo = "·";
  if (turboTotal > 0) {
    turbo = hits.turbo_remote_hits.length > hits.turbo_remote_misses.length ? "🔥" : "·";
  }

  const title =
    `image: ${hits.prebaked_image ? "hit" : "miss"} · ` +
    `volume: ${hits.shared_volume ? "hit" : "miss"} · ` +
    `turbo: ${hits.turbo_remote_hits.length}/${turboTotal}`;

  return (
    <span className="font-mono text-sm text-white/70" title={title} aria-label={title}>
      {image}
      {volume}
      {turbo}
    </span>
  );
}
