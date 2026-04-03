import { Link } from "react-router";
import { Button } from "@/components/ui/Button";
import { FileQuestion } from "lucide-react";

export function NotFoundPage() {
  return (
    <div className="flex flex-1 items-center justify-center">
      <div className="mx-auto max-w-md text-center space-y-4">
        <FileQuestion className="h-16 w-16 text-muted-foreground mx-auto" />
        <h1 className="text-4xl font-bold">404</h1>
        <p className="text-muted-foreground">
          The page you are looking for does not exist or has been moved.
        </p>
        <Link to="/">
          <Button>Back to Dashboard</Button>
        </Link>
      </div>
    </div>
  );
}
