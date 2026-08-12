import { describe, expect, it, vi } from "vitest";

import { createRun, decodeRun, getRun } from "./api";

const UUID = "123e4567-e89b-42d3-a456-426614174000";
const HASH = "a".repeat(64);
const NIL_UUID = "00000000-0000-0000-0000-000000000001";

function run(events: unknown[] = []): Record<string, unknown> {
  return {
    run_id: UUID,
    request_id: UUID,
    task_level: "L1",
    profile: "fixture",
    corpus_manifest_id: HASH,
    status: "running",
    reason: null,
    created_at: "2026-08-12T00:00:00Z",
    started_at: "2026-08-12T00:00:01Z",
    completed_at: null,
    events,
  };
}

describe("decodeRun", () => {
  it("rejects a trace containing plaintext fields", () => {
    expect(() =>
      decodeRun(
        run([
          {
            kind: "tool_finished",
            sequence: 1,
            step_id: "step-1",
            tool: "get_clause",
            argument_keys: ["document_id"],
            result_count: 1,
            duration_ms: 3,
            retry_count: 0,
            error_code: null,
            query: "hidden",
          },
        ]),
      ),
    ).toThrow("unexpected trace field");
  });

  it.each([
    ["unknown event", { kind: "future_event", sequence: 1 }],
    ["nested plaintext", { kind: "evidence_summary", sequence: 1, evidence: [{ evidence_id: HASH, content_hash: HASH, excerpt: "hidden" }] }],
    ["disguised count", { kind: "tool_finished", sequence: 1, step_id: "s", tool: "get_clause", argument_keys: [], result_count: "1", duration_ms: 0, retry_count: 0, error_code: null }],
    ["out-of-bound sequence", { kind: "plan_summary", sequence: 10001, plan_id: "p", step_count: 1, max_tool_calls: 1 }],
  ])("rejects %s", (_name, event) => {
    expect(() => decodeRun(run([event]))).toThrow();
  });

  it.each([
    ["blocked egress with attempt metadata", { kind: "egress_summary", sequence: 1, stage: "planning", reservation_id: UUID, ledger_id: null, admitted: false, replayed: false, request_tokens: null, request_bytes: null, cost_microunits: null, error_code: "policy_blocked" }],
    ["answered terminal with reason", { kind: "terminal", sequence: 1, status: "answered", reason: "provider_error" }],
    ["passed verifier check with fault", { kind: "verifier_summary", sequence: 1, checks: [{ evidence_id: HASH, passed: true, fault_code: "citation_fault" }], duration_ms: 1 }],
  ])("rejects invalid server invariant: %s", (_name, event) => {
    expect(() => decodeRun(run([event]))).toThrow();
  });

  it("accepts the complete closed event union", () => {
    const events = [
      { kind: "state_transition", sequence: 1, previous_status: "queued", status: "running", reason: null },
      { kind: "plan_summary", sequence: 2, plan_id: "plan-1", step_count: 1, max_tool_calls: 1 },
      { kind: "agent_step", sequence: 3, agent: "orchestrator", step_id: "step-1", phase: "finished", duration_ms: 2, error_code: null },
      { kind: "tool_finished", sequence: 4, step_id: "step-1", tool: "search_clauses", argument_keys: ["query"], result_count: 1, duration_ms: 2, retry_count: 0, error_code: null },
      { kind: "candidate_summary", sequence: 5, candidates: [{ candidate_id: "candidate-1", score: 0.5 }] },
      { kind: "evidence_summary", sequence: 6, evidence: [{ evidence_id: HASH, content_hash: HASH }] },
      { kind: "egress_summary", sequence: 7, stage: "planning", reservation_id: UUID, ledger_id: UUID, admitted: true, replayed: false, request_tokens: 2, request_bytes: 10, cost_microunits: null, error_code: null },
      { kind: "usage_summary", sequence: 8, stage: "planning", prompt_tokens: 2, completion_tokens: 1, request_bytes: 10, duration_ms: 4, cost_microunits: 3 },
      { kind: "answer_outcome", sequence: 9, verdict: "answered", refusal_reason: null, provider_error: null, reservation_id: UUID, replayed: false, parse_fault_code: null },
      { kind: "verifier_summary", sequence: 10, checks: [{ evidence_id: HASH, passed: true, fault_code: null }], duration_ms: 2 },
      { kind: "terminal", sequence: 11, status: "answered", reason: null },
    ];
    const decoded = decodeRun({ ...run(events), status: "answered", completed_at: "2026-08-12T00:00:02Z" });
    expect(decoded.events.map((event) => event.kind)).toHaveLength(11);
  });

  it("accepts the canonical UUID forms emitted by Python", () => {
    const decoded = decodeRun({ ...run(), run_id: NIL_UUID, request_id: NIL_UUID });
    expect(decoded.run_id).toBe(NIL_UUID);
  });

  it.each([
    ["non-RFC3339 timestamp", { created_at: "August 12, 2026" }],
    ["timezone-free timestamp", { created_at: "2026-08-12T00:00:00" }],
    ["start before creation", { started_at: "2026-08-11T23:59:59Z" }],
    ["completion before start", { completed_at: "2026-08-12T00:00:00Z" }],
    ["queued with a start", { status: "queued", started_at: "2026-08-12T00:00:01Z" }],
    ["running without a start", { started_at: null }],
    ["running with a reason", { reason: "provider_timeout" }],
    ["answered with a reason", { status: "answered", reason: "provider_timeout", completed_at: "2026-08-12T00:00:02Z" }],
    ["refused with provider reason", { status: "refused", reason: "provider_timeout", completed_at: "2026-08-12T00:00:02Z" }],
    ["interrupted with completion", { status: "interrupted", reason: "lease_expired", completed_at: "2026-08-12T00:00:02Z" }],
  ])("rejects RunView invariant: %s", (_name, mutation) => {
    expect(() => decodeRun({ ...run(), ...mutation })).toThrow();
  });
});

