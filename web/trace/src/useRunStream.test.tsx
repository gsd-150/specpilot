import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { RunEvent, RunStatus, RunView } from "./api";
import { StreamProtocolError, streamRunEvents } from "./sse";
import { useRunStream } from "./useRunStream";

vi.mock("./sse", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./sse")>()),
  streamRunEvents: vi.fn(),
}));

const RUN_ID = "123e4567-e89b-42d3-a456-426614174000";
const HASH = "a".repeat(64);
const mockedStream = vi.mocked(streamRunEvents);

function transition(sequence: number): Extract<RunEvent, { kind: "state_transition" }> {
  return { kind: "state_transition", sequence, previous_status: "running", status: "running", reason: null };
}

function terminal(sequence: number): Extract<RunEvent, { kind: "terminal" }> {
  return { kind: "terminal", sequence, status: "answered", reason: null };
}

function interruptedTransition(sequence: number): Extract<RunEvent, { kind: "state_transition" }> {
  return { kind: "state_transition", sequence, previous_status: "running", status: "interrupted", reason: "queue_delivery_failed" };
}

function view(status: RunStatus = "running", events: RunEvent[] = [transition(1)]): RunView {
  const completed = status !== "queued" && status !== "running" && status !== "interrupted";
  return {
    run_id: RUN_ID,
    request_id: RUN_ID,
    task_level: "L1",
    profile: "fixture",
    corpus_manifest_id: HASH,
    status,
    reason: status === "answered" ? null : status === "running" || status === "queued" ? null : "stable_reason",
    created_at: "2026-08-12T00:00:00Z",
    started_at: status === "queued" ? null : "2026-08-12T00:00:01Z",
    completed_at: completed ? "2026-08-12T00:00:02Z" : null,
    events,
  };
}

function response(run: RunView): Response {
  return new Response(JSON.stringify(run), { status: 200, headers: { "content-type": "application/json" } });
}

async function* failStream(): AsyncIterable<RunEvent> {
  throw new Error("stream transport failed with secret");
}

async function* eventsThenFail(...events: RunEvent[]): AsyncIterable<RunEvent> {
  for (const event of events) yield event;
  throw new Error("stream closed");
}

