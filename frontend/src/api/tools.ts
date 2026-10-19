import { api } from "./client";
import type { ToolCatalogEntry } from "@/lib/types";

/** `GET /tools`: the tool names an agent definition can name (docs/api.md, Tools). */
export async function fetchTools(): Promise<ToolCatalogEntry[]> {
  const { data } = await api.get<ToolCatalogEntry[]>("/tools");
  return data;
}
