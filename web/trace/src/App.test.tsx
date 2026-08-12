// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App, type AppApi, type PollingHook } from "./App";
import type { RunEvent, RunStatus, RunView } from "./api";

const UUID = "123e4567-e89b-42d3-a456-426614174000";
const HASH = "a".repeat(64);
const ANSWER_FAULT_CODES = [
  "reply_too_large",
  "reply_not_json",
  "reply_not_an_object",
  "reply_missing_sufficient",
  "reply_missing_answer",
  "reply_answer_too_long",
  "reply_missing_citations",
  "reply_too_many_citations",
  "reply_citation_not_a_string",
  "reply_citation_malformed",
  "reply_unreadable",
  "not_disclosed",
  "cross_manifest",
  "no_citation",
] as const;

function view(status: RunStatus, reason: string | null = null, events: RunEvent[] = []): RunView {
  const terminal = !["queued", "running", "interrupted"].includes(status);
  return {
    run_id: UUID,
    request_id: UUID,
    task_level: "L1",
    profile: "fixture",
    corpus_manifest_id: HASH,
    status,
    reason,
    created_at: "2026-08-12T00:00:00Z",
    started_at: status === "queued" ? null : "2026-08-12T00:00:01Z",
    completed_at: terminal ? "2026-08-12T00:00:02Z" : null,
    events,
  };
}

function harness(serverRun: RunView | null, connectionState: ReturnType<PollingHook>["connectionState"] = "connected") {
  const create = vi.fn().mockResolvedValue({ run_id: UUID, status: "queued" as const });
  const api: AppApi = { createRun: create };
  const polling: PollingHook = () => ({ serverRun, connectionState, refresh: vi.fn() });
  return { api, polling, create };
}

async function submit(h: ReturnType<typeof harness>, question = "What does the RFC require?") {
  render(<App api={h.api} usePolling={h.polling} sourceManifestId={HASH} corpusManifestId={HASH} />);
  const input = screen.getByLabelText("L1 question");
  fireEvent.change(input, { target: { value: question } });
  fireEvent.click(screen.getByRole("button", { name: "Run L1" }));
  await waitFor(() => expect(h.create).toHaveBeenCalledOnce());
  return input;
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  localStorage.clear();
  sessionStorage.clear();
  history.replaceState(null, "", "/");
});

