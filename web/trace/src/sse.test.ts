import { describe, expect, it, vi } from "vitest";

import type { RunEvent } from "./api";
import { MAX_SSE_FRAME_BYTES, streamRunEvents } from "./sse";

const RUN_ID = "123e4567-e89b-42d3-a456-426614174000";

function transition(sequence: number, reason: string | null = null): Extract<RunEvent, { kind: "state_transition" }> {
  return {
    kind: "state_transition",
    sequence,
    previous_status: sequence === 1 ? "queued" : "running",
    status: "running",
    reason,
  };
}

function frame(event: RunEvent, newline = "\n"): string {
  return [
    `id: ${event.sequence}`,
    `event: ${event.kind}`,
    `data: ${JSON.stringify(event)}`,
    "",
    "",
  ].join(newline);
}

function responseFromChunks(chunks: Uint8Array[]): Response {
  return new Response(new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(chunk);
      controller.close();
    },
  }), { status: 200, headers: { "content-type": "text/event-stream" } });
}

function encodedChunks(text: string, cuts: number[] = []): Uint8Array[] {
  const bytes = new TextEncoder().encode(text);
  const chunks: Uint8Array[] = [];
  let start = 0;
  for (const cut of cuts) {
    chunks.push(bytes.slice(start, cut));
    start = cut;
  }
  chunks.push(bytes.slice(start));
  return chunks;
}

async function collect(fetcher: typeof fetch, afterSequence = 0, token?: string): Promise<RunEvent[]> {
  const events: RunEvent[] = [];
  for await (const event of streamRunEvents(RUN_ID, { fetcher, afterSequence, token })) {
    events.push(event);
  }
  return events;
}

describe("streamRunEvents", () => {
  it("decodes UTF-8 and CRLF split across chunks and ignores comments", async () => {
    const text = `: 雪\r\n${frame(transition(1), "\r\n")}`;
    const bytes = new TextEncoder().encode(text);
    const snow = bytes.indexOf(0xe9);
    const cr = bytes.indexOf(0x0d, snow);
    const fetcher = vi.fn().mockResolvedValue(responseFromChunks([
      bytes.slice(0, snow + 1),
      bytes.slice(snow + 1, cr + 1),
      bytes.slice(cr + 1),
    ]));

    await expect(collect(fetcher)).resolves.toEqual([transition(1)]);
  });

  it("parses multiple frames and suppresses an identical adjacent duplicate", async () => {
    const text = `${frame(transition(1))}${frame(transition(1))}: keep-alive\n\n${frame(transition(2))}`;
    const fetcher = vi.fn().mockResolvedValue(responseFromChunks(encodedChunks(text, [3, 29, 91])));

    await expect(collect(fetcher)).resolves.toEqual([transition(1), transition(2)]);
  });

  it.each(["+1", "01", " 1", "1 ", "-1", "1.0", "10001", ""])(
    "rejects non-canonical event ID %j",
    async (id) => {
      const event = transition(1);
      const text = `id: ${id}\nevent: ${event.kind}\ndata: ${JSON.stringify(event)}\n\n`;
      const fetcher = vi.fn().mockResolvedValue(responseFromChunks(encodedChunks(text)));
      await expect(collect(fetcher)).rejects.toThrow("invalid SSE event id");
    },
  );

  it.each([
    ["gap", `${frame(transition(1))}${frame(transition(3))}`, "invalid SSE sequence gap"],
    ["decrease", `${frame(transition(1))}${frame(transition(2))}${frame(transition(1))}`, "invalid SSE sequence decrease"],
    ["conflict", `${frame(transition(1))}${frame({ ...transition(1), previous_status: null })}`, "invalid SSE sequence conflict"],
  ])("rejects sequence %s", async (_name, text, message) => {
    const fetcher = vi.fn().mockResolvedValue(responseFromChunks(encodedChunks(text)));
    await expect(collect(fetcher)).rejects.toThrow(message);
  });

  it.each([
    ["unknown SSE field", `retry: 100\n${frame(transition(1))}`],
    ["unknown JSON field", frame({ ...transition(1), plaintext: "hidden" } as unknown as RunEvent)],
    ["mismatched event name", frame(transition(1)).replace("event: state_transition", "event: terminal")],
    ["mismatched data sequence", frame({ ...transition(1), sequence: 2 }).replace("id: 2", "id: 1")],
  ])("rejects %s", async (_name, text) => {
    const fetcher = vi.fn().mockResolvedValue(responseFromChunks(encodedChunks(text)));
    await expect(collect(fetcher)).rejects.toThrow();
  });

  it("rejects an oversized frame before retaining an unbounded buffer", async () => {
    const text = `: ${"x".repeat(MAX_SSE_FRAME_BYTES)}\n`;
    const fetcher = vi.fn().mockResolvedValue(responseFromChunks(encodedChunks(text)));
    await expect(collect(fetcher)).rejects.toThrow("SSE frame too large");
  });

  it("measures the frame bound in UTF-8 bytes rather than JavaScript characters", async () => {
    const text = `: ${"雪".repeat(Math.ceil(MAX_SSE_FRAME_BYTES / 3))}\n\n`;
    const fetcher = vi.fn().mockResolvedValue(responseFromChunks(encodedChunks(text)));
    await expect(collect(fetcher)).rejects.toThrow("SSE frame too large");
  });

  it("rejects a content type that only prefixes the SSE media type", async () => {
    const response = new Response(new ReadableStream<Uint8Array>({ start(controller) { controller.close(); } }), {
      status: 200,
      headers: { "content-type": "text/event-streaming" },
    });
    await expect(collect(vi.fn().mockResolvedValue(response))).rejects.toThrow("stream response was invalid");
  });

  it("keeps bearer and cursor only in headers with same-origin credentials", async () => {
    const fetcher = vi.fn().mockResolvedValue(responseFromChunks([]));
    await collect(fetcher, 7, "secret");

    expect(fetcher).toHaveBeenCalledWith(`/runs/${RUN_ID}/events`, expect.objectContaining({
      credentials: "same-origin",
      headers: { Authorization: "Bearer secret", "Last-Event-ID": "7" },
    }));
    expect(JSON.stringify(fetcher.mock.calls)).not.toContain("?token=");
  });

  it("cancels the reader when the consumer closes the iterator", async () => {
    const cancel = vi.fn();
    const body = new ReadableStream<Uint8Array>({
      start(controller) { controller.enqueue(new TextEncoder().encode(frame(transition(1)))); },
      cancel,
    });
    const iterator = streamRunEvents(RUN_ID, {
      fetcher: vi.fn().mockResolvedValue(new Response(body, { status: 200, headers: { "content-type": "text/event-stream" } })),
    })[Symbol.asyncIterator]();

    await expect(iterator.next()).resolves.toEqual({ done: false, value: transition(1) });
    await iterator.return?.();
    expect(cancel).toHaveBeenCalledTimes(1);
  });

  it("aborts a pending read and cancels the reader", async () => {
    const cancel = vi.fn();
    const controller = new AbortController();
    const body = new ReadableStream<Uint8Array>({ cancel });
    const iterator = streamRunEvents(RUN_ID, {
      signal: controller.signal,
      fetcher: vi.fn().mockResolvedValue(new Response(body, { status: 200, headers: { "content-type": "text/event-stream" } })),
    })[Symbol.asyncIterator]();
    const pending = iterator.next();
    controller.abort();

    await expect(pending).rejects.toThrow("stream aborted");
    expect(cancel).toHaveBeenCalledTimes(1);
  });
});
