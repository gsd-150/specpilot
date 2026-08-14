import { decodeRunEvent, type ClientOptions, type RunEvent } from "./api";

export const MAX_SSE_FRAME_BYTES = 64 * 1024;
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const EVENT_ID_RE = /^(?:0|[1-9][0-9]{0,4})$/;

export class StreamProtocolError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "StreamProtocolError";
  }
}

export interface RunStreamOptions extends ClientOptions {
  afterSequence?: number;
}

interface ParserState {
  bytes: number;
  id: string | null;
  event: string | null;
  data: string | null;
  pendingLine: string;
  lastSequence: number;
  lastFingerprint: string | null;
}

function resetFrame(state: ParserState): void {
  state.bytes = 0;
  state.id = null;
  state.event = null;
  state.data = null;
}

function parseLine(state: ParserState, rawLine: string): RunEvent | null {
  const line = rawLine.endsWith("\r") ? rawLine.slice(0, -1) : rawLine;
  if (line === "") {
    if (state.id === null && state.event === null && state.data === null) {
      resetFrame(state);
      return null;
    }
    if (state.id === null || state.event === null || state.data === null) throw new StreamProtocolError("invalid SSE frame");
    if (!EVENT_ID_RE.test(state.id) || Number(state.id) > 10_000) throw new StreamProtocolError("invalid SSE event id");
    const id = Number(state.id);
    let decoded: unknown;
    try {
      decoded = JSON.parse(state.data) as unknown;
    } catch {
      throw new StreamProtocolError("invalid SSE data");
    }
    let event: RunEvent;
    try {
      event = decodeRunEvent(decoded);
    } catch {
      throw new StreamProtocolError("invalid SSE event data");
    }
    if (event.kind !== state.event || event.sequence !== id) throw new StreamProtocolError("invalid SSE frame identity");
    const fingerprint = `${state.event}\n${state.data}`;
    if (id < state.lastSequence) throw new StreamProtocolError("invalid SSE sequence decrease");
    if (id === state.lastSequence) {
      if (state.lastFingerprint !== fingerprint) throw new StreamProtocolError("invalid SSE sequence conflict");
      resetFrame(state);
      return null;
    }
    if (id !== state.lastSequence + 1) throw new StreamProtocolError("invalid SSE sequence gap");
    state.lastSequence = id;
    state.lastFingerprint = fingerprint;
    resetFrame(state);
    return event;
  }
  if (line.startsWith(":")) return null;
  if (line.startsWith("id: ")) {
    if (state.id !== null) throw new StreamProtocolError("invalid SSE frame");
    state.id = line.slice(4);
    return null;
  }
  if (line.startsWith("event: ")) {
    if (state.event !== null) throw new StreamProtocolError("invalid SSE frame");
    state.event = line.slice(7);
    return null;
  }
  if (line.startsWith("data: ")) {
    if (state.data !== null) throw new StreamProtocolError("invalid SSE frame");
    state.data = line.slice(6);
    return null;
  }
  throw new StreamProtocolError("invalid SSE field");
}

function* feedBytes(
  state: ParserState,
  decoder: TextDecoder,
  bytes: Uint8Array,
): Generator<RunEvent> {
  let start = 0;
  while (start < bytes.byteLength) {
    const relativeNewline = bytes.subarray(start).indexOf(0x0a);
    const end = relativeNewline < 0 ? bytes.byteLength : start + relativeNewline + 1;
    const segment = bytes.subarray(start, end);
    state.bytes += segment.byteLength;
    if (state.bytes > MAX_SSE_FRAME_BYTES) throw new StreamProtocolError("SSE frame too large");
    let text: string;
    try {
      text = decoder.decode(segment, { stream: true });
    } catch {
      throw new StreamProtocolError("invalid SSE encoding");
    }
    state.pendingLine += text;
    if (relativeNewline < 0) return;
    if (!state.pendingLine.endsWith("\n")) throw new StreamProtocolError("invalid SSE encoding");
    const line = state.pendingLine.slice(0, -1);
    state.pendingLine = "";
    const event = parseLine(state, line);
    if (event !== null) yield event;
    start = end;
  }
}

function readWithAbort(
  reader: ReadableStreamDefaultReader<Uint8Array>,
  signal: AbortSignal | undefined,
): Promise<ReadableStreamReadResult<Uint8Array>> {
  if (signal?.aborted) return Promise.reject(new Error("stream aborted"));
  if (signal === undefined) return reader.read();
  return new Promise((resolve, reject) => {
    const abort = (): void => reject(new Error("stream aborted"));
    signal.addEventListener("abort", abort, { once: true });
    reader.read().then(resolve, reject).finally(() => signal.removeEventListener("abort", abort));
  });
}

export async function* streamRunEvents(
  runId: string,
  options: RunStreamOptions = {},
): AsyncIterable<RunEvent> {
  if (!UUID_RE.test(runId)) throw new Error("invalid run id");
  const afterSequence = options.afterSequence ?? 0;
  if (!Number.isSafeInteger(afterSequence) || afterSequence < 0 || afterSequence > 10_000) throw new Error("invalid stream cursor");
  const headers: Record<string, string> = { "Last-Event-ID": String(afterSequence) };
  if (options.token !== undefined) headers.Authorization = `Bearer ${options.token}`;
  const fetcher = options.fetcher ?? fetch;
  let response: Response;
  try {
    response = await fetcher(`/runs/${encodeURIComponent(runId)}/events`, {
      method: "GET",
      credentials: "same-origin",
      headers,
      ...(options.signal === undefined ? {} : { signal: options.signal }),
    });
  } catch {
    if (options.signal?.aborted) throw new Error("stream aborted");
    throw new Error("stream request unavailable");
  }
  if (!response.ok) throw new Error(`stream request failed (${response.status})`);
  const mediaType = response.headers.get("content-type")?.split(";", 1)[0]?.trim().toLowerCase();
  if (mediaType !== "text/event-stream" || response.body === null) {
    throw new StreamProtocolError("stream response was invalid");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8", { fatal: true });
  const state: ParserState = {
    bytes: 0,
    id: null,
    event: null,
    data: null,
    pendingLine: "",
    lastSequence: afterSequence,
    lastFingerprint: null,
  };
  try {
    while (true) {
      const { done, value } = await readWithAbort(reader, options.signal);
      if (done) break;
      yield* feedBytes(state, decoder, value);
    }
    let finalText: string;
    try {
      finalText = decoder.decode();
    } catch {
      throw new StreamProtocolError("invalid SSE encoding");
    }
    state.pendingLine += finalText;
    if (state.pendingLine !== "" || state.id !== null || state.event !== null || state.data !== null) {
      throw new StreamProtocolError("incomplete SSE frame");
    }
  } finally {
    try { await reader.cancel(); } catch { /* the fetch body may already be closed */ }
    reader.releaseLock();
  }
}
