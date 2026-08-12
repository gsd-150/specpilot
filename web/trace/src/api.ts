export type RunStatus = "queued" | "running" | "answered" | "refused" | "egress_blocked" | "failed" | "interrupted";
export type TerminalStatus = Exclude<RunStatus, "queued" | "running">;
export type EgressStage = "planning" | "evidence" | "compliance" | "verifier" | "judge";

type StateTransition = { kind: "state_transition"; sequence: number; previous_status: RunStatus | null; status: RunStatus; reason: string | null };
type PlanSummary = { kind: "plan_summary"; sequence: number; plan_id: string; step_count: number; max_tool_calls: number };
type AgentStep = { kind: "agent_step"; sequence: number; agent: "orchestrator" | "evidence_agent" | "answer" | "verifier"; step_id: string; phase: "started" | "finished"; duration_ms: number | null; error_code: string | null };
type ToolFinished = { kind: "tool_finished"; sequence: number; step_id: string; tool: "search_clauses" | "get_clause" | "get_toc" | "expand_references" | "lookup_term"; argument_keys: string[]; result_count: number; duration_ms: number; retry_count: number; error_code: string | null };
type CandidateSummary = { kind: "candidate_summary"; sequence: number; candidates: Array<{ candidate_id: string; score: number }> };
type EvidenceSummary = { kind: "evidence_summary"; sequence: number; evidence: Array<{ evidence_id: string; content_hash: string }> };
type EgressSummary = { kind: "egress_summary"; sequence: number; stage: EgressStage; reservation_id: string | null; ledger_id: string | null; admitted: boolean; replayed: boolean; request_tokens: number | null; request_bytes: number | null; cost_microunits: number | null; error_code: string | null };
type UsageSummary = { kind: "usage_summary"; sequence: number; stage: EgressStage; prompt_tokens: number; completion_tokens: number; request_bytes: number; duration_ms: number; cost_microunits: number };
type AnswerOutcome = { kind: "answer_outcome"; sequence: number; verdict: "answered" | "refused"; refusal_reason: string | null; provider_error: string | null; reservation_id: string | null; replayed: boolean; parse_fault_code: string | null };
type VerifierSummary = { kind: "verifier_summary"; sequence: number; checks: Array<{ evidence_id: string | null; passed: boolean; fault_code: string | null }>; duration_ms: number };
type Terminal = { kind: "terminal"; sequence: number; status: TerminalStatus; reason: string | null };

export type RunEvent = StateTransition | PlanSummary | AgentStep | ToolFinished | CandidateSummary | EvidenceSummary | EgressSummary | UsageSummary | AnswerOutcome | VerifierSummary | Terminal;

export interface RunView {
  run_id: string;
  request_id: string;
  task_level: "L1";
  profile: string;
  corpus_manifest_id: string;
  status: RunStatus;
  reason: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  events: RunEvent[];
}

export interface CreateRunRequest {
  question: string;
  request_id: string;
  evaluation_root_id: string;
  task_level: "L1";
  source_manifest_id: string;
  corpus_manifest_id: string;
}

export interface ChatAccepted { run_id: string; status: "queued" }
type Fetcher = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;
export interface ClientOptions { token?: string; fetcher?: Fetcher }

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const HASH_RE = /^[0-9a-f]{64}$/;
const IDENT_RE = /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$/;
const REASON_RE = /^[a-z][a-z0-9_]{0,63}$/;
const ARGUMENT_RE = /^[A-Za-z][A-Za-z0-9_]{0,63}$/;
const STATUSES = ["queued", "running", "answered", "refused", "egress_blocked", "failed", "interrupted"] as const;
const TERMINAL = ["answered", "refused", "egress_blocked", "failed", "interrupted"] as const;
const STAGES = ["planning", "evidence", "compliance", "verifier", "judge"] as const;

