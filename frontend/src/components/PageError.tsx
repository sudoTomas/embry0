import { Link } from "react-router";
import { Button } from "@/components/ui/Button";
import { AlertCircle } from "lucide-react";

interface PageErrorProps {
  message?: string;
  onRetry?: () => void;
  /** A way back, for a record that does not exist (where Retry cannot help). */
  back?: { to: string; label: string };
}

export function PageError({
  message = "Failed to load data",
  onRetry,
  back,
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
      {back && (
        <Link to={back.to} className="text-sm text-white/60 hover:underline">
          {back.label}
        </Link>
      )}
    </div>
  );
}
