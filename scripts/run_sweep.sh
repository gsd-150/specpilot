#!/usr/bin/env bash
# Run one evaluation level and split end to end, one artifact per case.
#
# Author-run: real provider calls against a real key, which AGENTS.md reserves
# to the author. Supersedes `tmp/run_l1_dev.sh` and `tmp/run_l2_dev.sh`, which
# were gitignored, untested, and hardcoded to the dev split.
#
# Three defects from those two scripts are closed here:
#
#   1. The retry branch in run_l2_dev.sh printed ${CODE}, a variable nothing
#      assigned, under `set -u`. The first transport-level retry therefore
#      aborted the sweep with "CODE: unbound variable" instead of retrying —
#      reachable only on provider_unreachable / invalid_tool_plan /
#      provider_timeout, which is to say only during a long live run.
#   2. Neither script checked its case count. `sweep plan` now refuses a
#      selection that differs from --expected, so a filter matching
#      nothing fails here instead of printing "running 0 cases" and exiting 0.
#   3. Both selected the chain root rather than the head. See
#      src/specpilot/evaluation/sweep.py.
#
# Prerequisites:
#   colima start && docker start specpilot-qdrant-1
#   PostgreSQL specpilot_live migrated
#   export SPECPILOT_MAIN_API_KEY='...'
#
# Usage (from anywhere — the script resolves its own tree):
#   bash scripts/run_sweep.sh --level l1 --split dev --expected 12
#   bash scripts/run_sweep.sh --level l1 --split locked --expected 25 \
#       --include-unanswerable
#   bash scripts/run_sweep.sh --level l2-adv --split locked --expected 10 \
#       --source-manifest <id>
set -u

# Everything below is relative to the tree this script lives in, not to wherever
# it was invoked from. The restricted artifacts, the model weights and the
# renditions are all worktree-local, so a sweep run from the wrong directory
# either fails on a path or — worse — succeeds against the wrong tree.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT" || { echo "cannot enter $REPO_ROOT" >&2; exit 3; }

LEVEL=""
SPLIT=""
EXPECTED=""
OUT_DIR=""
INCLUDE_UNANSWERABLE=0
ADV_SOURCE=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --level)                 LEVEL="$2"; shift 2 ;;
    --split)                 SPLIT="$2"; shift 2 ;;
    --expected)              EXPECTED="$2"; shift 2 ;;
    --out-dir)               OUT_DIR="$2"; shift 2 ;;
    --source-manifest)       ADV_SOURCE="$2"; shift 2 ;;
    --include-unanswerable)  INCLUDE_UNANSWERABLE=1; shift ;;
    *) echo "unknown argument: $1" >&2; exit 4 ;;
  esac
done

# Required, never defaulted. §8.5 keeps the locked splits unread until W6, so a
# split with a default is a locked run nobody asked for.
[ -n "$LEVEL" ]    || { echo "usage: --level {l1|l2|l2-adv}" >&2; exit 4; }
[ -n "$SPLIT" ]    || { echo "usage: --split {dev|locked}" >&2; exit 4; }
[ -n "$EXPECTED" ] || { echo "usage: --expected N" >&2; exit 4; }
: "${SPECPILOT_MAIN_API_KEY:?set SPECPILOT_MAIN_API_KEY first}"

# A git worktree carries no `.venv` — the environment lives in the main
# checkout, installed there as an editable package. That has one consequence
# worth stating plainly: an unqualified `import specpilot` from that interpreter
# resolves to the *main checkout's* source, which is a different commit
# entirely. Every superseded driver in `tmp/` set PYTHONPATH by hand for this
# reason and the first version of this script did not.
if [ -n "${SPECPILOT_PYTHON:-}" ]; then
  PYTHON="$SPECPILOT_PYTHON"
elif [ -x "$REPO_ROOT/.venv/bin/python" ]; then
  PYTHON="$REPO_ROOT/.venv/bin/python"
else
  COMMON_DIR="$(git rev-parse --git-common-dir 2>/dev/null || echo .git)"
  PYTHON="$(cd "$COMMON_DIR/.." 2>/dev/null && pwd)/.venv/bin/python"
fi
[ -x "$PYTHON" ] || { echo "no interpreter at $PYTHON" >&2; exit 3; }
export PYTHONPATH="$REPO_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

