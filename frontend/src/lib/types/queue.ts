import type { JobResponse } from "./jobs";

export interface QueueResponse {
  depth: number;
  paused: boolean;
  jobs: JobResponse[];
}
