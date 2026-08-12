import type { RunEvent } from "../api";

type SafeEvent = { sequence: number; title: string; rows: Array<[string, string]> };

const IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$/;
const ARGUMENT = /^[A-Za-z][A-Za-z0-9_]{0,63}$/;
const STABLE_CODE = /^[a-z][a-z0-9_]{0,63}$/;
const HASH = /^[0-9a-f]{64}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const TOOLS = new Set(["search_clauses", "get_clause", "get_toc", "expand_references", "lookup_term"]);
const STATUSES = new Set(["queued", "running", "answered", "refused", "egress_blocked", "failed", "interrupted"]);
const STAGES = new Set(["planning", "evidence", "compliance", "verifier", "judge"]);

function record(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}
function text(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 && value.length <= 128 ? value : null;
}
function matched(value: unknown, pattern: RegExp): string | null {
  const valueText = text(value);
  return valueText !== null && pattern.test(valueText) ? valueText : null;
}
function choice(value: unknown, values: Set<string>): string | null {
  const valueText = text(value);
  return valueText !== null && values.has(valueText) ? valueText : null;
}
function integer(value: unknown): string | null {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0
    ? String(value)
    : null;
}
function yesNo(value: unknown): string | null {
  return typeof value === "boolean" ? (value ? "yes" : "no") : null;
}
function sequence(recordValue: Record<string, unknown>): number | null {
  const value = recordValue.sequence;
  return typeof value === "number" && Number.isSafeInteger(value) && value > 0 ? value : null;
}
function row(label: string, value: string | null): [string, string][] {
  return value === null ? [] : [[label, value]];
}
function idRows(items: unknown, label: string): Array<[string, string]> {
  if (!Array.isArray(items)) return [];
  const rows: Array<[string, string]> = [];
  for (const item of items.slice(0, 20)) {
    const safe = record(item);
    if (safe === null) continue;
    const evidenceId = matched(safe.evidence_id, HASH);
    const contentHash = matched(safe.content_hash, HASH);
    if (evidenceId !== null) rows.push([`${label} ID`, evidenceId]);
    if (contentHash !== null) rows.push(["Content hash", contentHash]);
  }
  return rows;
}

