/**
 * How a job is named in a list. Jobs carry no title of their own, and a
 * watcher tick's task is a long log briefing whose first words are the same
 * for every tick, so a list showing the task told no two ticks apart.
 */

/** The 8 hex characters that tell jobs apart: job-fd155299c8a4 -> fd155299. */
export function shortJobId(jobId: string): string {
  return jobId.replace(/^job-/, "").slice(0, 8);
}

// The first line of the watcher's analysis task (_ANALYSIS_TASK_TEMPLATE in
// embry0/services/watcher.py; a test pins this to it). A brief list cuts the
// line at 200 characters, so the line count may be missing.
const WATCHER_TICK = /^Analyze the following production log excerpts from the (\S+) stack\b(?:.*?\b(\d+) matching lines\b)?/;

/**
 * A watcher tick reads "Watcher tick, <repo name>, <n> lines"; any other job
 * reads as its task's first line, which for an issue's job is the issue's
 * title.
 */
export function jobTitle(task: string): string {
  const first = task.split("\n", 1)[0].trim();
  const tick = WATCHER_TICK.exec(first);
  if (tick) {
    const name = tick[1].split("/").pop();
    if (!tick[2]) return `Watcher tick, ${name}`;
    return `Watcher tick, ${name}, ${tick[2]} ${tick[2] === "1" ? "line" : "lines"}`;
  }
  return first.replace(/^#+\s*/, "") || task.trim();
}
