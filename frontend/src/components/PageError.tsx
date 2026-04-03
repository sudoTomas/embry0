import { Button } from "@/components/ui/Button";
import { AlertCircle } from "lucide-react";

interface PageErrorProps {
  message?: string;
  onRetry?: () => void;
}

export function PageError({
  message = "Failed to load data",
  onRetry,
}: PageErrorProps) {
  return (
    <div className="flex flex-col items-center justify-center py-12 space-y-4">
      <AlertCircle className="h-10 w-10 text-destructive" />
      <p className="text-muted-foreground text-sm">{message}</p>
      {onRetry && (
        <Button variant="outline" size="sm" onClick={onRetry}>
          Retry
        </Button>
      )}
    </div>
  );
}