# Assert it rather than trust it. Running the wrong tree's code is the failure
# this project keeps finding in other forms — a value present in the source and
# absent from what actually ran.
IMPORTED=$("$PYTHON" -c 'import specpilot, pathlib; print(pathlib.Path(specpilot.__file__).resolve().parent)')
if [ "$IMPORTED" != "$REPO_ROOT/src/specpilot" ]; then
  echo "interpreter would run $IMPORTED, not $REPO_ROOT/src/specpilot" >&2
  exit 3
fi
QDRANT="${SPECPILOT_QDRANT_URL:-http://localhost:6333}"
LEDGER="${SPECPILOT_LEDGER_DSN:-postgresql:///specpilot_live}"
MODEL_DIR="${SPECPILOT_MODEL_DIR:-data/cache/models/bge-m3}"
DEVICE="${SPECPILOT_DEVICE:-mps}"
ROUTE="${SPECPILOT_ROUTE:-main}"

# The frozen corpus and the renditions it binds, same defaults as scripts/ask.sh.
# The secure directory walker opens each path component by dir_fd and refuses
# `..`, so the manifest directories must be absolute.
CORPUS="${SPECPILOT_CORPUS_MANIFEST:-1abafff704358c2357ead5b837d212f130cadfa330dfa30d1df0a24f76d74295}"

# `manifests/` is gitignored, so a git worktree does not carry it and the store
# stays in the main checkout. Resolved rather than assumed: this runs from the
# freeze worktree, where the local path does not exist, and the first rehearsal
# died right here. The freeze tree is unaffected either way — only the
# manifests' content hashes enter the identity status, never their location.
resolve_manifest_dir() {
  local relative="$1" base found
  for base in "." "$(git rev-parse --git-common-dir 2>/dev/null || echo .)/.."; do
    found=$(cd "$base/$relative" 2>/dev/null && pwd) || continue
    if [ -n "$found" ]; then printf '%s' "$found"; return 0; fi
  done
  return 1
}
CORPUS_DIR="${SPECPILOT_CORPUS_MANIFEST_DIR:-$(resolve_manifest_dir manifests/local/r0/corpus)}"
SOURCE_DIR="${SPECPILOT_SOURCE_MANIFEST_DIR:-$(resolve_manifest_dir manifests/local/r0/source)}"
ANNOTATION_DIR="${SPECPILOT_ANNOTATION_DIR:-artifacts/restricted/annotations}"
GROUP_DIR="${SPECPILOT_GROUP_DIR:-artifacts/restricted/l2-adv}"
RFC9110="${SPECPILOT_RFC9110_MANIFEST:-af230fed7cf961ba9a099e39be4ae03a881ef7cd885b40fa84bc9ffa55e34691}"
RFC9112="${SPECPILOT_RFC9112_MANIFEST:-3a752dd99f78398815252baa322e1ad0e9963ade5eb66dfe66e2861d8c2bede2}"
XML9110="${SPECPILOT_RFC9110_XML:-artifacts/restricted/sources/ietf/rfc9110/rfc9110.xml}"
XML9112="${SPECPILOT_RFC9112_XML:-artifacts/restricted/sources/ietf/rfc9112/rfc9112.xml}"
AUTHORIZED_9110="${SPECPILOT_AUTHORIZED_9110:-c42813e7b81a092bcdc7d8144c67beb9987d7c2742841fef9b249f34958296d7}"
AUTHORIZED_9112="${SPECPILOT_AUTHORIZED_9112:-b74abd04e5887a44995a58e0895a6de34be3f61480fd5519754205a2b0d66d4f}"

[ -n "$CORPUS_DIR" ] || { echo "corpus manifest directory not found" >&2; exit 3; }
[ -n "$SOURCE_DIR" ] || { echo "source manifest directory not found" >&2; exit 3; }

if [ -z "$OUT_DIR" ]; then
  OUT_DIR="artifacts/restricted/${SPLIT}/${LEVEL}"
fi
# Created after every check passes, not before. An earlier version made the
# directory first, so a refused invocation still left one behind — and because
# the default path carries the split, a refused `--split locked` created a
# directory under the namespace that must stay empty until W6 executes.

# A group spans documents by construction — document attribution is one of the
# five distractor dimensions — so no single authorization follows from the
# record and the author names it.
if [ "$LEVEL" = "l2-adv" ] && [ -z "$ADV_SOURCE" ]; then
  echo "--source-manifest is required for --level l2-adv" >&2
  exit 4