// The API decoder is the first boundary. This explicit projection is a second
// one: rendering remains closed even if a malformed object bypasses TS at runtime.
function project(event: unknown): SafeEvent | null {
  const safe = record(event);
  if (safe === null) return null;
  const seq = sequence(safe);
  const kind = text(safe.kind);
  if (seq === null || kind === null) return null;
  switch (kind) {
    case "state_transition":
      return { sequence: seq, title: "State transition", rows: [...row("Previous", choice(safe.previous_status, STATUSES)), ...row("Status", choice(safe.status, STATUSES)), ...row("Reason", matched(safe.reason, STABLE_CODE))] };
    case "plan_summary":
      return { sequence: seq, title: "Tool plan", rows: [...row("Plan ID", matched(safe.plan_id, IDENTIFIER)), ...row("Steps", integer(safe.step_count)), ...row("Maximum calls", integer(safe.max_tool_calls))] };
    case "agent_step":
      return { sequence: seq, title: "Agent step", rows: [...row("Agent", matched(safe.agent, STABLE_CODE)), ...row("Step ID", matched(safe.step_id, IDENTIFIER)), ...row("Phase", choice(safe.phase, new Set(["started", "finished"]))), ...row("Duration", integer(safe.duration_ms) === null ? null : `${integer(safe.duration_ms)} ms`), ...row("Stable error", matched(safe.error_code, STABLE_CODE))] };
    case "tool_finished": { const keys = Array.isArray(safe.argument_keys) ? safe.argument_keys.map((key) => matched(key, ARGUMENT)).filter((key): key is string => key !== null).slice(0, 16) : []; const retries = integer(safe.retry_count); const count = integer(safe.result_count); const duration = integer(safe.duration_ms); return { sequence: seq, title: "Tool finished", rows: [...row("Tool", choice(safe.tool, TOOLS)), ...row("Step ID", matched(safe.step_id, IDENTIFIER)), ...row("Argument keys", keys.length === 0 ? null : keys.join(", ")), ...row("Results", count === null ? null : `${count} results`), ...row("Duration", duration === null ? null : `${duration} ms`), ...row("Retries", retries === null ? null : `${retries} ${retries === "1" ? "retry" : "retries"}`), ...row("Stable error", matched(safe.error_code, STABLE_CODE))] }; }
    case "candidate_summary": { const count = Array.isArray(safe.candidates) ? safe.candidates.length : null; return { sequence: seq, title: "Candidate summary", rows: row("Candidate count", count === null ? null : String(Math.min(count, 20))) }; }
    case "evidence_summary":
      return { sequence: seq, title: "Evidence selected", rows: idRows(safe.evidence, "Evidence") };
    case "egress_summary": { const tokens = integer(safe.request_tokens); const bytes = integer(safe.request_bytes); const cost = integer(safe.cost_microunits); return { sequence: seq, title: "Egress decision", rows: [...row("Stage", choice(safe.stage, STAGES)), ...row("Reservation ID", matched(safe.reservation_id, UUID)), ...row("Ledger ID", matched(safe.ledger_id, UUID)), ...row("Admitted", yesNo(safe.admitted)), ...row("Replayed", yesNo(safe.replayed)), ...row("Request tokens", tokens), ...row("Request bytes", bytes), ...row("Cost (microunits)", cost), ...row("Stable error", matched(safe.error_code, STABLE_CODE))] }; }
    case "usage_summary": { const duration = integer(safe.duration_ms); return { sequence: seq, title: "Provider usage", rows: [...row("Stage", choice(safe.stage, STAGES)), ...row("Prompt tokens", integer(safe.prompt_tokens)), ...row("Completion tokens", integer(safe.completion_tokens)), ...row("Request bytes", integer(safe.request_bytes)), ...row("Duration", duration === null ? null : `${duration} ms`), ...row("Cost (microunits)", integer(safe.cost_microunits))] }; }
    case "answer_outcome":
      return { sequence: seq, title: "Answer outcome", rows: [...row("Verdict", choice(safe.verdict, new Set(["answered", "refused"]))), ...row("Refusal reason", matched(safe.refusal_reason, STABLE_CODE)), ...row("Provider error", matched(safe.provider_error, STABLE_CODE)), ...row("Reservation ID", matched(safe.reservation_id, UUID)), ...row("Replayed", yesNo(safe.replayed)), ...row("Parse fault", matched(safe.parse_fault_code, STABLE_CODE))] };
    case "verifier_summary": { const duration = integer(safe.duration_ms); const rows: Array<[string, string]> = []; if (Array.isArray(safe.checks)) for (const check of safe.checks.slice(0, 20)) { const c = record(check); if (c === null) continue; rows.push(...row("Evidence ID", matched(c.evidence_id, HASH)), ...row("Check passed", yesNo(c.passed)), ...row("Fault", matched(c.fault_code, STABLE_CODE))); } rows.push(...row("Duration", duration === null ? null : `${duration} ms`)); return { sequence: seq, title: "Verifier checks", rows }; }
    case "terminal":
      return { sequence: seq, title: "Terminal state", rows: [...row("Status", choice(safe.status, STATUSES)), ...row("Reason", matched(safe.reason, STABLE_CODE))] };
    default:
      return null;
  }
}

export function TraceTimeline({ events }: { events: RunEvent[] }) {
  const projected = events.map(project).filter((event): event is SafeEvent => event !== null).sort((a, b) => a.sequence - b.sequence);
  return (
    <section className="trace-section" aria-labelledby="trace-title">
      <div className="section-heading"><p className="eyebrow">Sanitized execution</p><h2 id="trace-title">Run trace</h2></div>
      {projected.length === 0 ? <p className="empty-state">No sanitized events have arrived yet.</p> : (
        <ol className="timeline" aria-label="Run trace">
          {projected.map((event) => (
            <li className="timeline__item" data-sequence={event.sequence} key={`${event.sequence}-${event.title}`}>
              <div className="timeline__marker" aria-hidden="true">{event.sequence}</div>
              <article><h3>{event.title}</h3><dl>{event.rows.map(([label, value], index) => <div key={`${label}-${index}`}><dt>{label}</dt><dd><code>{value}</code></dd></div>)}</dl></article>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
