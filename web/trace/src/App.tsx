import { useEffect, useRef, useState } from "react";

import { createRun, type ChatAccepted, type CreateRunRequest } from "./api";
import { StatusPanel } from "./components/StatusPanel";
import { TraceTimeline } from "./components/TraceTimeline";
import type { RunPollingOptions, RunPollingResult } from "./useRunPolling";
import { useRunStream } from "./useRunStream";

const QUESTION_LIMIT = 8_192;
const DEFAULT_INTERVAL_MS = 1_000;
const DEFAULT_DEADLINE_MS = 60_000;

export interface AppApi {
  createRun: (request: CreateRunRequest, options?: { token?: string }) => Promise<ChatAccepted>;
}
export type PollingHook = (options: RunPollingOptions) => RunPollingResult;
export interface AppProps {
  api?: AppApi;
  usePolling?: PollingHook;
  token?: string;
  sourceManifestId: string;
  corpusManifestId: string;
}

function createError(error: unknown): string {
  const message = error instanceof Error ? error.message : "";
  if (message === "chat request failed (401)") return "Session authorization failed.";
  if (message === "chat request failed (422)") return "Check the request fields and try again.";
  if (message === "chat request failed (503)") return "The run queue is busy. Try again shortly.";
  if (message === "chat request unavailable") return "The service could not be reached.";
  return "The run could not be created.";
}

function ActiveRun({ runId, token, polling }: { runId: string; token?: string; polling: PollingHook }) {
  const { serverRun, connectionState, refresh } = polling({ runId, token, intervalMs: DEFAULT_INTERVAL_MS, deadlineMs: DEFAULT_DEADLINE_MS });
  if (serverRun === null) {
    const message = connectionState === "connecting" ? "Connecting to the sanitized trace…" : {
      unauthorized: "Session authorization failed.", not_found: "Run is unavailable.", service_unavailable: "Trace service is unavailable.", network_error: "Network connection failed.", invalid_response: "Trace response was rejected.", poll_timeout: "Connection limit reached.", stream_unavailable: "Live trace is unavailable. Use Refresh for a current snapshot.", connected: "Waiting for the first trace snapshot…",
    }[connectionState];
    return <section className="trace-shell"><div className="connection-alert" role={connectionState === "connecting" || connectionState === "connected" ? "status" : "alert"}>{message}<button className="button button--quiet" type="button" onClick={() => void refresh()}>Refresh trace</button></div></section>;
  }
  return (
    <div className="trace-shell">
      <div className="run-identity"><div><span>Run ID</span><code>{serverRun.run_id}</code></div><div><span>Profile</span><code>{serverRun.profile}</code></div></div>
      <StatusPanel status={serverRun.status} reason={serverRun.reason} connectionState={connectionState} onRefresh={refresh} />
      <TraceTimeline events={serverRun.events} />
    </div>
  );
}

export function App({ api = { createRun }, usePolling: polling = useRunStream, token, sourceManifestId, corpusManifestId }: AppProps) {
  const [question, setQuestion] = useState("");
  const [runId, setRunId] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const mounted = useRef(false);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  const submit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const bounded = question.trim();
    if (submitting || bounded.length === 0) return;
    setSubmitting(true);
    setError(null);
    const request: CreateRunRequest = {
      question: bounded,
      request_id: crypto.randomUUID(),
      evaluation_root_id: crypto.randomUUID(),
      task_level: "L1",
      source_manifest_id: sourceManifestId,
      corpus_manifest_id: corpusManifestId,
    };
    try {
      const accepted = await api.createRun(request, token === undefined ? {} : { token });
      if (!mounted.current) return;
      setQuestion("");
      setRunId(accepted.run_id);
    } catch (caught) {
      if (mounted.current) setError(createError(caught));
    } finally {
      if (mounted.current) setSubmitting(false);
    }
  };

  return (
    <main>
      <header className="hero"><div className="brand-mark" aria-hidden="true">SP</div><div><p className="eyebrow">Verifiable clause QA</p><h1>SpecPilot trace</h1><p className="hero__copy">Ask one bounded L1 question, then inspect the disclosure and verification decisions without exposing source text.</p></div></header>
      <section className="question-card" aria-labelledby="question-title">
        <div><p className="eyebrow">New run</p><h2 id="question-title">Ask a specification question</h2></div>
        <form onSubmit={(event) => void submit(event)}>
          <label htmlFor="question">L1 question</label>
          <textarea id="question" maxLength={QUESTION_LIMIT} rows={4} value={question} onChange={(event) => setQuestion(event.target.value.slice(0, QUESTION_LIMIT))} autoComplete="off" spellCheck="false" placeholder="Ask about a requirement or protocol behavior…" />
          <div className="form-footer"><span>{question.length.toLocaleString()} / {QUESTION_LIMIT.toLocaleString()}</span><button className="button button--primary" type="submit" disabled={submitting || question.trim().length === 0}>{submitting ? "Starting…" : "Run L1"}</button></div>
          {error === null ? null : <p className="form-error" role="alert">{error}</p>}
        </form>
      </section>
      {runId === null ? <section className="empty-card"><p className="eyebrow">Trace workspace</p><h2>No active run</h2><p>Submitted questions are cleared after acceptance and are never kept in page history.</p></section> : <ActiveRun key={runId} runId={runId} token={token} polling={polling} />}
    </main>
  );
}
