# Source and provider self-assessment

**This is a self-assessment. There is no external approver.** Nothing in this
repository grants permission, and neither the CLI nor the manifest store can
confer it. Filling this in records the author's own due diligence and the
reasoning behind one specific decision; it does not make that decision correct,
and it must never be described to anyone as approval, clearance, sign-off, or
legal advice.

Only the author may complete section 4. Do not let a tool, an assistant, or a
teammate write a conclusion on your behalf: `author_id` names whoever is
accountable for it.

## How this file is used

Fill in a JSON document with these four sections, then bind it to exactly one
provider route:

```bash
python -m specpilot.cli source-manifest authorize-successor \
  --manifest-dir manifests/local \
  --predecessor <initial-manifest-id> \
  --assessment docs/compliance/my-assessment.json \
  --provider-id <provider> \
  --endpoint-purpose <purpose> \
  --use online_main \
  --created-at 2026-08-06T03:00:00Z
```

The command validates completeness and internal consistency and checks that the
conclusion names the same route being bound. It refuses anything else with
`invalid_authorization_evidence`. A refusal means the evidence is incomplete,
not that the underlying activity is permitted or forbidden.

Until an authorized successor exists, the source manifest is default-deny and
no cloud route is reachable. That is the intended resting state.

## 1. `source_terms` — what the source's own terms say

| Field | What goes in it |
| --- | --- |
| `terms_snapshot.snapshot_url` | The HTTPS URL of the official terms page you actually read |
| `terms_snapshot.snapshot_sha256` | SHA-256 of the exact bytes you retrieved |
| `terms_snapshot.captured_at` | RFC3339 timestamp of retrieval |
| `summary` | Your own short paraphrase of the relevant permissions and restrictions |
| `uncertainty` | At least one honest statement of what you could not determine |

Write the summary in your own words. Do not paste long passages of the terms
into this file: the point is to record what you understood, and a copied page is
not a record of understanding.

`uncertainty` is required and must not be empty. An assessment with nothing
uncertain in it is a claim of certainty that reading a web page cannot support.

## 2. `provider_policy` — what the provider does with what you send

| Field | What goes in it |
| --- | --- |
| `policy_snapshot` | URL, SHA-256, and retrieval time of the provider's policy page |
| `retention_summary` | How long the provider says it keeps request content |
| `training_summary` | Whether the provider says it trains on request content |
| `region_summary` | Where the provider says processing happens |
| `subprocessor_summary` | Who else the provider says may see the content |
| `uncertainty` | At least one statement of what the policy leaves unclear |

Record the account-level policy that actually applies to the plan you will use,
not the strictest tier the provider advertises. If you cannot confirm which tier
applies to your account, that belongs in `uncertainty`.

## 3. `outbound_limit` — the factual premise the decision rests on

| Field | What goes in it |
| --- | --- |
| `premise` | The exact factual statement about how much text can leave |
| `premise_sha256` | SHA-256 of `premise`, verified on load |

This section exists because the decision in section 4 is only as good as its
premise. The shipped premise is bounded excerpts under the caps in
`src/specpilot/egress/policies/default-v1.json`, and the enforcer and ledger are
what make it true rather than aspirational.

**If you change the caps, this premise changes and the assessment is stale.**
Changing `policy_hash` already stops an in-flight evaluation root, but the
staleness of a written premise is something only you can notice.

## 4. `author_conclusion` — the decision, and who owns it

| Field | What goes in it |
| --- | --- |
| `authorized` | `true` or `false`. Strict boolean; `"yes"` and `1` are rejected |
| `authorization_statement` | What exactly you are deciding, in one sentence |
| `author_id` | Whoever is accountable |
| `provider_id`, `endpoint_purpose` | Must match the route being bound |
| `authored_at`, `expires_at` | RFC3339; expiry must follow authorship |

`expires_at` is mandatory and there is no revocation path. The store is
create-only, so a decision cannot be withdrawn once written — it can only run
out. Choose a horizon you would be comfortable defending for its whole length,
and prefer a short one.

A conclusion of `false` is a complete, valid assessment. It records that the
work was done and the answer was no, and it leaves the default-deny manifest in
place. Route B or C in the go/no-go runbook then applies.
