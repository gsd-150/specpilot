import type { ConnectionState } from "../useRunPolling";
import type { RunStatus } from "../api";

const STATUS_COPY: Record<RunStatus, { title: string; detail: string }> = {
  queued: { title: "Queued", detail: "The run is waiting for a worker." },
  running: { title: "Running", detail: "Agents are producing a sanitized trace." },
  answered: { title: "Answer verified", detail: "The evidence and citation checks passed." },
  refused: { title: "The system declined to answer", detail: "Available evidence did not support a safe answer." },
  egress_blocked: { title: "Disclosure gate blocked the send", detail: "The outbound request was stopped before provider execution." },
  failed: { title: "Provider execution failed", detail: "Infrastructure failed; this is not an evidence-based refusal." },
  interrupted: { title: "Run was interrupted", detail: "The worker lease expired. This run will not be resumed automatically." },
};

const CONNECTION_COPY: Partial<Record<ConnectionState, string>> = {
  poll_timeout: "Polling limit reached. The last server status is preserved.",
  unauthorized: "Session authorization failed.",
  not_found: "Run is unavailable.",
  service_unavailable: "Trace service is unavailable.",
  network_error: "Network connection failed.",
  invalid_response: "Trace response was rejected.",
};

export interface StatusPanelProps {
  status: RunStatus;
  reason: string | null;
  connectionState: ConnectionState;
  onRefresh: () => Promise<void>;
}

export function StatusPanel({ status, reason, connectionState, onRefresh }: StatusPanelProps) {
  const copy = STATUS_COPY[status];
  const connectionCopy = CONNECTION_COPY[connectionState];
  return (
    <section className={`status-panel status-panel--${status}`} data-status={status} role="status" aria-label="Run status">
      <div className="status-panel__heading">
        <div>
          <p className="eyebrow">Server status</p>
          <h2>{copy.title}</h2>
        </div>
        <span className="status-chip">{status}</span>
      </div>
      <p>{copy.detail}</p>
      {reason === null ? null : <p className="stable-code"><span>Reason</span><code>{reason}</code></p>}
      {connectionCopy === undefined ? null : (
        <div className="connection-alert" role="alert">
          <span>{connectionCopy}</span>
          <button className="button button--quiet" type="button" onClick={() => void onRefresh()}>Refresh trace</button>
        </div>
      )}
    </section>
  );
}
