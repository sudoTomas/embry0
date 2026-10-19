import { useLayoutEffect, useRef, type CSSProperties, type RefObject } from "react";
import { GitPullRequest } from "lucide-react";
import type { AgentState } from "@/hooks/useAgentStates";
import type { PipelineGraph } from "@/lib/types";
import { useIsNarrow } from "@/hooks/useIsNarrow";
import { prefersReducedMotion } from "@/hooks/useReducedMotion";
import { isTerminalStatus } from "@/lib/jobStatus";
import { EASE_TRAVEL, motionMs } from "@/lib/motion";
import { inPipelineOrder } from "@/lib/pipelineOrder";
import { cn } from "@/lib/utils";

/**
 * Where a stop on the line stands. `flight` is the stage a running job is in
 * (the cream dot: life); `held` is the stage a job waits at for a person.
 */
type StopState = "done" | "failed" | "flight" | "held" | "pending" | "pr";

interface Stop {
  key: string;
  label: string;
  /** The label is a graph node id, which reads as code. */
  mono: boolean;
  state: StopState;
}

/** The job statuses that wait on a person. */
const HELD_STATUSES = new Set(["awaiting_input", "paused"]);

/** The drawn line is the job, so it takes the colour of the job's status. */
const LINE_TONE: Record<string, string> = {
  running: "var(--color-info)",
  awaiting_input: "var(--color-warning)",
  paused: "var(--color-warning)",
  completed: "var(--color-success)",
  pr_merged: "var(--color-success)",
};

const SPOKEN: Record<StopState, string> = {
  done: "done",
  failed: "failed",
  flight: "running",
  held: "waiting for a person",
  pending: "not started",
  pr: "opened",
};

/** Each stop's name: its agent, or its graph step where one agent runs several (onboarding's). */
function stageNames(agents: AgentState[]): string[] {
  const nodesOf = new Map<string, Set<string>>();
  for (const a of agents) nodesOf.set(a.agent, (nodesOf.get(a.agent) ?? new Set()).add(a.node));
  return agents.map((a) => ((nodesOf.get(a.agent)?.size ?? 0) > 1 ? a.node : a.agent));
}

function lineOf(agents: AgentState[], graph: PipelineGraph | null, status: string, prUrl: string | null) {
  const ordered = inPipelineOrder(agents, graph);
  const names = stageNames(ordered);
  const held = HELD_STATUSES.has(status);
  const stops: Stop[] = ordered.map((a, i) => ({
    key: a.runId,
    label: names[i],
    mono: names[i] !== a.agent,
    state: a.status === "running" ? (held ? "held" : "flight") : a.status === "completed" ? "done" : a.status,
  }));
  const terminal = isTerminalStatus(status);
  if (terminal && prUrl && stops.length > 0) stops.push({ key: "pr", label: "pull request", mono: false, state: "pr" });

  let reach = -1;
  stops.forEach((s, i) => {
    if (s.state !== "pending") reach = i;
  });
  // A job waiting on a person with no stage running waits where it got to.
  if (held && reach >= 0 && !stops.some((s) => s.state === "held")) stops[reach] = { ...stops[reach], state: "held" };
  // Until the job's graph names the stages ahead, the way on is open.
  const open = !terminal && !(graph && graph.nodes.length > 0);
  const current = stops.findIndex((s) => s.state === "flight" || s.state === "held");
  return { stops, open, reach, focus: current >= 0 ? current : Math.max(reach, 0) };
}

/** A draw of the line in progress, from and to as parts of its length. */
interface Draw {
  animation: Animation;
  from: number;
  to: number;
}

/**
 * Where a draw has the line now, or null once it has ended. The animation's
 * progress is eased already, so it is where the line is on screen.
 */
function drawnNow(draw: Draw | null): number | null {
  if (!draw) return null;
  const progress = draw.animation.effect?.getComputedTiming().progress;
  return progress == null ? null : draw.from + (draw.to - draw.from) * progress;
}

/**
 * Draws the line to `to` (0 to 1 of its length): from the start when there is
 * first something to draw, from where it stands when a stage lands. A stage
 * can land while the line is still drawing, so the new draw starts where the
 * running one has the line, not where it was going. The element's own style
 * already holds the end, so reduced motion, or an animation that never runs,
 * leaves the line drawn.
 */
