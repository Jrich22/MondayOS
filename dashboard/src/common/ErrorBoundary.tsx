import { Component, type ErrorInfo, type ReactNode } from "react";

/**
 * App-level error boundary. Keeps a thrown render error in one region from
 * taking down the whole operating-system dashboard; shows a recoverable panel
 * instead. Used around the workspace/command regions where adapter-driven data
 * is rendered.
 */

interface Props {
  children: ReactNode;
  label?: string;
  fallback?: ReactNode;
}
interface State {
  error?: Error;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = {};

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // eslint-disable-next-line no-console
    console.error(`[${this.props.label ?? "dashboard"}]`, error, info.componentStack);
  }

  reset = () => this.setState({ error: undefined });

  render() {
    if (this.state.error) {
      if (this.props.fallback) return this.props.fallback;
      return (
        <div className="card m-4 p-6 text-sm">
          <div className="mb-1 font-semibold text-status-blocked">Something broke in {this.props.label ?? "this view"}.</div>
          <div className="mb-3 text-ink-muted">{this.state.error.message}</div>
          <button
            onClick={this.reset}
            className="focus-ring rounded-lg border border-line px-3 py-1.5 text-ink-muted transition hover:text-ink"
          >
            Retry
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
