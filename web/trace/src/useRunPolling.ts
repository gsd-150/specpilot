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
  | "invalid_response"
  | "stream_unavailable";

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
  const activationKey = JSON.stringify([runId, token ?? null]);
  const [serverState, setServerState] = useState<{
    key: string;
    run: RunView | null;
  }>({ key: activationKey, run: null });
  const [connection, setConnection] = useState<{
    key: string;
    state: ConnectionState;
  }>({ key: activationKey, state: "connecting" });
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
    let queuedManual: Promise<void> | undefined;
    const deadlineAt = performance.now() + deadlineMs;

    setServerState({ key: activationKey, run: null });
    setConnection({ key: activationKey, state: "connecting" });

    const clearPollTimer = (): void => {
      if (pollTimer !== undefined) clearTimeout(pollTimer);
      pollTimer = undefined;
    };

    const scheduleAuto = (): void => {
      clearPollTimer();
      if (!active || expired || terminal || queuedManual !== undefined) return;
      const remaining = deadlineAt - performance.now();
      if (remaining <= 0) return;
      pollTimer = setTimeout(() => {
        pollTimer = undefined;
        void execute(false);
      }, Math.min(intervalMs, remaining));
    };

    const execute = (manual: boolean): Promise<void> => {
      if (!active) return Promise.resolve();
      if (inFlight !== undefined) {
        if (!manual) return inFlight;
        if (queuedManual !== undefined) return queuedManual;
        const predecessor = inFlight;
        const queued = predecessor
          .then(() => active ? execute(true) : undefined)
          .finally(() => {
            if (queuedManual === queued) queuedManual = undefined;
            scheduleAuto();
          });
        queuedManual = queued;
        return queued;
      }

      if (manual) clearPollTimer();
      controller = new AbortController();
      const requestController = controller;
      if (manual) {
        const remaining = expired || terminal
          ? deadlineMs
          : Math.max(0, deadlineAt - performance.now());
        requestTimer = setTimeout(() => {
          requestController.abort();
          if (active) setConnection({ key: activationKey, state: "poll_timeout" });
        }, remaining);
      }

      const request = getRun(runId, { token, signal: requestController.signal })
        .then((run) => {
          if (!active || requestController.signal.aborted) return;
          setServerState({ key: activationKey, run });
          setConnection({ key: activationKey, state: "connected" });
          terminal = terminal || TERMINAL.has(run.status as TerminalStatus);
          if (TERMINAL.has(run.status as TerminalStatus)) {
            clearPollTimer();
            if (deadlineTimer !== undefined) clearTimeout(deadlineTimer);
            deadlineTimer = undefined;
          }
        })
        .catch((error: unknown) => {
          if (!active || requestController.signal.aborted) return;
          setConnection({ key: activationKey, state: errorState(error) });
        })
        .finally(() => {
          if (requestTimer !== undefined) clearTimeout(requestTimer);
          requestTimer = undefined;
          if (controller === requestController) controller = undefined;
          if (inFlight === request) inFlight = undefined;
          if (queuedManual === undefined) scheduleAuto();
        });
      inFlight = request;
      return request;
    };

    refreshRef.current = () => {
      clearPollTimer();
      return execute(true);
    };
    deadlineTimer = setTimeout(() => {
      expired = true;
      clearPollTimer();
      controller?.abort();
      if (active && !terminal) setConnection({ key: activationKey, state: "poll_timeout" });
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
  }, [activationKey, deadlineMs, intervalMs, runId, token]);

  const refresh = useCallback(() => refreshRef.current(), []);
  return {
    serverRun: serverState.key === activationKey ? serverState.run : null,
    connectionState: connection.key === activationKey
      ? connection.state
      : "connecting",
    refresh,
  };
}
