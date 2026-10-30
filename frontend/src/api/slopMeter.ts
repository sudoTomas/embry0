import { api } from "./client";
import { repoPath } from "./repoPath";

/** A Slop-O-Meter reading (spec 2026-10-26): how much of the code landed beyond human attention. */
export interface SlopMeterReading {
  /** Cumulative slop over cumulative attention cost, 0 to 1. */
  score: number;
  /** 1 Buttered Noodles … 5 Just The Slop. */
  level: number;
  name: string;
  /** A large commit is not judged yet: its lines are left out of the slop until it is. */
  provisional: boolean;
  computed_at: string | null;
}

/** One week of the series: its attention cost, the human capacity behind it, and what exceeded it (weighted lines). */
export interface SlopMeterWeek {
  week: string;
  cost: number;
  capacity: number;
  slop: number;
  lines: number;
  /** The cumulative score at the week's end. */
  score: number;
}

export interface SlopMeter {
  repo: string;
  meter: SlopMeterReading | null;
  areas: { area: string; meter: SlopMeterReading | null }[];
  /** The area the series is of; null for the whole repository. */
  area: string | null;
  weeks: SlopMeterWeek[];
  reading: { commits_read: number; commits_total: number; computed_at: string | null; error: string | null };
}

export async function fetchSlopMeter(fullRepo: string, area = ""): Promise<SlopMeter> {
  const { data } = await api.get<SlopMeter>(repoPath(fullRepo, "slop-meter"), { params: area ? { area } : {} });
  return data;
}
