import { useCallback, useEffect, useRef, useState } from "react";

import { getRun, type RunView, type TerminalStatus } from "./api";

const TERMINAL = new Set<TerminalStatus>([
  "answered",
  "refused",
  "egress_blocked",
  "failed",
  "interrupted",
]);

export type ConnectionState =
  | "connecting"
  | "connected"
  | "poll_timeout"
  | "unauthorized"
  | "not_found"
  | "service_unavailable"
  | "network_error"
  | "invalid_response";

export interface RunPollingOptions {
  runId: string;
  token?: string;
  intervalMs: number;
  deadlineMs: number;
}

export interface RunPollingResult {
  serverRun: RunView | null;
  connectionState: ConnectionState;
  refresh: () => Promise<void>;
}

function errorState(error: unknown): ConnectionState {
  const message = error instanceof Error ? error.message : "";
  if (message === "run request failed (401)") return "unauthorized";
  if (message === "run request failed (404)") return "not_found";
  if (message === "run request failed (503)") return "service_unavailable";
  if (message === "run request unavailable") return "network_error";
  return "invalid_response";
}

export function useRunPolling({
  runId,
  token,
  intervalMs,
  deadlineMs,
}: RunPollingOptions): RunPollingResult {
  const [serverRun, setServerRun] = useState<RunView | null>(null);
  const [connectionState, setConnectionState] = useState<ConnectionState>("connecting");
  const refreshRef = useRef<() => Promise<void>>(async () => undefined);

  useEffect(() => {
    let active = true;
    let expired = false;
    let terminal = false;
    let pollTimer: ReturnType<typeof setTimeout> | undefined;
    let requestTimer: ReturnType<typeof setTimeout> | undefined;
    let deadlineTimer: ReturnType<typeof setTimeout> | undefined;
    let controller: AbortController | undefined;
    let inFlight: Promise<void> | undefined;

    setServerRun(null);
    setConnectionState("connecting");

    const clearPollTimer = (): void => {
      if (pollTimer !== undefined) clearTimeout(pollTimer);
      pollTimer = undefined;
    };

    const execute = (manual: boolean): Promise<void> => {
      if (!active) return Promise.resolve();
      if (inFlight !== undefined) {
        return manual
          ? inFlight.then(() => active ? execute(true) : undefined)
          : inFlight;
      }

      controller = new AbortController();
      const requestController = controller;
      if (manual && expired) {
        requestTimer = setTimeout(() => {
          requestController.abort();
          if (active) setConnectionState("poll_timeout");
        }, deadlineMs);
      }

      const request = getRun(runId, { token, signal: requestController.signal })
        .then((run) => {
          if (!active || requestController.signal.aborted) return;
          setServerRun(run);
          setConnectionState("connected");
          terminal = TERMINAL.has(run.status as TerminalStatus);
          if (terminal) {
            clearPollTimer();
            if (deadlineTimer !== undefined) clearTimeout(deadlineTimer);
            deadlineTimer = undefined;
          }
        })
        .catch((error: unknown) => {
          if (!active || requestController.signal.aborted) return;
          setConnectionState(errorState(error));
        })
        .finally(() => {
          if (requestTimer !== undefined) clearTimeout(requestTimer);
          requestTimer = undefined;
          if (controller === requestController) controller = undefined;
          if (inFlight === request) inFlight = undefined;
          if (!manual && active && !expired && !terminal) {
            pollTimer = setTimeout(() => {
              pollTimer = undefined;
              void execute(false);
            }, intervalMs);
          }
        });
      inFlight = request;
      return request;
    };

    refreshRef.current = () => execute(true);
    deadlineTimer = setTimeout(() => {
      expired = true;
      clearPollTimer();
      controller?.abort();
      if (active && !terminal) setConnectionState("poll_timeout");
    }, deadlineMs);
    void execute(false);

    return () => {
      active = false;
      refreshRef.current = async () => undefined;
      if (deadlineTimer !== undefined) clearTimeout(deadlineTimer);
      clearPollTimer();
      if (requestTimer !== undefined) clearTimeout(requestTimer);
      controller?.abort();
    };
  }, [deadlineMs, intervalMs, runId, token]);

  const refresh = useCallback(() => refreshRef.current(), []);
  return { serverRun, connectionState, refresh };
}
