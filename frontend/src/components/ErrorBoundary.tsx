import { Component, type ErrorInfo, type ReactNode } from "react";
import { Button } from "@/components/ui/Button";
import { AlertTriangle } from "lucide-react";

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
  inline?: boolean;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  private retryCount = 0;

  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("ErrorBoundary caught:", error, info.componentStack);
  }

  handleReset = () => {
    this.retryCount += 1;
    if (this.retryCount >= 3) {
      window.location.reload();
      return;
    }
    this.setState({ hasError: false, error: null });
  };

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) return this.props.fallback;
      if (this.props.inline) {
        return (
          <div className="flex flex-col items-center justify-center py-20 text-center">
            <AlertTriangle size={40} className="text-amber-400/50 mb-4" />
            <p className="text-white/40 text-sm font-medium">Something went wrong</p>
            <p className="text-white/20 text-xs mt-1 max-w-md">{this.state.error?.message}</p>
            <button
              onClick={() => this.setState({ hasError: false, error: null })}
              className="mt-4 text-xs text-blue-400 hover:text-blue-300"
            >
              Try again
            </button>
          </div>
        );
      }
      return (
        <div className="flex h-screen items-center justify-center bg-background text-foreground">
          <div className="mx-auto max-w-md text-center space-y-4">
            <AlertTriangle className="h-12 w-12 text-destructive mx-auto" />
            <h1 className="text-2xl font-bold">Something went wrong</h1>
            <p className="text-muted-foreground">
              An unexpected error occurred. You can try reloading the page or resetting the app state.
            </p>
            {this.state.error && (
              <pre className="text-xs text-destructive bg-destructive/5 rounded-md p-3 overflow-auto max-h-32 text-left">
                {this.state.error.message}
              </pre>
            )}
            <div className="flex gap-3 justify-center">
              <Button variant="outline" onClick={() => window.location.reload()}>
                Reload Page
              </Button>
              <Button onClick={this.handleReset}>Try Again</Button>
            </div>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}