function object(value: unknown): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) throw new Error("invalid trace object");
  return value as Record<string, unknown>;
}
function exact(value: unknown, keys: readonly string[]): Record<string, unknown> {
  const record = object(value);
  const actual = Object.keys(record);
  if (actual.length !== keys.length || actual.some((key) => !keys.includes(key))) throw new Error("unexpected trace field");
  return record;
}
function string(value: unknown, label: string, pattern: RegExp, max = 128): string {
  if (typeof value !== "string" || value.length < 1 || value.length > max || value.trim() !== value || !pattern.test(value)) throw new Error(`invalid ${label}`);
  return value;
}
function nullableString(value: unknown, label: string, pattern: RegExp, max = 128): string | null {
  return value === null ? null : string(value, label, pattern, max);
}
function enumeration<T extends string>(value: unknown, choices: readonly T[], label: string): T {
  if (typeof value !== "string" || !choices.includes(value as T)) throw new Error(`invalid ${label}`);
  return value as T;
}
function integer(value: unknown, label: string, min: number, max: number): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < min || value > max) throw new Error(`invalid ${label}`);
  return value;
}
function number(value: unknown, label: string, min: number, max: number): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value < min || value > max) throw new Error(`invalid ${label}`);
  return value;
}
function bool(value: unknown, label: string): boolean { if (typeof value !== "boolean") throw new Error(`invalid ${label}`); return value; }
function uuid(value: unknown, label: string): string { return string(value, label, UUID_RE, 36); }
function nullableUuid(value: unknown, label: string): string | null { return value === null ? null : uuid(value, label); }
function hash(value: unknown, label: string): string { return string(value, label, HASH_RE, 64); }
function identifier(value: unknown, label: string): string { return string(value, label, IDENT_RE); }
function reason(value: unknown, label: string): string | null { return nullableString(value, label, REASON_RE, 64); }
function timestamp(value: unknown, label: string): string {
  if (typeof value !== "string" || !/(?:Z|[+-]\d{2}:\d{2})$/.test(value) || !Number.isFinite(Date.parse(value))) throw new Error(`invalid ${label}`);
  return value;
}
function nullableTimestamp(value: unknown, label: string): string | null { return value === null ? null : timestamp(value, label); }
function list(value: unknown, label: string, max: number): unknown[] { if (!Array.isArray(value) || value.length > max) throw new Error(`invalid ${label}`); return value; }
function nullableCount(value: unknown, label: string, max = 1_000_000): number | null { return value === null ? null : integer(value, label, 0, max); }