function useDrawTo(ref: RefObject<HTMLElement | null>, to: number) {
  const target = useRef(0);
  const running = useRef<Draw | null>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    const last = target.current;
    target.current = to;
    if (!el || last === to || prefersReducedMotion() || typeof el.animate !== "function") return;
    const start = drawnNow(running.current) ?? last;
    running.current?.animation.cancel();
    const animation = el.animate([{ transform: `scaleX(${start})` }, { transform: `scaleX(${to})` }], {
      duration: start === 0 ? motionMs(el, "--motion-draw", 1400) : motionMs(el, "--motion-slow", 560),
      easing: EASE_TRAVEL,
    });
    running.current = { animation, from: start, to };
  }, [ref, to]);
}

/**
 * Where a stop's name sits under it (offsets from the stop, in a box as wide
 * as the line). On a wide screen every stop is named and no name reaches its
 * neighbour's or past the line's ends; on a phone only one is, kept inside.
 */
function namePlace(at: number, i: number, last: number, step: number, narrow: boolean): CSSProperties {
  const start = { left: -6 };
  const end = { left: 6, transform: "translateX(-100%)" };
  const middle = { left: 0, transform: "translateX(-50%)" };
  if (narrow) return { ...(at < 30 ? start : at > 70 ? end : middle), maxWidth: "60%" };
  if (last === 0) return { ...start, maxWidth: "100%" };
  if (i === 0) return { ...start, maxWidth: `${step / 2}%` };
  if (i === last) return { ...end, maxWidth: `${step / 2}%` };
  return { ...middle, maxWidth: `calc(${step}% - 8px)` };
}

function StopMark({ state }: { state: StopState }) {
  switch (state) {
    case "flight":
      return <span className="motion-breathe h-2.5 w-2.5 rounded-full bg-cream" />;
    case "held":
      return <span className="h-2.5 w-2.5 rounded-full bg-warning" />;
    case "done":
      return <span className="h-2 w-2 rounded-full bg-success" />;
    case "failed":
      return <span className="h-2 w-2 rounded-full bg-destructive" />;
    case "pending":
      return <span className="h-2 w-2 rounded-full border border-white/25" />;
    case "pr":
      return <GitPullRequest className="h-3 w-3 text-purple" />;
  }
}

interface JobLineProps {
  agents: AgentState[];
  graph: PipelineGraph | null;
  status: string;
  prUrl: string | null;
}

/**
 * The job's journey as one line: a stop per stage in the order the job
 * travels them, the line drawn through the stages it has reached, the cream
 * dot breathing at the stage a running job is in, and a last stop for the
 * pull request a finished job opened.
 */
export function JobLine({ agents, graph, status, prUrl }: JobLineProps) {
  const narrow = useIsNarrow();
  const { stops, open, reach, focus } = lineOf(agents, graph, status, prUrl);
  const last = stops.length + (open ? 1 : 0) - 1;
  /** The distance between two stops, in % of the line. */
  const step = last > 0 ? 100 / last : 0;
  const drawn = reach > 0 ? reach / last : 0;
  const fillRef = useRef<HTMLDivElement>(null);
  useDrawTo(fillRef, drawn);

  return (
    <div data-testid="job-line" className="overflow-hidden px-2">
      <div className="relative h-7">
        {last > 0 && <div aria-hidden className="absolute inset-x-0 top-[5px] border-t-2 border-dashed border-white/[0.12]" />}
        <div
          ref={fillRef}
          aria-hidden
          data-testid="job-progress-fill"
          className="absolute inset-x-0 top-[5px] h-[2px] origin-left"
          style={{ background: LINE_TONE[status] ?? "var(--color-fg-muted)", transform: `scaleX(${drawn})` }}
        />
        <ol aria-label="Stages" className="absolute inset-0">
          {/* A stop is a box as wide as the line, moved along it by
              transform, so a stop that shifts when a stage joins glides. */}
          {stops.map((stop, i) => (
            <li
              key={stop.key}
              data-testid="job-line-stop"
              data-state={stop.state}
              className="job-line-stop pointer-events-none absolute inset-x-0 top-0"
              style={{ transform: `translateX(${i * step}%)` }}
            >
              <span aria-hidden className="absolute left-0 top-0 grid h-3 w-3 -translate-x-1/2 place-items-center rounded-full bg-page">
                <StopMark state={stop.state} />
              </span>
              <span className="sr-only">
                {stop.label}, {SPOKEN[stop.state]}
              </span>
              {(!narrow || i === focus) && (
                <span
                  aria-hidden
                  title={stop.label}
                  className={cn(
                    "pointer-events-auto absolute top-[15px] truncate text-[10px] leading-3",
                    stop.mono && "font-mono",
                    i === focus ? "text-fg-secondary" : stop.state === "pending" ? "text-white/30" : "text-fg-muted",
                  )}
                  style={namePlace(i * step, i, last, step, narrow)}
                >
                  {stop.label}
                </span>
              )}
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}
