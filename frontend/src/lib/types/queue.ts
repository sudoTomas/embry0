export interface QueueResponse {
  depth: number;
  pending: number;
  running: number;
  awaiting_input: number;
  paused: number;
}
