// Contains a render failure in one page or panel, so the rest of the dashboard stays usable.
import { Component } from "react";

import { toVisibleText } from "../case-rows";
import "./ErrorBoundary.css";

import type * as React from "react";

type ErrorBoundaryProps = {
  /** What failed, for the message: "This page could not be shown". */
  subject: string;
  children: React.ReactNode;
};

type ErrorBoundaryState = { error: unknown };

// A reason can quote input text, so it is shown escaped and cut short.
const MAX_REASON_LENGTH = 120;

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: unknown): ErrorBoundaryState {
    return { error };
  }

  render() {
    const { error } = this.state;
    if (error === null) {
      return this.props.children;
    }
    const fullReason = toVisibleText(error instanceof Error ? error.message : String(error));
    const reason =
      fullReason.length > MAX_REASON_LENGTH ? `${fullReason.slice(0, MAX_REASON_LENGTH - 1)}…` : fullReason;
    return (
      <p className="error-boundary" role="alert">
        {`${this.props.subject} could not be shown: ${reason}`}
      </p>
    );
  }
}