fi

PLAN_ARGS=(sweep plan --level "$LEVEL" --split "$SPLIT" --expected "$EXPECTED")
if [ "$LEVEL" = "l2-adv" ]; then
  PLAN_ARGS+=(--group-dir "$GROUP_DIR")
else
  PLAN_ARGS+=(--annotation-dir "$ANNOTATION_DIR")
  [ "$INCLUDE_UNANSWERABLE" = "1" ] && PLAN_ARGS+=(--include-unanswerable)
fi

# The plan refuses before the first provider call if the count is wrong, so
# this is the gate: no cases, no sweep.
PLAN=$("$PYTHON" -m specpilot.cli "${PLAN_ARGS[@]}") || {
  echo "sweep plan refused; nothing was sent" >&2
  exit 2
}
COUNT=$(printf '%s\n' "$PLAN" | sed '/^$/d' | wc -l | tr -d ' ')

mkdir -p "$OUT_DIR" && chmod 700 "$OUT_DIR"

HEAD_BEFORE=$(git rev-parse HEAD)
echo "sweep  level=${LEVEL} split=${SPLIT} cases=${COUNT} out=${OUT_DIR}"
echo "head   ${HEAD_BEFORE}"

field() { printf '%s' "$1" | python3 -c "import json,sys; print(json.load(sys.stdin).get(sys.argv[1]) or '')" "$2"; }

run_l1_case() {
  local item_id="$1" question="$2"
  "$PYTHON" -m specpilot.cli answer \
    --question "$question" \
    --corpus-manifest "$CORPUS" --corpus-manifest-dir "$CORPUS_DIR" \
    --manifest-dir "$SOURCE_DIR" \
    --manifest "$RFC9110" --xml "$XML9110" \
    --manifest "$RFC9112" --xml "$XML9112" \
    --source-manifest "$AUTHORIZED_9110" \
    --model-dir "$MODEL_DIR" --device "$DEVICE" --qdrant-url "$QDRANT" \
    --ledger-dsn "$LEDGER" --route "$ROUTE" \
    --evaluation-root-id "case-$(date +%s)-${item_id}" \
    --run-id "run-$(date +%s)-${item_id}" 2>&1 | grep -v "Loading weights"
}

run_l2_case() {
  local case_id="$1" question="$2" authorized="$3" root="$4"
  "$PYTHON" -m specpilot.cli l2 run \
    --question "$question" --case-id "$case_id" \
    --corpus-manifest "$CORPUS" --corpus-manifest-dir "$CORPUS_DIR" \
    --manifest-dir "$SOURCE_DIR" \
    --manifest "$RFC9110" --xml "$XML9110" \
    --manifest "$RFC9112" --xml "$XML9112" \
    --source-manifest "$authorized" \
    --model-dir "$MODEL_DIR" --device "$DEVICE" --qdrant-url "$QDRANT" \
    --ledger-dsn "$LEDGER" --route "$ROUTE" \
    --evaluation-root-id "$root" --run-id "$(uuidgen | tr 'A-Z' 'a-z')" \
    --out-dir "$OUT_DIR" 2>&1 | grep -v "Loading weights"
}

