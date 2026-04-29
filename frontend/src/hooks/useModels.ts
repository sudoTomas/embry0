import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";

interface ModelsConfig {
  heavy: string;
  medium: string;
  light: string;
}

const FALLBACK_MODELS = [
  "claude-opus-4-7",
  "claude-sonnet-4-6",
  "claude-haiku-4-5",
];

export function useModels(): { models: string[]; isLoading: boolean } {
  const { data, isLoading } = useQuery({
    queryKey: ["config", "models"],
    queryFn: async () => {
      const { data } = await api.get<ModelsConfig>("/config/models");
      return data;
    },
    staleTime: 5 * 60 * 1000, // 5 minutes — model config rarely changes
    retry: 1,
  });

  const models = data
    ? [data.heavy, data.medium, data.light]
    : FALLBACK_MODELS;

  return { models, isLoading };
}
