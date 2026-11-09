import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchAuditActions } from "@/api/audit";
import { auditVocabulary, type AuditVocabulary } from "@/lib/auditLabels";

/** Its own key, outside "audit": the log's reads refetch every 30 s, and the registry changes only with a release. */
export const AUDIT_ACTIONS_KEY = ["audit-actions"] as const;

export interface AuditActions {
  vocabulary: AuditVocabulary;
  /** The registry could not be read: the vocabulary is the fallback, sentence case and no filter options. */
  isError: boolean;
  refetch: () => void;
}

/**
 * The audit action registry (GET /audit/actions), read once per visit and
 * kept, as the vocabulary made from it: the Audit filter's options and each
 * action's label and icon. Until it is read, and if it cannot be, every
 * action reads in sentence case and the filter offers none.
 */
export function useAuditActions(): AuditActions {
  const { data, isError, refetch } = useQuery({
    queryKey: AUDIT_ACTIONS_KEY,
    queryFn: fetchAuditActions,
    staleTime: Infinity,
    gcTime: Infinity,
  });
  const vocabulary = useMemo(() => auditVocabulary(data), [data]);
  return { vocabulary, isError, refetch: () => void refetch() };
}