describe("run form", () => {
  it("clears the bounded question immediately after an accepted run without retaining it", async () => {
    const h = harness(view("queued"));
    const input = await submit(h, "private question marker");
    expect(input).toHaveValue("");
    expect(document.body).not.toHaveTextContent("private question marker");
    expect(location.href).not.toContain("private");
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
    expect(JSON.stringify(history.state)).not.toContain("private");
  });

  it("retains the question for correction after a sanitized validation failure", async () => {
    const create = vi.fn().mockRejectedValue(new Error("chat request failed (422)"));
    const api: AppApi = { createRun: create };
    render(<App api={api} usePolling={() => ({ serverRun: null, connectionState: "connecting", refresh: vi.fn() })} sourceManifestId={HASH} corpusManifestId={HASH} />);
    fireEvent.change(screen.getByLabelText("L1 question"), { target: { value: "fix me" } });
    fireEvent.click(screen.getByRole("button", { name: "Run L1" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Check the request fields");
    expect(screen.getByLabelText("L1 question")).toHaveValue("fix me");
  });

  it("coalesces double submit and never sends a question beyond the API bound", async () => {
    let resolve!: (value: { run_id: string; status: "queued" }) => void;
    const create = vi.fn(() => new Promise<{ run_id: string; status: "queued" }>((done) => { resolve = done; }));
    render(<App api={{ createRun: create }} usePolling={() => ({ serverRun: null, connectionState: "connecting", refresh: vi.fn() })} sourceManifestId={HASH} corpusManifestId={HASH} />);
    const input = screen.getByLabelText("L1 question");
    fireEvent.change(input, { target: { value: "q".repeat(9_000) } });
    expect(input).toHaveValue("q".repeat(8_192));
    const button = screen.getByRole("button", { name: "Run L1" });
    fireEvent.click(button);
    fireEvent.click(button);
    expect(create).toHaveBeenCalledOnce();
    resolve({ run_id: UUID, status: "queued" });
    await waitFor(() => expect(input).toHaveValue(""));
    expect(button).toBeDisabled();
  });

  it("uses cookie mode when no bearer credential is supplied and never persists a supplied credential", async () => {
    const h = harness(view("queued"));
    const { unmount } = render(<App api={h.api} usePolling={h.polling} token="bearer-marker" sourceManifestId={HASH} corpusManifestId={HASH} />);
    expect(document.documentElement.outerHTML).not.toContain("bearer-marker");
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
    unmount();
  });

  it.each([
    ["chat request failed (401)", "Session authorization failed"],
    ["chat request failed (503)", "The run queue is busy"],
    ["chat request unavailable", "The service could not be reached"],
  ])("renders a stable create error for %s", async (failure, copy) => {
    const create = vi.fn().mockRejectedValue(new Error(failure));
    render(<App api={{ createRun: create }} usePolling={() => ({ serverRun: null, connectionState: "connecting", refresh: vi.fn() })} sourceManifestId={HASH} corpusManifestId={HASH} />);
    fireEvent.change(screen.getByLabelText("L1 question"), { target: { value: "retry me" } });
    fireEvent.click(screen.getByRole("button", { name: "Run L1" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(copy);
    expect(screen.getByLabelText("L1 question")).toHaveValue("retry me");
  });

  it("ignores a create completion after unmount", async () => {
    let resolve!: (value: { run_id: string; status: "queued" }) => void;
    const create = vi.fn(() => new Promise<{ run_id: string; status: "queued" }>((done) => { resolve = done; }));
    const error = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const rendered = render(<App api={{ createRun: create }} usePolling={() => ({ serverRun: null, connectionState: "connecting", refresh: vi.fn() })} sourceManifestId={HASH} corpusManifestId={HASH} />);
    fireEvent.change(screen.getByLabelText("L1 question"), { target: { value: "cancel me" } });
    fireEvent.click(screen.getByRole("button", { name: "Run L1" }));
    rendered.unmount();
    resolve({ run_id: UUID, status: "queued" });
    await Promise.resolve();
    expect(error).not.toHaveBeenCalled();
  });

  it("accepts a delayed create completion after the StrictMode effect replay", async () => {
    let resolve!: (value: { run_id: string; status: "queued" }) => void;
    const create = vi.fn(() => new Promise<{ run_id: string; status: "queued" }>((done) => { resolve = done; }));
    render(<StrictMode><App api={{ createRun: create }} usePolling={() => ({ serverRun: view("queued"), connectionState: "connected", refresh: vi.fn() })} sourceManifestId={HASH} corpusManifestId={HASH} /></StrictMode>);
    const input = screen.getByLabelText("L1 question");
    fireEvent.change(input, { target: { value: "strict private question" } });
    fireEvent.click(screen.getByRole("button", { name: "Run L1" }));
    expect(create).toHaveBeenCalledOnce();
    resolve({ run_id: UUID, status: "queued" });
    await waitFor(() => expect(input).toHaveValue(""));
    expect(await screen.findByText(UUID)).toBeVisible();
    expect(document.body).not.toHaveTextContent("strict private question");
  });
});

describe("terminal status semantics", () => {
  it.each([
    ["answered", null, "Answer verified"],
    ["refused", "evidence_insufficient", "The system declined to answer"],
    ["egress_blocked", "root_unique_excerpts_exceeded", "Disclosure gate blocked the send"],
    ["failed", "provider_timeout", "Provider execution failed"],
    ["interrupted", "lease_expired", "Run was interrupted"],
  ] as const)("renders %s distinctly with its stable reason", async (status, reason, copy) => {
    const h = harness(view(status, reason));
    await submit(h);
    const panel = await screen.findByRole("status", { name: "Run status" });
    expect(panel).toHaveTextContent(copy);
    if (reason !== null) expect(panel).toHaveTextContent(reason);
    expect(panel).toHaveAttribute("data-status", status);
  });

  it("shows poll timeout as a connection condition without replacing the server status", async () => {
    const h = harness(view("running"), "poll_timeout");
    await submit(h);
    expect(await screen.findByRole("status", { name: "Run status" })).toHaveTextContent("Running");
    expect(screen.getByRole("alert")).toHaveTextContent("Polling limit reached");
  });

  it.each([
    ["unauthorized", "Session authorization failed"],
    ["not_found", "Run is unavailable"],
    ["service_unavailable", "Trace service is unavailable"],
    ["network_error", "Network connection failed"],
    ["invalid_response", "Trace response was rejected"],
  ] as const)("renders sanitized %s connection copy", async (state, copy) => {
    const h = harness(null, state);
    await submit(h);
    expect(await screen.findByRole("alert")).toHaveTextContent(copy);
  });
});

describe("sanitized timeline", () => {
  it("renders explicit allowed metadata in chronological sequence", async () => {
    const events: RunEvent[] = [
      { kind: "tool_finished", sequence: 1, step_id: "s1", tool: "search_clauses", argument_keys: ["limit", "query"], result_count: 3, duration_ms: 14, retry_count: 1, error_code: "tool_timeout" },
      { kind: "egress_summary", sequence: 2, stage: "planning", reservation_id: UUID, ledger_id: UUID, admitted: true, replayed: false, request_tokens: 12, request_bytes: 88, cost_microunits: null, error_code: null },
      { kind: "evidence_summary", sequence: 3, evidence: [{ evidence_id: HASH, content_hash: "b".repeat(64) }] },
      { kind: "verifier_summary", sequence: 4, checks: [{ evidence_id: HASH, passed: false, fault_code: "unverifiable_citation" }], duration_ms: 5 },
    ];
    const h = harness(view("refused", "unverifiable_citation", events));
    await submit(h);
    const timeline = await screen.findByRole("list", { name: "Run trace" });
    const items = within(timeline).getAllByRole("listitem");
    expect(items.map((item) => item.getAttribute("data-sequence"))).toEqual(["1", "2", "3", "4"]);
    expect(timeline).toHaveTextContent("search_clauses");
    expect(timeline).toHaveTextContent("limit, query");
    expect(timeline).toHaveTextContent("3 results");
    expect(timeline).toHaveTextContent("14 ms");
    expect(timeline).toHaveTextContent("1 retry");
    expect(timeline).toHaveTextContent("tool_timeout");
    expect(timeline).toHaveTextContent(UUID);
    expect(timeline).toHaveTextContent(HASH);
    expect(timeline).toHaveTextContent("unverifiable_citation");
  });

  it("drops unknown keys and hostile nested plaintext even when malformed data bypasses TypeScript", async () => {
    const hostile = {
      kind: "tool_finished", sequence: 1, step_id: "safe", tool: "get_clause",
      argument_keys: ["document_id", { query: "nested-secret" }], result_count: 1,
      duration_ms: 1, retry_count: 0, error_code: null,
      query: "question-secret", excerpt: "excerpt-secret", candidate_body: "candidate-secret",
      provider_response: "provider-secret", path: "/Users/private", token: "token-secret",
    } as unknown as RunEvent;
    const future = { kind: "future", sequence: 2, safe: "unknown-secret" } as unknown as RunEvent;
    const h = harness(view("running", null, [hostile, future]));
    await submit(h);
    const html = (await screen.findByRole("list", { name: "Run trace" })).textContent ?? "";
    for (const marker of ["nested-secret", "question-secret", "excerpt-secret", "candidate-secret", "provider-secret", "/Users/private", "token-secret", "unknown-secret"]) {
      expect(html).not.toContain(marker);
    }
    expect(html).toContain("get_clause");
  });

  it("rejects hostile values smuggled through otherwise allowed fields", async () => {
    const events = [
      { kind: "tool_finished", sequence: 1, step_id: "/Users/private", tool: "provider-secret", argument_keys: ["token-secret"], result_count: 1, duration_ms: 1, retry_count: 0, error_code: "raw exception message" },
      { kind: "evidence_summary", sequence: 2, evidence: [{ evidence_id: "excerpt-secret", content_hash: "candidate-secret" }] },
    ] as unknown as RunEvent[];
    const h = harness(view("running", null, events));
    await submit(h);
    const html = (await screen.findByRole("list", { name: "Run trace" })).textContent ?? "";
    for (const marker of ["/Users/private", "provider-secret", "token-secret", "raw exception message", "excerpt-secret", "candidate-secret"]) expect(html).not.toContain(marker);
  });

  it("never renders provider-authored plan or step IDs even when syntactically valid", async () => {
    const events = [
      { kind: "plan_summary", sequence: 1, plan_id: "private_question_marker", step_count: 1, max_tool_calls: 1 },
      { kind: "agent_step", sequence: 2, agent: "orchestrator", step_id: "api_key_secret", phase: "finished", duration_ms: 1, error_code: null },
      { kind: "tool_finished", sequence: 3, step_id: "api_key_secret", tool: "search_clauses", argument_keys: ["query", "private_question_marker"], result_count: 1, duration_ms: 1, retry_count: 0, error_code: "provider_authored_error" },
    ] as unknown as RunEvent[];
    const h = harness(view("running", null, events));
    await submit(h);
    const html = (await screen.findByRole("list", { name: "Run trace" })).textContent ?? "";
    for (const marker of ["private_question_marker", "api_key_secret", "provider_authored_error"]) expect(html).not.toContain(marker);
    expect(html).toContain("search_clauses");
    expect(html).toContain("query");
  });

  it.each(ANSWER_FAULT_CODES)("renders the real closed answer fault %s", async (faultCode) => {
    const events = [{
      kind: "verifier_summary", sequence: 1,
      checks: [{ evidence_id: HASH, passed: false, fault_code: faultCode }],
      duration_ms: 1,
    }] as unknown as RunEvent[];
    const h = harness(view("refused", "unverifiable_citation", events));
    await submit(h);
    expect(await screen.findByRole("list", { name: "Run trace" })).toHaveTextContent(faultCode);
  });

  it.each([
    "reply_not_json_private",
    "not_disclosed_secret",
    "cross_manifest_raw",
    "no_citations",
    "provider_timeout_detail",
    "root_unique_excerpts_exceeded_extra",
  ])("does not render the similar but unknown code %s", async (faultCode) => {
    const events = [{
      kind: "verifier_summary", sequence: 1,
      checks: [{ evidence_id: HASH, passed: false, fault_code: faultCode }],
      duration_ms: 1,
    }] as unknown as RunEvent[];
    const h = harness(view("refused", "unverifiable_citation", events));
    await submit(h);
    expect((await screen.findByRole("list", { name: "Run trace" })).textContent).not.toContain(faultCode);
  });
});