function decodeEvent(value: unknown): RunEvent {
  const head = object(value);
  const kind = head.kind;
  const sequence = integer(head.sequence, "sequence", 1, 10_000);
  switch (kind) {
    case "state_transition": { const r = exact(value, ["kind", "sequence", "previous_status", "status", "reason"]); return { kind, sequence, previous_status: r.previous_status === null ? null : enumeration(r.previous_status, STATUSES, "previous status"), status: enumeration(r.status, STATUSES, "status"), reason: reason(r.reason, "reason") }; }
    case "plan_summary": { const r = exact(value, ["kind", "sequence", "plan_id", "step_count", "max_tool_calls"]); return { kind, sequence, plan_id: identifier(r.plan_id, "plan id"), step_count: integer(r.step_count, "step count", 1, 4), max_tool_calls: integer(r.max_tool_calls, "max tool calls", 1, 6) }; }
    case "agent_step": { const r = exact(value, ["kind", "sequence", "agent", "step_id", "phase", "duration_ms", "error_code"]); return { kind, sequence, agent: enumeration(r.agent, ["orchestrator", "evidence_agent", "answer", "verifier"] as const, "agent"), step_id: identifier(r.step_id, "step id"), phase: enumeration(r.phase, ["started", "finished"] as const, "phase"), duration_ms: nullableCount(r.duration_ms, "duration", 3_600_000), error_code: reason(r.error_code, "error code") }; }
    case "tool_finished": { const r = exact(value, ["kind", "sequence", "step_id", "tool", "argument_keys", "result_count", "duration_ms", "retry_count", "error_code"]); return { kind, sequence, step_id: identifier(r.step_id, "step id"), tool: enumeration(r.tool, ["search_clauses", "get_clause", "get_toc", "expand_references", "lookup_term"] as const, "tool"), argument_keys: list(r.argument_keys, "argument keys", 16).map((v) => string(v, "argument key", ARGUMENT_RE, 64)), result_count: integer(r.result_count, "result count", 0, 1_000_000), duration_ms: integer(r.duration_ms, "duration", 0, 3_600_000), retry_count: integer(r.retry_count, "retry count", 0, 1), error_code: reason(r.error_code, "error code") }; }
    case "candidate_summary": { const r = exact(value, ["kind", "sequence", "candidates"]); return { kind, sequence, candidates: list(r.candidates, "candidates", 20).map((item) => { const c = exact(item, ["candidate_id", "score"]); return { candidate_id: identifier(c.candidate_id, "candidate id"), score: number(c.score, "score", -1e12, 1e12) }; }) }; }
    case "evidence_summary": { const r = exact(value, ["kind", "sequence", "evidence"]); return { kind, sequence, evidence: list(r.evidence, "evidence", 5).map((item) => { const e = exact(item, ["evidence_id", "content_hash"]); return { evidence_id: hash(e.evidence_id, "evidence id"), content_hash: hash(e.content_hash, "content hash") }; }) }; }
    case "egress_summary": { const r = exact(value, ["kind", "sequence", "stage", "reservation_id", "ledger_id", "admitted", "replayed", "request_tokens", "request_bytes", "cost_microunits", "error_code"]); const event: EgressSummary = { kind, sequence, stage: enumeration(r.stage, STAGES, "stage"), reservation_id: nullableUuid(r.reservation_id, "reservation id"), ledger_id: nullableUuid(r.ledger_id, "ledger id"), admitted: bool(r.admitted, "admitted"), replayed: bool(r.replayed, "replayed"), request_tokens: nullableCount(r.request_tokens, "request tokens"), request_bytes: nullableCount(r.request_bytes, "request bytes"), cost_microunits: nullableCount(r.cost_microunits, "cost", 1_000_000_000), error_code: reason(r.error_code, "error code") }; if (event.admitted ? event.reservation_id === null || event.error_code !== null || ((event.request_tokens === null) !== (event.request_bytes === null)) : event.error_code === null || event.reservation_id !== null || event.ledger_id !== null || event.replayed || event.request_tokens !== null || event.request_bytes !== null || event.cost_microunits !== null) throw new Error("invalid egress summary"); return event; }
    case "usage_summary": { const r = exact(value, ["kind", "sequence", "stage", "prompt_tokens", "completion_tokens", "request_bytes", "duration_ms", "cost_microunits"]); return { kind, sequence, stage: enumeration(r.stage, STAGES, "stage"), prompt_tokens: integer(r.prompt_tokens, "prompt tokens", 0, 1_000_000), completion_tokens: integer(r.completion_tokens, "completion tokens", 0, 1_000_000), request_bytes: integer(r.request_bytes, "request bytes", 0, 1_000_000), duration_ms: integer(r.duration_ms, "duration", 0, 3_600_000), cost_microunits: integer(r.cost_microunits, "cost", 0, 1_000_000_000) }; }
    case "answer_outcome": { const r = exact(value, ["kind", "sequence", "verdict", "refusal_reason", "provider_error", "reservation_id", "replayed", "parse_fault_code"]); const event: AnswerOutcome = { kind, sequence, verdict: enumeration(r.verdict, ["answered", "refused"] as const, "verdict"), refusal_reason: reason(r.refusal_reason, "refusal reason"), provider_error: reason(r.provider_error, "provider error"), reservation_id: nullableUuid(r.reservation_id, "reservation id"), replayed: bool(r.replayed, "replayed"), parse_fault_code: reason(r.parse_fault_code, "parse fault code") }; if ((event.verdict === "answered" && event.refusal_reason !== null) || (event.verdict === "refused" && event.refusal_reason === null)) throw new Error("invalid answer outcome"); return event; }
    case "verifier_summary": { const r = exact(value, ["kind", "sequence", "checks", "duration_ms"]); return { kind, sequence, checks: list(r.checks, "checks", 20).map((item) => { const c = exact(item, ["evidence_id", "passed", "fault_code"]); const check = { evidence_id: c.evidence_id === null ? null : hash(c.evidence_id, "evidence id"), passed: bool(c.passed, "passed"), fault_code: reason(c.fault_code, "fault code") }; if ((check.passed && check.fault_code !== null) || (!check.passed && check.fault_code === null)) throw new Error("invalid verifier check"); return check; }), duration_ms: integer(r.duration_ms, "duration", 0, 3_600_000) }; }
    case "terminal": { const r = exact(value, ["kind", "sequence", "status", "reason"]); const event: Terminal = { kind, sequence, status: enumeration(r.status, TERMINAL, "terminal status"), reason: reason(r.reason, "reason") }; if ((event.status === "answered" && event.reason !== null) || (event.status !== "answered" && event.reason === null)) throw new Error("invalid terminal event"); return event; }
    default: throw new Error("unknown trace event");
  }
}