describe("HTTP client", () => {
  it("keeps bearer credentials in the Authorization header and UUID in an encoded path", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify(run()), { status: 200, headers: { "content-type": "application/json" } }));
    await getRun(UUID, { token: "secret", fetcher });
    expect(fetcher).toHaveBeenCalledWith(`/runs/${encodeURIComponent(UUID)}`, expect.objectContaining({ credentials: "same-origin", headers: { Authorization: "Bearer secret" } }));
    expect(JSON.stringify(fetcher.mock.calls)).not.toContain("?token");
  });

  it("passes the caller's abort signal through by identity", async () => {
    const controller = new AbortController();
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify(run()), { status: 200 }));
    await getRun(UUID, { fetcher, signal: controller.signal });
    expect(fetcher).toHaveBeenCalledWith(
      `/runs/${UUID}`,
      expect.objectContaining({ signal: controller.signal }),
    );
  });

  it("sanitizes abort failures", async () => {
    const fetcher = vi.fn().mockRejectedValue(new DOMException("credential secret", "AbortError"));
    await expect(getRun(UUID, { fetcher, signal: new AbortController().signal }))
      .rejects.toThrow("run request unavailable");
    await expect(getRun(UUID, { fetcher })).rejects.not.toThrow("secret");
  });

  it("rejects a non-UUID path identifier before fetching", async () => {
    const fetcher = vi.fn();
    await expect(getRun("../runs?token=secret", { fetcher })).rejects.toThrow("invalid run id");
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("accepts a canonical nil-version UUID path without normalization", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ...run(), run_id: NIL_UUID }), { status: 200, headers: { "content-type": "application/json" } }));
    await getRun(NIL_UUID, { fetcher });
    expect(fetcher).toHaveBeenCalledWith(`/runs/${NIL_UUID}`, expect.anything());
  });

  it("rejects uppercase UUID path input rather than transforming it", async () => {
    await expect(getRun(UUID.toUpperCase(), { fetcher: vi.fn() })).rejects.toThrow("invalid run id");
  });

  it("forces the Playwright fixture server profile despite a real parent environment", async () => {
    vi.stubEnv("SPECPILOT_API_PROFILE", "real");
    const config = (await import("../playwright.config")).default;
    expect(config.webServer).toMatchObject({
      env: { SPECPILOT_API_PROFILE: "fixture", SPECPILOT_API_BIND_HOST: "127.0.0.1" },
    });
    vi.unstubAllEnvs();
  });

  it("uses the repository virtualenv Python for the Playwright fixture server by default", async () => {
    vi.stubEnv("SPECPILOT_PYTHON", undefined);
    vi.resetModules();
    const config = (await import("../playwright.config")).default;
    expect(config.webServer).toMatchObject({
      command: ".venv/bin/python -m uvicorn tests.browser.fixture_app:create_fixture_app --factory --host 127.0.0.1 --port 8765",
    });
    vi.unstubAllEnvs();
  });

  it("uses SPECPILOT_PYTHON for the Playwright fixture server when provided", async () => {
    vi.stubEnv("SPECPILOT_PYTHON", "python");
    vi.resetModules();
    const config = (await import("../playwright.config")).default;
    expect(config.webServer).toMatchObject({
      command: "python -m uvicorn tests.browser.fixture_app:create_fixture_app --factory --host 127.0.0.1 --port 8765",
    });
    vi.unstubAllEnvs();
  });

  it("creates a run without placing credentials in the URL", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ run_id: UUID, status: "queued" }), { status: 202, headers: { "content-type": "application/json" } }));
    await createRun({ question: "question", request_id: UUID, evaluation_root_id: "root-1", task_level: "L1", source_manifest_id: HASH, corpus_manifest_id: HASH }, { token: "secret", fetcher });
    expect(fetcher).toHaveBeenCalledWith("/chat", expect.objectContaining({ method: "POST", credentials: "same-origin", headers: expect.objectContaining({ Authorization: "Bearer secret" }) }));
  });

  it("sanitizes response failures instead of exposing bodies", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response("provider secret", { status: 503 }));
    await expect(getRun(UUID, { fetcher })).rejects.toThrow("run request failed (503)");
    await expect(getRun(UUID, { fetcher })).rejects.not.toThrow("provider secret");
  });
});
