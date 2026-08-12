import { act, render, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { RunStatus, RunView } from "./api";
import { useRunPolling } from "./useRunPolling";

const RUN_ID = "123e4567-e89b-42d3-a456-426614174000";
const OTHER_RUN_ID = "00000000-0000-0000-0000-000000000001";
const HASH = "a".repeat(64);

function view(status: RunStatus = "running", runId = RUN_ID): RunView {
  const terminal = !["queued", "running"].includes(status);
  const reason = status === "answered" ? null
    : status === "refused" ? "evidence_insufficient"
      : status === "egress_blocked" ? "root_unique_excerpts_exceeded"
        : status === "failed" ? "provider_timeout"
          : status === "interrupted" ? "lease_expired"
            : null;
  return {
    run_id: runId,
    request_id: runId,
    task_level: "L1",
    profile: "fixture",
    corpus_manifest_id: HASH,
    status,
    reason,
    created_at: "2026-08-12T00:00:00Z",
    started_at: status === "queued" ? null : "2026-08-12T00:00:01Z",
    completed_at: terminal && status !== "interrupted" ? "2026-08-12T00:00:02Z" : null,
    events: [],
  };
}

function response(run: RunView): Response {
  return new Response(JSON.stringify(run), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

function deferred<T>(): {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason?: unknown) => void;
} {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

async function flush(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe("useRunPolling", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllTimers();
    vi.useRealTimers();
  });

  it.each<RunStatus>([
    "answered",
    "refused",
    "egress_blocked",
    "failed",
    "interrupted",
  ])("stops automatic polling on terminal status %s", async (status) => {
    const fetcher = vi.fn().mockResolvedValue(response(view(status)));
    vi.stubGlobal("fetch", fetcher);

    renderHook(() => useRunPolling({
      runId: RUN_ID,
      token: "credential",
      intervalMs: 10,
      deadlineMs: 1_000,
    }));
    await flush();
    await act(async () => vi.advanceTimersByTimeAsync(100));

    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("never overlaps requests and schedules the next poll after completion", async () => {
    const first = deferred<Response>();
    const fetcher = vi.fn()
      .mockReturnValueOnce(first.promise)
      .mockResolvedValue(response(view("running")));
    vi.stubGlobal("fetch", fetcher);

    renderHook(() => useRunPolling({
      runId: RUN_ID,
      token: "credential",
      intervalMs: 10,
      deadlineMs: 1_000,
    }));
    await flush();
    await act(async () => vi.advanceTimersByTimeAsync(100));
    expect(fetcher).toHaveBeenCalledTimes(1);

    first.resolve(response(view("running")));
    await flush();
    await act(async () => vi.advanceTimersByTimeAsync(9));
    expect(fetcher).toHaveBeenCalledTimes(1);
    await act(async () => vi.advanceTimersByTimeAsync(1));
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("aborts an in-flight request at the fixed deadline and retains the last run", async () => {
    const second = deferred<Response>();
    const signals: AbortSignal[] = [];
    const fetcher = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      signals.push(init?.signal as AbortSignal);
      return signals.length === 1
        ? Promise.resolve(response(view("running")))
        : second.promise;
    });
    vi.stubGlobal("fetch", fetcher);

    const hook = renderHook(() => useRunPolling({
      runId: RUN_ID,
      token: "credential",
      intervalMs: 10,
      deadlineMs: 25,
    }));
    await flush();
    await act(async () => vi.advanceTimersByTimeAsync(10));
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(signals[0]).not.toBe(signals[1]);

    await act(async () => vi.advanceTimersByTimeAsync(15));
    expect(signals[1]?.aborted).toBe(true);
    expect(hook.result.current.serverRun?.status).toBe("running");
    expect(hook.result.current.connectionState).toBe("poll_timeout");

    second.resolve(response(view("answered")));
    await flush();
    expect(hook.result.current.serverRun?.status).toBe("running");
  });

  it("uses a deadline measured from activation rather than from request completion", async () => {
    const pending = deferred<Response>();
    let signal: AbortSignal | undefined;
    vi.stubGlobal("fetch", vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      signal = init?.signal as AbortSignal;
      return pending.promise;
    }));

    const hook = renderHook(() => useRunPolling({
      runId: RUN_ID,
      token: "credential",
      intervalMs: 10,
      deadlineMs: 25,
    }));
    await flush();
    await act(async () => vi.advanceTimersByTimeAsync(25));

    expect(signal?.aborted).toBe(true);
    expect(hook.result.current.connectionState).toBe("poll_timeout");
  });

  it("manual refresh after timeout performs one read without resetting the deadline", async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce(response(view("running")))
      .mockResolvedValueOnce(response(view("answered")));
    vi.stubGlobal("fetch", fetcher);
    const hook = renderHook(() => useRunPolling({
      runId: RUN_ID,
      token: "credential",
      intervalMs: 100,
      deadlineMs: 25,
    }));
    await flush();
    await act(async () => vi.advanceTimersByTimeAsync(25));
    expect(hook.result.current.connectionState).toBe("poll_timeout");

    await act(async () => hook.result.current.refresh());
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(hook.result.current.serverRun?.status).toBe("answered");
    expect(hook.result.current.connectionState).toBe("connected");
    await act(async () => vi.advanceTimersByTimeAsync(1_000));
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("queues a refresh when the deadline abort has not settled yet", async () => {
    const aborted = deferred<Response>();
    const fetcher = vi.fn()
      .mockResolvedValueOnce(response(view("running")))
      .mockImplementationOnce((_input: RequestInfo | URL, init?: RequestInit) => {
        init?.signal?.addEventListener("abort", () => {
          aborted.reject(new DOMException("aborted", "AbortError"));
        });
        return aborted.promise;
      })
      .mockResolvedValueOnce(response(view("answered")));
    vi.stubGlobal("fetch", fetcher);
    const hook = renderHook(() => useRunPolling({
      runId: RUN_ID,
      token: "credential",
      intervalMs: 10,
      deadlineMs: 25,
    }));
    await flush();
    await act(async () => vi.advanceTimersByTimeAsync(10));
    expect(fetcher).toHaveBeenCalledTimes(2);

    let refreshPromise!: Promise<void>;
    act(() => {
      vi.advanceTimersByTime(15);
      refreshPromise = hook.result.current.refresh();
    });
    await act(async () => refreshPromise);

    expect(fetcher).toHaveBeenCalledTimes(3);
    expect(hook.result.current.serverRun?.status).toBe("answered");
    await act(async () => vi.advanceTimersByTimeAsync(1_000));
    expect(fetcher).toHaveBeenCalledTimes(3);
  });

  it("manual refresh does not clear the last server run while its request is pending", async () => {
    const pending = deferred<Response>();
    const fetcher = vi.fn()
      .mockResolvedValueOnce(response(view("refused")))
      .mockReturnValueOnce(pending.promise);
    vi.stubGlobal("fetch", fetcher);
    const hook = renderHook(() => useRunPolling({
      runId: RUN_ID,
      token: "credential",
      intervalMs: 100,
      deadlineMs: 1_000,
    }));
    await flush();

    let refreshPromise!: Promise<void>;
    act(() => {
      refreshPromise = hook.result.current.refresh();
    });
    expect(hook.result.current.serverRun?.status).toBe("refused");
    pending.resolve(response(view("answered")));
    await act(async () => refreshPromise);
  });

  it("manual refresh replaces a pending automatic timer and resumes the cadence", async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce(response(view("running")))
      .mockResolvedValueOnce(response(view("running")))
      .mockResolvedValue(response(view("running")));
    vi.stubGlobal("fetch", fetcher);
    const hook = renderHook(() => useRunPolling({
      runId: RUN_ID,
      token: "credential",
      intervalMs: 20,
      deadlineMs: 100,
    }));
    await flush();
    await act(async () => vi.advanceTimersByTimeAsync(5));
    await act(async () => hook.result.current.refresh());
    expect(fetcher).toHaveBeenCalledTimes(2);

    await act(async () => vi.advanceTimersByTimeAsync(19));
    expect(fetcher).toHaveBeenCalledTimes(2);
    await act(async () => vi.advanceTimersByTimeAsync(1));
    expect(fetcher).toHaveBeenCalledTimes(3);
  });

  it.each([
    ["success", () => Promise.resolve(response(view("running"))), "connected"],
    ["error", () => Promise.resolve(new Response("hidden", { status: 503 })), "service_unavailable"],
  ] as const)("resumes automatic polling after manual %s", async (_name, manual, expectedState) => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce(response(view("running")))
      .mockImplementationOnce(manual)
      .mockResolvedValue(response(view("answered")));
    vi.stubGlobal("fetch", fetcher);
    const hook = renderHook(() => useRunPolling({
      runId: RUN_ID,
      token: "credential",
      intervalMs: 10,
      deadlineMs: 100,
    }));
    await flush();
    await act(async () => hook.result.current.refresh());
    expect(hook.result.current.connectionState).toBe(expectedState);
    await act(async () => vi.advanceTimersByTimeAsync(10));
    expect(fetcher).toHaveBeenCalledTimes(3);
  });

  it("preserves automatic intent when its timer fires during a manual request", async () => {
    const manual = deferred<Response>();
    const fetcher = vi.fn()
      .mockResolvedValueOnce(response(view("running")))
      .mockReturnValueOnce(manual.promise)
      .mockResolvedValue(response(view("answered")));
    vi.stubGlobal("fetch", fetcher);
    const hook = renderHook(() => useRunPolling({
      runId: RUN_ID,
      token: "credential",
      intervalMs: 10,
      deadlineMs: 100,
    }));
    await flush();
    let refreshPromise!: Promise<void>;
    act(() => {
      refreshPromise = hook.result.current.refresh();
    });
    await act(async () => vi.advanceTimersByTimeAsync(10));
    expect(fetcher).toHaveBeenCalledTimes(2);
    manual.resolve(response(view("running")));
    await act(async () => refreshPromise);
    await act(async () => vi.advanceTimersByTimeAsync(10));
    expect(fetcher).toHaveBeenCalledTimes(3);
  });

  it("does not resume automatic polling after a manual terminal response", async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce(response(view("running")))
      .mockResolvedValueOnce(response(view("refused")));
    vi.stubGlobal("fetch", fetcher);
    const hook = renderHook(() => useRunPolling({
      runId: RUN_ID,
      token: "credential",
      intervalMs: 10,
      deadlineMs: 100,
    }));
    await flush();
    await act(async () => hook.result.current.refresh());
    await act(async () => vi.advanceTimersByTimeAsync(1_000));
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("bounds a hanging manual refresh after a terminal response", async () => {
    const hanging = deferred<Response>();
    let manualSignal: AbortSignal | undefined;
    const fetcher = vi.fn()
      .mockResolvedValueOnce(response(view("answered")))
      .mockImplementationOnce((_input: RequestInfo | URL, init?: RequestInit) => {
        manualSignal = init?.signal as AbortSignal;
        return hanging.promise;
      });
    vi.stubGlobal("fetch", fetcher);
    const hook = renderHook(() => useRunPolling({
      runId: RUN_ID,
      token: "credential",
      intervalMs: 10,
      deadlineMs: 25,
    }));
    await flush();
    act(() => { void hook.result.current.refresh(); });
    await act(async () => vi.advanceTimersByTimeAsync(25));
    expect(manualSignal?.aborted).toBe(true);
    expect(hook.result.current.serverRun?.status).toBe("answered");
    expect(hook.result.current.connectionState).toBe("poll_timeout");
  });

  it("uses only the remaining activation deadline for a pre-deadline manual request", async () => {
    const hanging = deferred<Response>();
    let manualSignal: AbortSignal | undefined;
    const fetcher = vi.fn()
      .mockResolvedValueOnce(response(view("running")))
      .mockImplementationOnce((_input: RequestInfo | URL, init?: RequestInit) => {
        manualSignal = init?.signal as AbortSignal;
        return hanging.promise;
      });
    vi.stubGlobal("fetch", fetcher);
    const hook = renderHook(() => useRunPolling({
      runId: RUN_ID,
      token: "credential",
      intervalMs: 100,
      deadlineMs: 25,
    }));
    await flush();
    await act(async () => vi.advanceTimersByTimeAsync(20));
    act(() => { void hook.result.current.refresh(); });
    await act(async () => vi.advanceTimersByTimeAsync(5));
    expect(manualSignal?.aborted).toBe(true);
    expect(hook.result.current.connectionState).toBe("poll_timeout");
  });

  it("coalesces repeated refreshes to at most one queued manual request", async () => {
    const first = deferred<Response>();
    const queued = deferred<Response>();
    const fetcher = vi.fn()
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(queued.promise);
    vi.stubGlobal("fetch", fetcher);
    const hook = renderHook(() => useRunPolling({
      runId: RUN_ID,
      token: "credential",
      intervalMs: 100,
      deadlineMs: 1_000,
    }));
    await flush();
    let p1!: Promise<void>;
    let p2!: Promise<void>;
    let p3!: Promise<void>;
    act(() => {
      p1 = hook.result.current.refresh();
      p2 = hook.result.current.refresh();
      p3 = hook.result.current.refresh();
    });
    expect(p1).toBe(p2);
    expect(p2).toBe(p3);
    first.resolve(response(view("running")));
    await flush();
    expect(fetcher).toHaveBeenCalledTimes(2);
    queued.resolve(response(view("answered")));
    await act(async () => p1);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("aborts a queued manual request on unmount without resuming automatic polling", async () => {
    const first = deferred<Response>();
    const queued = deferred<Response>();
    let queuedSignal: AbortSignal | undefined;
    const fetcher = vi.fn()
      .mockReturnValueOnce(first.promise)
      .mockImplementationOnce((_input: RequestInfo | URL, init?: RequestInit) => {
        queuedSignal = init?.signal as AbortSignal;
        return queued.promise;
      });
    vi.stubGlobal("fetch", fetcher);
    const hook = renderHook(() => useRunPolling({
      runId: RUN_ID,
      token: "credential",
      intervalMs: 10,
      deadlineMs: 1_000,
    }));
    await flush();
    let refreshPromise!: Promise<void>;
    act(() => { refreshPromise = hook.result.current.refresh(); });
    first.resolve(response(view("running")));
    await flush();
    hook.unmount();
    expect(queuedSignal?.aborted).toBe(true);
    queued.resolve(response(view("running")));
    await refreshPromise;
    expect(vi.getTimerCount()).toBe(0);
  });

  it("aborts on unmount and ignores completion from the abandoned request", async () => {
    const pending = deferred<Response>();
    let signal: AbortSignal | undefined;
    const fetcher = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      signal = init?.signal as AbortSignal;
      return pending.promise;
    });
    vi.stubGlobal("fetch", fetcher);
    const hook = renderHook(() => useRunPolling({
      runId: RUN_ID,
      token: "credential",
      intervalMs: 10,
      deadlineMs: 1_000,
    }));
    await flush();
    hook.unmount();
    expect(signal?.aborted).toBe(true);
    pending.resolve(response(view("answered")));
    await flush();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("discards stale completion after run ID and token change", async () => {
    const old = deferred<Response>();
    const fetcher = vi.fn()
      .mockReturnValueOnce(old.promise)
      .mockResolvedValueOnce(response(view("answered", OTHER_RUN_ID)));
    vi.stubGlobal("fetch", fetcher);
    const props = { runId: RUN_ID, token: "old-token" };
    const hook = renderHook(
      ({ runId, token }) => useRunPolling({ runId, token, intervalMs: 10, deadlineMs: 1_000 }),
      { initialProps: props },
    );
    await flush();
    hook.rerender({ runId: OTHER_RUN_ID, token: "new-token" });
    await flush();
    old.resolve(response(view("refused", RUN_ID)));
    await flush();

    expect(hook.result.current.serverRun?.run_id).toBe(OTHER_RUN_ID);
    expect(JSON.stringify(fetcher.mock.calls[1])).not.toContain("old-token");
  });

  it("synchronously hides the previous owner trace when identity changes", async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce(response(view("refused", RUN_ID)))
      .mockReturnValueOnce(deferred<Response>().promise);
    vi.stubGlobal("fetch", fetcher);
    const snapshots: Array<{ identity: string; runId: string | null; state: string }> = [];
    function Harness({ runId, token }: { runId: string; token: string }): null {
      const result = useRunPolling({ runId, token, intervalMs: 10, deadlineMs: 1_000 });
      snapshots.push({ identity: runId, runId: result.serverRun?.run_id ?? null, state: result.connectionState });
      return null;
    }
    const hook = render(<Harness runId={RUN_ID} token="old-token" />);
    await flush();
    expect(snapshots.at(-1)?.runId).toBe(RUN_ID);

    snapshots.length = 0;
    hook.rerender(<Harness runId={OTHER_RUN_ID} token="new-token" />);
    expect(snapshots[0]).toEqual({ identity: OTHER_RUN_ID, runId: null, state: "connecting" });
  });

  it.each([
    [401, "unauthorized"],
    [404, "not_found"],
    [503, "service_unavailable"],
  ] as const)("maps HTTP %s to stable %s without leaking the body", async (status, state) => {
    const fetcher = vi.fn().mockResolvedValue(new Response(
      "credential=secret provider-body run=" + RUN_ID,
      { status },
    ));
    vi.stubGlobal("fetch", fetcher);
    const hook = renderHook(() => useRunPolling({
      runId: RUN_ID,
      token: "credential",
      intervalMs: 10,
      deadlineMs: 1_000,
    }));
    await flush();

    expect(hook.result.current.connectionState).toBe(state);
    expect(JSON.stringify(hook.result.current)).not.toContain("secret");
  });

  it.each([
    [() => Promise.reject(new Error("credential secret")), "network_error"],
    [() => Promise.resolve(new Response("not-json", { status: 200 })), "invalid_response"],
  ] as const)("maps read failures to a sanitized stable state", async (implementation, state) => {
    vi.stubGlobal("fetch", vi.fn(implementation));
    const hook = renderHook(() => useRunPolling({
      runId: RUN_ID,
      token: "credential",
      intervalMs: 10,
      deadlineMs: 1_000,
    }));
    await flush();

    expect(hook.result.current.connectionState).toBe(state);
    expect(JSON.stringify(hook.result.current)).not.toContain("secret");
  });

  it("does not put credentials in a URL or leak timers under StrictMode", async () => {
    const fetcher = vi.fn().mockResolvedValue(response(view("answered")));
    vi.stubGlobal("fetch", fetcher);
    const hook = renderHook(() => useRunPolling({
      runId: RUN_ID,
      token: "credential",
      intervalMs: 10,
      deadlineMs: 1_000,
    }), { reactStrictMode: true });
    await flush();

    for (const [url, init] of fetcher.mock.calls) {
      expect(String(url)).toBe(`/runs/${RUN_ID}`);
      expect(String(url)).not.toContain("credential");
      expect((init as RequestInit).headers).toEqual({ Authorization: "Bearer credential" });
    }
    hook.unmount();
    expect(vi.getTimerCount()).toBe(0);
  });
});
