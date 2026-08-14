import { useCallback, useEffect, useRef, useState } from "react";

import { getRun, type RunEvent, type RunResultView, type StreamRunProjection, type TerminalStatus } from "./api";
import { StreamProtocolError, streamRunEvents } from "./sse";
import type { ConnectionState, RunPollingOptions, RunPollingResult } from "./useRunPolling";

const TERMINAL = new Set<TerminalStatus>([
  "answered",
  "refused",
  "egress_blocked",
  "failed",
  "interrupted",
]);
const BACKOFF_MS = [250, 500, 1_000, 2_000, 4_000] as const;

function snapshotErrorState(error: unknown): ConnectionState {
  const message = error instanceof Error ? error.message : "";
  if (message === "run request failed (401)") return "unauthorized";
  if (message === "run request failed (404)") return "not_found";
  if (message === "run request failed (503)") return "service_unavailable";
  if (message === "run request unavailable") return "network_error";
  return "invalid_response";
}

function terminalProjection(
  run: RunResultView,
  event: Extract<RunEvent, { kind: "state_transition" | "terminal" }>,
): StreamRunProjection {
  return {
    projection: "stream",
    run_id: run.run_id,
    request_id: run.request_id,
    task_level: run.task_level,
    profile: run.profile,
    corpus_manifest_id: run.corpus_manifest_id,
    status: event.status,
    reason: event.reason,
    created_at: run.created_at,
    started_at: run.started_at,
    events: [...run.events, event],
  };
}

function appendEvent(run: RunResultView, event: RunEvent): RunResultView {
  if (event.sequence !== (run.events.at(-1)?.sequence ?? 0) + 1) throw new Error("invalid stream sequence");
  if (event.kind === "state_transition" || event.kind === "terminal") {
    if (TERMINAL.has(event.status as TerminalStatus)) return terminalProjection(run, event);
    return { ...run, status: event.status, reason: event.reason, events: [...run.events, event] };
  }
  return { ...run, events: [...run.events, event] };
}