export function decodeRun(value: unknown): RunView {
  const r = exact(value, ["run_id", "request_id", "task_level", "profile", "corpus_manifest_id", "status", "reason", "created_at", "started_at", "completed_at", "events"]);
  const taskLevel = enumeration(r.task_level, ["L1"] as const, "task level");
  const events = list(r.events, "events", 10_000).map(decodeEvent);
  if (events.some((event, index) => index > 0 && event.sequence <= events[index - 1]!.sequence)) throw new Error("invalid event order");
  return { run_id: uuid(r.run_id, "run id"), request_id: uuid(r.request_id, "request id"), task_level: taskLevel, profile: identifier(r.profile, "profile"), corpus_manifest_id: hash(r.corpus_manifest_id, "corpus manifest id"), status: enumeration(r.status, STATUSES, "status"), reason: reason(r.reason, "reason"), created_at: timestamp(r.created_at, "created at"), started_at: nullableTimestamp(r.started_at, "started at"), completed_at: nullableTimestamp(r.completed_at, "completed at"), events };
}

function init(token: string | undefined, method = "GET", body?: unknown): RequestInit {
  const headers: Record<string, string> = {};
  if (token !== undefined) headers.Authorization = `Bearer ${token}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  return { method, credentials: "same-origin", headers, ...(body === undefined ? {} : { body: JSON.stringify(body) }) };
}
async function json(response: Response, operation: string): Promise<unknown> {
  if (!response.ok) throw new Error(`${operation} request failed (${response.status})`);
  try { return await response.json() as unknown; } catch { throw new Error(`${operation} response was invalid`); }
}

export async function getRun(runId: string, options: ClientOptions = {}): Promise<RunView> {
  if (!UUID_RE.test(runId)) throw new Error("invalid run id");
  const fetcher = options.fetcher ?? fetch;
  let response: Response;
  try { response = await fetcher(`/runs/${encodeURIComponent(runId)}`, init(options.token)); } catch { throw new Error("run request unavailable"); }
  return decodeRun(await json(response, "run"));
}

export async function createRun(request: CreateRunRequest, options: ClientOptions = {}): Promise<ChatAccepted> {
  const fetcher = options.fetcher ?? fetch;
  let response: Response;
  try { response = await fetcher("/chat", init(options.token, "POST", request)); } catch { throw new Error("chat request unavailable"); }
  const r = exact(await json(response, "chat"), ["run_id", "status"]);
  return { run_id: uuid(r.run_id, "run id"), status: enumeration(r.status, ["queued"] as const, "status") };
}
