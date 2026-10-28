import type { PackProblemLine } from "@/lib/progressionPack";

/** Every problem of a refused pack, by key. Each message is text, never markup. */
export function PackProblems({
  problems,
  title = "The pack was refused. Fix these, then check again:",
  testId = "pack-problems",
}: {
  problems: PackProblemLine[];
  title?: string;
  testId?: string;
}) {
  return (
    <div
      role="alert"
      data-testid={testId}
      className="space-y-1 rounded-lg border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive"
    >
      <p className="font-semibold">{title}</p>
      <ul className="list-disc space-y-0.5 pl-5 [overflow-wrap:anywhere]">
        {problems.map((problem, index) => (
          <li key={index} data-testid="pack-problem">
            {problem.key && <code className="font-mono">{problem.key}</code>}
            {problem.key ? `: ${problem.msg}` : problem.msg}
          </li>
        ))}
      </ul>
    </div>
  );
}