export function useRunStream({ runId, token, deadlineMs }: RunPollingOptions): RunPollingResult {
  const activationKey = JSON.stringify([runId, token ?? null]);
  const [serverState, setServerState] = useState<{ key: string; run: RunResultView | null }>({ key: activationKey, run: null });
  const [connection, setConnection] = useState<{ key: string; state: ConnectionState }>({ key: activationKey, state: "connecting" });
  const refreshRef = useRef<() => Promise<void>>(async () => undefined);

  useEffect(() => {
    let active = true;
    let expired = false;
    let terminal = false;
    let streamUnavailable = false;
    let currentRun: RunResultView | null = null;
    let cursor = 0;
    let retryIndex = 0;
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined;
    let deadlineTimer: ReturnType<typeof setTimeout> | undefined;
    let refreshTimer: ReturnType<typeof setTimeout> | undefined;
    let streamController: AbortController | undefined;
    let snapshotController: AbortController | undefined;
    let snapshotInFlight: Promise<void> | undefined;
    const deadlineAt = performance.now() + deadlineMs;

    setServerState({ key: activationKey, run: null });
    setConnection({ key: activationKey, state: "connecting" });

    const clearReconnect = (): void => {
      if (reconnectTimer !== undefined) clearTimeout(reconnectTimer);
      reconnectTimer = undefined;
    };

    const finishTerminal = (): void => {
      terminal = true;
      clearReconnect();
      if (deadlineTimer !== undefined) clearTimeout(deadlineTimer);
      deadlineTimer = undefined;
    };

    const publish = (run: RunResultView, state: ConnectionState): void => {
      currentRun = run;
      setServerState({ key: activationKey, run });
      setConnection({ key: activationKey, state });
    };

    const unavailable = (): void => {
      if (!active || terminal) return;
      streamUnavailable = true;
      clearReconnect();
      if (deadlineTimer !== undefined) clearTimeout(deadlineTimer);
      deadlineTimer = undefined;
      setConnection({ key: activationKey, state: "stream_unavailable" });
    };

    const connect = async (): Promise<void> => {
      if (!active || expired || terminal || currentRun === null) return;
      const controller = new AbortController();
      streamController = controller;
      try {
        for await (const event of streamRunEvents(runId, { token, afterSequence: cursor, signal: controller.signal })) {
          if (!active || controller.signal.aborted || currentRun === null) return;
          const next = appendEvent(currentRun, event);
          cursor = event.sequence;
          retryIndex = 0;
          publish(next, "connected");
          if (event.kind === "terminal") {
            finishTerminal();
            return;
          }
        }
        if (!terminal) throw new Error("stream ended before terminal event");
      } catch (error: unknown) {
        if (!active || expired || terminal || controller.signal.aborted) return;
        if (error instanceof StreamProtocolError) {
          unavailable();
          return;
        }
        if (retryIndex >= BACKOFF_MS.length) {
          unavailable();
          return;
        }
        const delay = BACKOFF_MS[retryIndex++];
        if (performance.now() + delay >= deadlineAt) return;
        reconnectTimer = setTimeout(() => {
          reconnectTimer = undefined;
          void connect();
        }, delay);
      } finally {
        if (streamController === controller) streamController = undefined;
      }
    };

    const refresh = (): Promise<void> => {
      if (!active) return Promise.resolve();
      if (snapshotInFlight !== undefined) return snapshotInFlight;
      const controller = new AbortController();
      snapshotController = controller;
      refreshTimer = setTimeout(() => {
        controller.abort();
        if (active) setConnection({ key: activationKey, state: "poll_timeout" });
      }, deadlineMs);
      const request = getRun(runId, { token, signal: controller.signal })
        .then((run) => {
          if (!active || controller.signal.aborted) return;
          cursor = run.events.at(-1)?.sequence ?? 0;
          publish(run, TERMINAL.has(run.status as TerminalStatus) ? "connected" : streamUnavailable ? "stream_unavailable" : "connected");
          if (TERMINAL.has(run.status as TerminalStatus)) finishTerminal();
        })
        .catch((error: unknown) => {
          if (!active || controller.signal.aborted) return;
          setConnection({ key: activationKey, state: snapshotErrorState(error) });
        })
        .finally(() => {
          if (refreshTimer !== undefined) clearTimeout(refreshTimer);
          refreshTimer = undefined;
          if (snapshotController === controller) snapshotController = undefined;
          if (snapshotInFlight === request) snapshotInFlight = undefined;
        });
      snapshotInFlight = request;
      return request;
    };
    refreshRef.current = refresh;

    deadlineTimer = setTimeout(() => {
      expired = true;
      clearReconnect();
      streamController?.abort();
      snapshotController?.abort();
      if (active && !terminal) {
        if (currentRun === null) setConnection({ key: activationKey, state: "poll_timeout" });
        else unavailable();
      }
    }, deadlineMs);

    const initialController = new AbortController();
    snapshotController = initialController;
    const initialRequest = getRun(runId, { token, signal: initialController.signal })
      .then((run) => {
        if (!active || initialController.signal.aborted) return;
        currentRun = run;
        cursor = run.events.at(-1)?.sequence ?? 0;
        publish(run, "connected");
        if (TERMINAL.has(run.status as TerminalStatus)) finishTerminal();
        else void connect();
      })
      .catch((error: unknown) => {
        if (!active || initialController.signal.aborted) return;
        streamUnavailable = true;
        if (deadlineTimer !== undefined) clearTimeout(deadlineTimer);
        deadlineTimer = undefined;
        setConnection({ key: activationKey, state: snapshotErrorState(error) });
      })
      .finally(() => {
        if (snapshotController === initialController) snapshotController = undefined;
        if (snapshotInFlight === initialRequest) snapshotInFlight = undefined;
      });
    snapshotInFlight = initialRequest;

    return () => {
      active = false;
      refreshRef.current = async () => undefined;
      clearReconnect();
      if (deadlineTimer !== undefined) clearTimeout(deadlineTimer);
      if (refreshTimer !== undefined) clearTimeout(refreshTimer);
      streamController?.abort();
      snapshotController?.abort();
    };
  }, [activationKey, deadlineMs, runId, token]);

  const refresh = useCallback(() => refreshRef.current(), []);
  return {
    serverRun: serverState.key === activationKey ? serverState.run : null,
    connectionState: connection.key === activationKey ? connection.state : "connecting",
    refresh,
  };
}