async function flush(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe("useRunStream", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    mockedStream.mockReset();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllTimers();
    vi.useRealTimers();
  });

  it("uses exponential reconnect backoff and stops after the bounded sequence", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(view())));
    mockedStream.mockImplementation(failStream);
    const hook = renderHook(() => useRunStream({ runId: RUN_ID, token: "credential", intervalMs: 1_000, deadlineMs: 60_000 }));
    await flush();
    expect(mockedStream).toHaveBeenCalledTimes(1);

    for (const [index, delay] of [250, 500, 1_000, 2_000, 4_000].entries()) {
      await act(async () => vi.advanceTimersByTimeAsync(delay - 1));
      expect(mockedStream).toHaveBeenCalledTimes(index + 1);
      await act(async () => vi.advanceTimersByTimeAsync(1));
      expect(mockedStream).toHaveBeenCalledTimes(index + 2);
    }
    expect(hook.result.current.connectionState).toBe("stream_unavailable");
    expect(hook.result.current.serverRun?.events).toHaveLength(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("enforces the fixed 60-second connection deadline", async () => {
    let streamSignal: AbortSignal | undefined;
    mockedStream.mockImplementation((_runId, options) => {
      const streamOptions = options!;
      streamSignal = streamOptions.signal;
      return (async function* () {
        await new Promise<void>((_resolve, reject) => streamOptions.signal?.addEventListener("abort", () => reject(new Error("aborted")), { once: true }));
      })();
    });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(view())));
    const hook = renderHook(() => useRunStream({ runId: RUN_ID, intervalMs: 1_000, deadlineMs: 60_000 }));
    await flush();

    await act(async () => vi.advanceTimersByTimeAsync(59_999));
    expect(streamSignal?.aborted).toBe(false);
    await act(async () => vi.advanceTimersByTimeAsync(1));
    expect(streamSignal?.aborted).toBe(true);
    expect(hook.result.current.connectionState).toBe("stream_unavailable");
  });

  it("reuses the last accepted cursor after a failed connection", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(view())));
    mockedStream
      .mockImplementationOnce(() => eventsThenFail(transition(2)))
      .mockImplementation(failStream);
    const hook = renderHook(() => useRunStream({ runId: RUN_ID, intervalMs: 1_000, deadlineMs: 60_000 }));
    await flush();
    expect(hook.result.current.serverRun?.events.map((event) => event.sequence)).toEqual([1, 2]);

    await act(async () => vi.advanceTimersByTimeAsync(250));
    expect(mockedStream.mock.calls[1]![1]!.afterSequence).toBe(2);
  });

  it("stops permanently after a terminal stream event", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(view())));
    mockedStream.mockImplementation(() => (async function* () { yield terminal(2); })());
    const hook = renderHook(() => useRunStream({ runId: RUN_ID, intervalMs: 1_000, deadlineMs: 60_000 }));
    await flush();

    expect(hook.result.current.serverRun?.status).toBe("answered");
    expect(hook.result.current.serverRun).toMatchObject({ projection: "stream" });
    expect(hook.result.current.serverRun).not.toHaveProperty("completed_at");
    expect(hook.result.current.connectionState).toBe("connected");
    await act(async () => vi.advanceTimersByTimeAsync(60_000));
    expect(mockedStream).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("replays an already-terminal initial snapshot through SSE", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(view("answered", [transition(1), terminal(2)]))));
    mockedStream.mockImplementation(() => (async function* () {
      yield transition(1);
      yield terminal(2);
    })());
    const hook = renderHook(() => useRunStream({ runId: RUN_ID, intervalMs: 1_000, deadlineMs: 60_000 }));
    await flush();

    expect(mockedStream).toHaveBeenCalledWith(RUN_ID, expect.objectContaining({ afterSequence: 0 }));
    expect(hook.result.current.serverRun?.events.map((event) => event.sequence)).toEqual([1, 2]);
    expect(hook.result.current.connectionState).toBe("connected");
  });

  it("retains a terminal-status transition and waits for the durable terminal event", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(view())));
    mockedStream.mockImplementation(() => (async function* () {
      yield interruptedTransition(2);
      yield { kind: "terminal", sequence: 3, status: "interrupted", reason: "queue_delivery_failed" } as const;
    })());
    const hook = renderHook(() => useRunStream({ runId: RUN_ID, intervalMs: 1_000, deadlineMs: 60_000 }));
    await flush();

    expect(hook.result.current.serverRun?.events.map((event) => event.sequence)).toEqual([1, 2, 3]);
    expect(hook.result.current.serverRun?.events.at(-1)?.kind).toBe("terminal");
    expect(hook.result.current.serverRun?.status).toBe("interrupted");
    expect(hook.result.current.serverRun).toMatchObject({ projection: "stream" });
    expect(hook.result.current.serverRun).not.toHaveProperty("completed_at");
    expect(mockedStream).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("aborts the active stream on unmount", async () => {
    let signal: AbortSignal | undefined;
    mockedStream.mockImplementation((_runId, options) => {
      const streamOptions = options!;
      signal = streamOptions.signal;
      return (async function* () {
        await new Promise<void>((_resolve, reject) => streamOptions.signal?.addEventListener("abort", () => reject(new Error("aborted")), { once: true }));
      })();
    });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(view())));
    const hook = renderHook(() => useRunStream({ runId: RUN_ID, intervalMs: 1_000, deadlineMs: 60_000 }));
    await flush();

    hook.unmount();
    expect(signal?.aborted).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("coalesces Refresh with the initial snapshot and aborts that one request on unmount", async () => {
    let signal: AbortSignal | undefined;
    const fetcher = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => new Promise<Response>((_resolve, reject) => {
      signal = init?.signal as AbortSignal;
      signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")), { once: true });
    }));
    vi.stubGlobal("fetch", fetcher);
    const hook = renderHook(() => useRunStream({ runId: RUN_ID, intervalMs: 1_000, deadlineMs: 60_000 }));
    await flush();

    let first!: Promise<void>;
    let second!: Promise<void>;
    act(() => {
      first = hook.result.current.refresh();
      second = hook.result.current.refresh();
    });
    expect(first).toBe(second);
    expect(fetcher).toHaveBeenCalledTimes(1);
    hook.unmount();
    expect(signal?.aborted).toBe(true);
    await first;
    expect(vi.getTimerCount()).toBe(0);
  });

  it("retains the last valid streamed view when reconnects are exhausted", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(view())));
    mockedStream
      .mockImplementationOnce(() => eventsThenFail(transition(2)))
      .mockImplementation(failStream);
    const hook = renderHook(() => useRunStream({ runId: RUN_ID, intervalMs: 1_000, deadlineMs: 60_000 }));
    await flush();
    await act(async () => vi.advanceTimersByTimeAsync(7_750));

    expect(hook.result.current.connectionState).toBe("stream_unavailable");
    expect(hook.result.current.serverRun?.events.map((event) => event.sequence)).toEqual([1, 2]);
    expect(JSON.stringify(hook.result.current)).not.toContain("secret");
  });

  it("fails closed immediately on invalid stream data without reconnecting", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(view())));
    mockedStream.mockImplementation(() => (async function* () {
      throw new StreamProtocolError("invalid SSE event data");
    })());
    const hook = renderHook(() => useRunStream({ runId: RUN_ID, intervalMs: 1_000, deadlineMs: 60_000 }));
    await flush();

    expect(hook.result.current.connectionState).toBe("stream_unavailable");
    expect(hook.result.current.serverRun?.events.map((event) => event.sequence)).toEqual([1]);
    await act(async () => vi.advanceTimersByTimeAsync(60_000));
    expect(mockedStream).toHaveBeenCalledTimes(1);
  });

  it("uses getRun only for the initial and manual fallback snapshots", async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce(response(view()))
      .mockResolvedValueOnce(response(view("answered", [transition(1), terminal(2)])));
    vi.stubGlobal("fetch", fetcher);
    mockedStream.mockImplementation(failStream);
    const hook = renderHook(() => useRunStream({ runId: RUN_ID, token: "credential", intervalMs: 1_000, deadlineMs: 60_000 }));
    await flush();
    await act(async () => vi.advanceTimersByTimeAsync(7_750));
    expect(fetcher).toHaveBeenCalledTimes(1);

    await act(async () => hook.result.current.refresh());
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(hook.result.current.serverRun?.status).toBe("answered");
    expect(hook.result.current.connectionState).toBe("connected");
    await act(async () => vi.advanceTimersByTimeAsync(60_000));
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("keeps manual fallback available when the initial snapshot recovers without a stream", async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce(new Response("unavailable", { status: 503 }))
      .mockResolvedValueOnce(response(view()));
    vi.stubGlobal("fetch", fetcher);
    const hook = renderHook(() => useRunStream({ runId: RUN_ID, intervalMs: 1_000, deadlineMs: 60_000 }));
    await flush();
    expect(hook.result.current.connectionState).toBe("service_unavailable");

    await act(async () => hook.result.current.refresh());
    expect(hook.result.current.serverRun?.status).toBe("running");
    expect(hook.result.current.connectionState).toBe("stream_unavailable");
    expect(mockedStream).not.toHaveBeenCalled();
  });

  it("bounds a hanging manual snapshot and preserves the last view", async () => {
    let manualSignal: AbortSignal | undefined;
    const fetcher = vi.fn()
      .mockResolvedValueOnce(response(view()))
      .mockImplementationOnce((_input: RequestInfo | URL, init?: RequestInit) => new Promise<Response>((_resolve, reject) => {
        manualSignal = init?.signal as AbortSignal;
        manualSignal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")), { once: true });
      }));
    vi.stubGlobal("fetch", fetcher);
    mockedStream.mockImplementation(failStream);
    const hook = renderHook(() => useRunStream({ runId: RUN_ID, intervalMs: 1_000, deadlineMs: 60_000 }));
    await flush();
    await act(async () => vi.advanceTimersByTimeAsync(7_750));

    act(() => { void hook.result.current.refresh(); });
    await act(async () => vi.advanceTimersByTimeAsync(60_000));
    expect(manualSignal?.aborted).toBe(true);
    expect(hook.result.current.connectionState).toBe("poll_timeout");
    expect(hook.result.current.serverRun?.events.map((event) => event.sequence)).toEqual([1]);
  });

  it("does not snapshot-poll while a stream is healthy", async () => {
    mockedStream.mockImplementation((_runId, options) => (async function* () {
      await new Promise<void>((_resolve, reject) => options!.signal?.addEventListener("abort", () => reject(new Error("aborted")), { once: true }));
    })());
    const fetcher = vi.fn().mockResolvedValue(response(view()));
    vi.stubGlobal("fetch", fetcher);
    const hook = renderHook(() => useRunStream({ runId: RUN_ID, intervalMs: 10, deadlineMs: 60_000 }));
    await flush();

    await act(async () => vi.advanceTimersByTimeAsync(30_000));
    expect(fetcher).toHaveBeenCalledTimes(1);
    hook.unmount();
  });
});
