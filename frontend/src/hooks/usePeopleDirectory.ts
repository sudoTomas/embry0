import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createPerson,
  fetchDirectory,
  fetchSuggestions,
  removePerson,
  updatePerson,
  type DirectoryPerson,
  type PersonWrite,
} from "@/api/people";

/** The people directory (#179). Not ["people"], the hand-off list's key (useDecisions.usePeople). */
const DIRECTORY = "people-directory";
const SUGGESTIONS = "people-suggestions";

export function usePeopleDirectory(includeDisabled: boolean) {
  return useQuery({ queryKey: [DIRECTORY, includeDisabled], queryFn: () => fetchDirectory(includeDisabled) });
}

export function usePeopleSuggestions() {
  return useQuery({ queryKey: [SUGGESTIONS], queryFn: fetchSuggestions });
}

/** A write changes the directory, who is left to add, and whom a decision can be handed to by login. */
function invalidatePeople(qc: ReturnType<typeof useQueryClient>) {
  return Promise.all([
    qc.invalidateQueries({ queryKey: [DIRECTORY] }),
    qc.invalidateQueries({ queryKey: [SUGGESTIONS] }),
    qc.invalidateQueries({ queryKey: ["people"] }),
  ]);
}

export function useCreatePerson() {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (body: PersonWrite) => createPerson(body), onSuccess: () => invalidatePeople(qc) });
}

/** A change refused as stale (409) reads the directory again, so the next try starts from what is stored. */
export function useUpdatePerson() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ person, body }: { person: DirectoryPerson; body: PersonWrite }) => updatePerson(person, body),
    onSettled: () => invalidatePeople(qc),
  });
}

export function useRemovePerson() {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (personId: string) => removePerson(personId), onSuccess: () => invalidatePeople(qc) });
}
