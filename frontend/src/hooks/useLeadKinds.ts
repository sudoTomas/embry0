import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { isAxiosError } from "axios";
import { fetchLeadKinds, type LeadKind } from "@/api/leadKinds";
import { kindsById } from "@/lib/world";

/** Its own key, outside "world": the guild's reads invalidate everything under "world", and the catalogue changes only with a release. */
export const LEAD_KINDS_KEY = ["lead-kinds"] as const;

/** How many times a failed read is tried again before the page names kinds by their ids. */
export const LEAD_KINDS_RETRIES = 2;

/** Before each try again: one second, then two. */
export function leadKindsRetryDelay(attempt: number): number {
  return Math.min(1_000 * 2 ** attempt, 8_000);
}

/** A read worth trying again: anything but a backend that has no such route (404), which a retry cannot change. */
export function retryLeadKinds(failures: number, error: unknown): boolean {
  if (isAxiosError(error) && error.response?.status === 404) return false;
  return failures < LEAD_KINDS_RETRIES;
}

/**
 * The lead-kind catalogue by id (GET /lead-kinds), read once per visit and
 * kept: it changes only with a release. The browser asks again on each
 * reload with the ETag it holds, so a deploy's new kinds show on the first
 * reload after it, at the price of a 304 otherwise. Empty until it is read,
 * and on an older backend that does not serve it, so each kind reads as its
 * own id.
 */
export function useLeadKinds(): ReadonlyMap<string, LeadKind> {
  const { data } = useQuery({
    queryKey: LEAD_KINDS_KEY,
    queryFn: fetchLeadKinds,
    staleTime: Infinity,
    gcTime: Infinity,
    retry: retryLeadKinds,
    retryDelay: leadKindsRetryDelay,
  });
  return useMemo(() => kindsById(data), [data]);
}
