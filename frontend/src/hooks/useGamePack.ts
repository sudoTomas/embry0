import { useMemo } from "react";
import { useProgression } from "@/hooks/useProgression";
import { gamePackOf, type GamePack } from "@/lib/gamePack";

/** The active content pack's names, colours and words, from the app-wide progression read; null until it answers. */
export function useGamePack(): GamePack | null {
  const { data } = useProgression();
  return useMemo(() => (data ? gamePackOf(data) : null), [data]);
}
