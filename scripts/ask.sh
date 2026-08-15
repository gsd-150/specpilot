#!/usr/bin/env bash
# One live question through the whole gate: retrieval, the disclosure ledger,
# the enforcer, and the provider. Author-run — it spends real budget against a
# real key and puts RFC excerpts on the wire, which AGENTS.md reserves to the
# author.
#
# This lived as `tmp/ask.sh` and `tmp/` is gitignored, so AGENTS.md pointed a
# freshly cloned checkout at a file that was never there. Running the real path
# early is the practice that found every defect a passing unit suite missed, so
# the one command that does it belongs in the repository.
#
# Usage:  bash scripts/ask.sh "your question"
#         SPECPILOT_ROUTE=judge bash scripts/ask.sh "..."   # calibration route
set -euo pipefail

QUESTION="${1:?usage: bash scripts/ask.sh \"your question\"}"
: "${SPECPILOT_MAIN_API_KEY:?set SPECPILOT_MAIN_API_KEY first}"

ROUTE="${SPECPILOT_ROUTE:-main}"
PYTHON="${SPECPILOT_PYTHON:-.venv/bin/python}"
QDRANT="${SPECPILOT_QDRANT_URL:-http://localhost:6333}"
LEDGER="${SPECPILOT_LEDGER_DSN:-postgresql:///specpilot_live}"

# The frozen corpus and the renditions it binds. A checkout that has not
# initialized a real corpus will refuse at the manifest, which is the correct
# failure: the caps and the citations are bound to this manifest.
CORPUS="${SPECPILOT_CORPUS_MANIFEST:-1abafff704358c2357ead5b837d212f130cadfa330dfa30d1df0a24f76d74295}"
CORPUS_DIR="${SPECPILOT_CORPUS_MANIFEST_DIR:-manifests/local/r0/corpus}"
SOURCE_DIR="${SPECPILOT_SOURCE_MANIFEST_DIR:-manifests/local/r0/source}"
RFC9110="${SPECPILOT_RFC9110_MANIFEST:-af230fed7cf961ba9a099e39be4ae03a881ef7cd885b40fa84bc9ffa55e34691}"
AUTHORIZED="${SPECPILOT_AUTHORIZED_SOURCE:-c42813e7b81a092bcdc7d8144c67beb9987d7c2742841fef9b249f34958296d7}"
XML="${SPECPILOT_RFC9110_XML:-artifacts/restricted/sources/ietf/rfc9110/rfc9110.xml}"

exec "$PYTHON" -m specpilot.cli answer \
  --question "$QUESTION" \
  --corpus-manifest "$CORPUS" \
  --corpus-manifest-dir "$CORPUS_DIR" \
  --manifest-dir "$SOURCE_DIR" \
  --manifest "$RFC9110" \
  --xml "$XML" \
  --source-manifest "$AUTHORIZED" \
  --model-dir data/cache/models/bge-m3 \
  --device mps \
  --qdrant-url "$QDRANT" \
  --ledger-dsn "$LEDGER" \
  --route "$ROUTE"
