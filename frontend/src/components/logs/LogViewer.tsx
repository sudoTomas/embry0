import { CostTracker } from "./CostTracker";
import { ConversationView } from "./ConversationView";
import { StructuredView } from "./StructuredView";
import { RawView } from "./RawView";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/Tabs";
import type { LogEvent } from "@/lib/types";

interface LogViewerProps {
  events: LogEvent[];
  isConnected: boolean;
  isComplete: boolean;
  costUsd: number;
  tokensIn: number;
  tokensOut: number;
  turns: number;
  budgetUsd?: number;
}

export function LogViewer({
  events,
  isConnected,
  isComplete,
  costUsd,
  tokensIn,
  tokensOut,
  turns,
  budgetUsd,
}: LogViewerProps) {
  return (
    <div className="space-y-4">
      <CostTracker
        costUsd={costUsd}
        tokensIn={tokensIn}
        tokensOut={tokensOut}
        turns={turns}
        isComplete={isComplete}
        budgetUsd={budgetUsd}
      />

      {!isConnected && !isComplete && (
        <div className="text-sm text-warning">Connecting to log stream...</div>
      )}

      <Tabs defaultValue="conversation">
        <TabsList>
          <TabsTrigger value="conversation">Conversation</TabsTrigger>
          <TabsTrigger value="structured">Structured</TabsTrigger>
          <TabsTrigger value="raw">Raw</TabsTrigger>
        </TabsList>
        <TabsContent value="conversation">
          <ConversationView events={events} />
        </TabsContent>
        <TabsContent value="structured">
          <StructuredView events={events} />
        </TabsContent>
        <TabsContent value="raw">
          <RawView events={events} />
        </TabsContent>
      </Tabs>
    </div>
  );
}