# Process substitution, not a pipe: the loop must run in this shell so a failed
# case aborts the whole sweep instead of only its subshell.
while IFS= read -r LINE; do
  [ -n "$LINE" ] || continue
  CASE_ID=$(field "$LINE" case_id)
  QUESTION=$(field "$LINE" question)
  DOCUMENT=$(field "$LINE" document_id)
  GROUP_ID=$(field "$LINE" group_id)

  ATTEMPT=0
  STATUS=""
  while [ "$ATTEMPT" -lt 3 ]; do
    ATTEMPT=$((ATTEMPT + 1))
    if [ "$LEVEL" = "l1" ]; then
      OUT=$(run_l1_case "$CASE_ID" "$QUESTION")
    else
      if [ "$LEVEL" = "l2-adv" ]; then
        AUTHORIZED="$ADV_SOURCE"
        ROOT="adv-$(date +%s)-${CASE_ID}-a${ATTEMPT}"
      elif [ "$DOCUMENT" = "ietf-rfc-9110" ]; then
        AUTHORIZED="$AUTHORIZED_9110"
        ROOT="l2-case-$(date +%s)-${CASE_ID}-a${ATTEMPT}"
      else
        AUTHORIZED="$AUTHORIZED_9112"
        ROOT="l2-case-$(date +%s)-${CASE_ID}-a${ATTEMPT}"
      fi
      OUT=$(run_l2_case "$CASE_ID" "$QUESTION" "$AUTHORIZED" "$ROOT")
    fi

    STATUS=$(printf '%s' "$OUT" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("status","unreadable"))' 2>/dev/null || echo unreadable)
    case "$STATUS" in
      answered|completed) break ;;
      refused)
        # An expected refusal is a result, not a failure: the judge scores
        # answered cases only, so it is logged and never written as an answer.
        REASON=$(printf '%s' "$OUT" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("refusal_reason",""))' 2>/dev/null || echo "")
        printf '%s\t%s\n' "$CASE_ID" "$REASON" >> "$OUT_DIR/refusals.log"
        break
        ;;
    esac

    # Assigned before it is printed. The predecessor referenced an unset
    # ${CODE} here and died on its own retry path under `set -u`.
    CODE=$(printf '%s' "$OUT" | head -1)
    case "$CODE" in
      *provider_unreachable*|*provider_timeout*|*invalid_tool_plan*)
        echo "retry ${ATTEMPT}/3 ${CASE_ID} (${CODE})"
        sleep 5
        ;;
      *)
        echo "FAILED ${CASE_ID}: ${OUT}" >&2
        exit 1
        ;;
    esac
  done

  case "$STATUS" in
    answered)
      printf '%s' "$OUT" | python3 -c '
import json, sys
payload = json.load(sys.stdin)
print(json.dumps({"answer": payload["answer"]}, ensure_ascii=False))
' > "$OUT_DIR/${CASE_ID}.json"
      echo "answered   ${CASE_ID}"
      ;;
    completed)
      echo "completed  ${CASE_ID}${GROUP_ID:+  (${GROUP_ID})}"
      ;;
    refused)
      echo "refused    ${CASE_ID}"
      ;;
    *)
      echo "FAILED ${CASE_ID} after ${ATTEMPT} attempt(s)" >&2
      exit 1
      ;;
  esac
done < <(printf '%s\n' "$PLAN")

# Batch coherence. Every artifact must record the prompt identity this checkout
# computed at run time, so a mid-sweep code change cannot mix generations into
# one directory and call the mixture a result.
if [ "$LEVEL" != "l1" ]; then
  EXPECTED_HASH=$("$PYTHON" -c "from specpilot.cli import _l2_compliance_prompt_hash; print(_l2_compliance_prompt_hash())")
  MISMATCH=0
  CHECKED=0
  for FILE in "$OUT_DIR"/*.json; do
    [ -f "$FILE" ] || continue
    RECORDED=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get('compliance_prompt_sha256',''))" "$FILE")
    [ -n "$RECORDED" ] || continue
    CHECKED=$((CHECKED + 1))
    if [ "$RECORDED" != "$EXPECTED_HASH" ]; then
      echo "HASH MISMATCH $(basename "$FILE"): $RECORDED" >&2
      MISMATCH=1
    fi
  done
  [ "$MISMATCH" = "0" ] || { echo "batch prompt-identity mismatch; re-run the sweep" >&2; exit 1; }
  # A guard with nothing to check reports success. The predecessor skipped every
  # artifact lacking a prompt field and then printed "verified" — which on a
  # directory holding no case outcomes is a pass earned by the absence of
  # evidence. The count is the difference between checking and appearing to.
  if [ "$CHECKED" -lt "$COUNT" ]; then
    echo "prompt identity checked ${CHECKED} of ${COUNT} case outcome(s); the rest carry no prompt field" >&2
    exit 1
  fi
  echo "batch prompt identity verified across ${CHECKED} outcome(s): ${EXPECTED_HASH}"
fi

# The W5 gate was voided once because a commit landed mid-run and the evidence
# spanned two trees.
HEAD_AFTER=$(git rev-parse HEAD)
if [ "$HEAD_BEFORE" != "$HEAD_AFTER" ]; then
  echo "HEAD moved during the sweep: ${HEAD_BEFORE} -> ${HEAD_AFTER}" >&2
  exit 1
fi
echo "sweep complete  level=${LEVEL} split=${SPLIT} cases=${COUNT} head=${HEAD_AFTER}"
