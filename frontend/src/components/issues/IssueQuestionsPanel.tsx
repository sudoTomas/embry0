import { useState } from "react";
import { toast } from "sonner";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Textarea } from "@/components/ui/Textarea";
import { AgentIndicator } from "@/components/issues/AgentIndicator";
import { useIssueInputs, useAnswerIssueInput } from "@/hooks/useIssueInputs";
import { formatDate } from "@/lib/utils";
import type { InputResponse } from "@/lib/types";

interface IssueQuestionsPanelProps {
  issueId: string;
}

function QuestionRow({ input, issueId }: { input: InputResponse; issueId: string }) {
  const [answerText, setAnswerText] = useState("");
  const answerMutation = useAnswerIssueInput();

  const handleSubmit = () => {
    if (!answerText.trim()) return;
    answerMutation.mutate(
      { issueId, inputId: input.id, answer: answerText.trim() },
      {
        onSuccess: () => {
          toast.success("Answer submitted");
          setAnswerText("");
        },
        onError: (e) => toast.error(e instanceof Error ? e.message : "Failed to submit answer"),
      },
    );
  };

  return (
    <div className="border border-white/[0.06] rounded-lg p-4 space-y-3">
      {/* Importance badge + question */}
      <div className="flex items-start gap-3">
        {input.importance === "blocking" ? (
          <span className="shrink-0 inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-red-500/20 text-red-300 border border-red-500/30">
            blocking
          </span>
        ) : (
          <span className="shrink-0 inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-zinc-500/20 text-zinc-400 border border-zinc-500/30">
            auto
          </span>
        )}
        <p className="text-sm text-foreground/90 leading-relaxed">{input.question}</p>
      </div>

      {/* Response area */}
      {input.status === "pending" && input.importance === "blocking" && (
        <div className="space-y-2 pl-0">
          <Textarea
            value={answerText}
            onChange={(e) => setAnswerText(e.target.value)}
            placeholder="Type your answer…"
            className="min-h-[72px] text-sm"
          />
          <Button
            size="sm"
            onClick={handleSubmit}
            disabled={!answerText.trim() || answerMutation.isPending}
          >
            {answerMutation.isPending ? "Submitting…" : "Submit Answer"}
          </Button>
        </div>
      )}

      {input.status === "answered" && (
        <div className="pl-0 space-y-1">
          <p className="text-sm text-foreground/80">{input.answer}</p>
          <p className="text-xs text-muted-foreground">
            Answered by {input.answered_by ?? "user"}
            {input.answered_at ? ` · ${formatDate(input.answered_at)}` : ""}
          </p>
        </div>
      )}

      {input.status === "auto_answered" && (
        <p className="text-sm italic text-muted-foreground">
          Auto-answered: {input.auto_answer}
        </p>
      )}
    </div>
  );
}

export function IssueQuestionsPanel({ issueId }: IssueQuestionsPanelProps) {
  const { data: inputs } = useIssueInputs(issueId);

  if (!inputs || inputs.length === 0) return null;

  const total = inputs.length;
  const answered = inputs.filter((i) => i.status !== "pending").length;
  const waiting = inputs.filter((i) => i.status === "pending" && i.importance === "blocking").length;

  // Find the asking node for the header indicator
  const askingNode = inputs[0]?.asking_node;

  return (
    <Card className="border-amber-500/40 bg-amber-500/[0.03]">
      <CardHeader className="pb-3">
        <div className="flex items-center gap-3">
          <CardTitle className="text-base text-amber-300">
            Pipeline needs more information
          </CardTitle>
          {askingNode && <AgentIndicator agentType={askingNode} size="sm" />}
        </div>
        <p className="text-sm text-muted-foreground">
          {answered} of {total} question{total !== 1 ? "s" : ""} answered
          {waiting > 0 && ` — waiting for ${waiting} more`}
        </p>
      </CardHeader>
      <CardContent className="space-y-3">
        {inputs.map((input) => (
          <QuestionRow key={input.id} input={input} issueId={issueId} />
        ))}
      </CardContent>
    </Card>
  );
}
