# Task 10 Prerequisite: Task 8 Step 4 Evidence Draft Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fill the Task 8 Step 4 evidence gap that currently blocks Task 10 by freezing two exact 3GPP Release 18 sources, capturing content-addressed official-policy evidence, and producing four unsigned compliance-assessment drafts while preserving SpecPilot's default-deny boundary.

**Architecture:** Task 9 is already complete and is not repeated here. Real source files, response snapshots, account evidence, evidence indexes, manifests, and unsigned drafts stay in dedicated Git-ignored local directories. The repository receives only a sanitized Task 10 input record. The deliverable deliberately stops before `author_conclusion`, successor creation, real-provider smoke, or any 3GPP-bearing provider call, so route eligibility remains `extend`; the separate Task 10 verification record and final report are not created by this plan.

**Tech Stack:** Existing Python 3.12+ package and CLI, Pydantic manifest contracts, `curl`, SHA-256, JSON, Git.

## Global Constraints

- Use TS 38.300 version 18.10.0 (`38300-ia0.zip`) and TS 38.321 version 18.10.0 (`38321-ia0.zip`), both from the 3GPP Release 18 line.
- Main route identity is `deepseek` / `online-main-deepseek-v4-flash-api` / `online_main`; requested model slug is `deepseek-v4-flash`.
- Judge route identity is `chatanywhere` / `offline-judge-glm-5-2-api` / `offline_judge`; requested model slug is `glm-5.2`.
- Never write, infer, default, or upgrade `author_conclusion`; unsigned draft JSON omits the field entirely.
- Never call `source-manifest authorize-successor` in this plan.
- Never send real 3GPP content or excerpts to DeepSeek, ChatAnywhere, GLM, or any other provider.
- Never commit ZIP/DOCX files, webpage bodies, account screenshots, credentials, API keys, local manifests, provider metadata, or unsigned assessment drafts.
- Create every restricted directory with `umask 077`; require `0700` directories and `0600` data files. Reject symlink targets and publish downloads no-clobber from private temporary files.
- Preserve the user-owned untracked `SpecPilot_项目方案.md`; only the confirmed judge-model/API wording may be updated, and the file must not be staged or committed.
- Do not create or modify `artifacts/public/w0-verification.json` or `docs/reports/w0-foundation-report.md`, and do not mark Task 10 complete. This plan produces one input to Task 10, not the final W0 verification.
- The only route-eligibility state this plan may report is `extend`.

## Verified Repository Checkpoint

- Branch: `feat/w0-foundation`.
- Task 9 implementation commit: `6185874 ci: add isolated w0 service and fixture checks`; all six Task 9 plan steps are checked.
- Plan-writing audit `HEAD`: `3b9c423 docs: design task8 compliance assessment draft`; execution begins only after this reviewed plan is committed on top.
- `docs/runbooks/w0-go-no-go.md` exists but its checklist is still unchecked.
- `artifacts/public/w0-verification.json` and `docs/reports/w0-foundation-report.md` do not exist.
- Fresh Ruff, mypy, unit, CLI, and Compose-configuration checks passed during the read-only audit. PostgreSQL integration, fixture smoke, Docker build, and service startup still require fresh Task 10 runs and are deliberately outside this plan.

---

## File Structure

### Restricted, Git-ignored outputs

- `artifacts/restricted/sources/3gpp/38.300/18.10.0/38300-ia0.zip` — exact TS 38.300 archive.
- `artifacts/restricted/sources/3gpp/38.321/18.10.0/38321-ia0.zip` — exact TS 38.321 archive.
- `data/real/3gpp/38.300/18.10.0/38300-ia0.docx` — safely inspected DOCX.
- `data/real/3gpp/38.321/18.10.0/38321-ia0.docx` — safely inspected DOCX.
- `data/quarantine/3gpp/` — restricted refusal destination. If OOXML inspection rejects after extraction, move only the newly extracted artifact here and never repair it into an accepted input.
- `artifacts/restricted/compliance/2026-08-07/snapshots/` — exact HTTP response bodies used for hashing.
- `artifacts/restricted/compliance/2026-08-07/capture-metadata/` — one machine-generated URL/time/hash/size sidecar per response.
- `artifacts/restricted/compliance/2026-08-07/evidence-indexes/` — content-addressed route evidence indexes.
- `artifacts/restricted/compliance/2026-08-07/capture-page` — restricted capture helper; never committed.
- `artifacts/restricted/compliance/2026-08-07/account-evidence/` — restricted DeepSeek personal-account setting evidence or a machine-readable blocked record.
- `manifests/local/task8-step4/source/` — exactly two initial source manifests; no successor in this plan.
- `manifests/local/task8-step4/assessment-drafts/` — four JSON drafts containing sections 1–3 only.

### Repository-visible outputs

- Create: `docs/compliance/2026-08-07-task8-step4-evidence-status.md` — sanitized IDs, chosen routes, blocked state, evidence scope, and an explicit statement that Task 10 remains incomplete.
- Modify but do not stage: `SpecPilot_项目方案.md` — replace the five obsolete `gpt-5.6-luna` judge references with the confirmed `glm-5.2` model over the ChatAnywhere API route, without adding unsupported claims about the upstream processing vendor.

---

### Task 0: Reconcile the live Task 10 checkpoint before writing anything

**Files:**
- Read only: `docs/superpowers/plans/2026-08-06-w0-safety-egress-foundation.md`
- Read only: `docs/runbooks/w0-go-no-go.md`
- Must not overwrite: `artifacts/public/w0-verification.json`
- Must not overwrite: `docs/reports/w0-foundation-report.md`

**Interfaces:**
- Consumes: the live branch, working-tree status, and any Task 10 work created after this plan was written.
- Produces: a confirmed no-overlap execution boundary.

- [ ] **Step 1: Re-check the branch, HEAD, dirty files, and Task 10 outputs**

Run:

```bash
git status --short --branch
git log -1 --oneline --decorate
git merge-base --is-ancestor 3b9c423 HEAD
test -z "$(git diff --cached --name-only)"
test ! -e artifacts/public/w0-verification.json
test ! -e docs/reports/w0-foundation-report.md
```

Expected: branch `feat/w0-foundation`; `HEAD` descends from the committed Task 8 design `3b9c423` and includes this implementation plan as its own commit; the Git index is empty; the user-owned proposal is untracked; both Task 10 outputs are absent. If the plan is still untracked, commit only the reviewed plan before execution. If either Task 10 output now exists, stop and inspect it instead of overwriting or duplicating it. If unrelated dirty or staged files overlap this plan, stop and reconcile ownership first.

- [ ] **Step 2: Confirm this remains a Task 10 prerequisite, not a Task 9 replay**

Run:

```bash
sed -n '565,670p' docs/superpowers/plans/2026-08-06-w0-safety-egress-foundation.md
```

Expected: Task 9 remains fully checked; Task 10 remains unchecked and explicitly consumes Task 8 compliance/provider evidence. Do not rerun or modify Task 9 as part of this plan.

---

### Task 1: Freeze the two Release 18 source manifests

**Files:**
- Create locally: `artifacts/restricted/sources/3gpp/38.300/18.10.0/38300-ia0.zip`
- Create locally: `artifacts/restricted/sources/3gpp/38.321/18.10.0/38321-ia0.zip`
- Create locally: `data/real/3gpp/38.300/18.10.0/38300-ia0.docx`
- Create locally: `data/real/3gpp/38.321/18.10.0/38321-ia0.docx`
- Create locally: `manifests/local/task8-step4/source/*.json`

**Interfaces:**
- Consumes: `archive inspect` and `source-manifest create` from `src/specpilot/cli.py`.
- Produces: two initial manifest IDs plus exact archive and DOCX hashes for Tasks 2–4.

- [ ] **Step 1: Prove the exact destinations are unused**

Run:

```bash
.venv/bin/python - <<'PY'
import os
from pathlib import Path

targets = (
    Path("artifacts/restricted/sources/3gpp/38.300/18.10.0/38300-ia0.zip"),
    Path("artifacts/restricted/sources/3gpp/38.321/18.10.0/38321-ia0.zip"),
    Path("data/real/3gpp/38.300/18.10.0"),
    Path("data/real/3gpp/38.321/18.10.0"),
    Path("manifests/local/task8-step4"),
)
for target in targets:
    current = Path()
    for part in target.parts:
        current /= part
        if os.path.lexists(current):
            assert not current.is_symlink(), current
    assert not os.path.lexists(target), target
print("source destinations are absent and no existing parent is a symlink")
PY
test ! -e artifacts/restricted/sources/3gpp/38.300/18.10.0/38300-ia0.zip
test ! -L artifacts/restricted/sources/3gpp/38.300/18.10.0/38300-ia0.zip
test ! -e artifacts/restricted/sources/3gpp/38.321/18.10.0/38321-ia0.zip
test ! -L artifacts/restricted/sources/3gpp/38.321/18.10.0/38321-ia0.zip
test ! -e data/real/3gpp/38.300/18.10.0
test ! -L data/real/3gpp/38.300/18.10.0
test ! -e data/real/3gpp/38.321/18.10.0
test ! -L data/real/3gpp/38.321/18.10.0
test ! -e manifests/local/task8-step4
test ! -L manifests/local/task8-step4
```

Expected: the Python preflight prints its success message and all ten shell checks exit 0, including the dangling-symlink checks. If any target exists or any parent is a symlink, stop and inspect it; do not overwrite or delete it.

- [ ] **Step 2: Create only the required parent directories**

Run:

```bash
umask 077
mkdir -p artifacts/restricted/sources/3gpp/38.300/18.10.0
mkdir -p artifacts/restricted/sources/3gpp/38.321/18.10.0
mkdir -p data/real/3gpp/38.300
mkdir -p data/real/3gpp/38.321
mkdir -p data/quarantine/3gpp/38.300
mkdir -p data/quarantine/3gpp/38.321
mkdir -p manifests/local/task8-step4/source
mkdir -p manifests/local/task8-step4/assessment-drafts
```

Expected: directories exist and remain ignored by Git.

- [ ] **Step 3: Download, inspect, and manifest TS 38.300 v18.10.0**

Run the block in one shell so the exact timestamps and hashes stay bound to this download:

```bash
set -euo pipefail
umask 077
SPECPILOT_38300_TMP="$(mktemp artifacts/restricted/sources/3gpp/38.300/18.10.0/.38300-ia0.zip.XXXXXX)"
trap 'rm -f -- "$SPECPILOT_38300_TMP"' EXIT HUP INT TERM
curl --fail --location --silent --show-error --compressed \
  --remove-on-error \
  --user-agent 'SpecPilot-W0-evidence/1.0' \
  --output "$SPECPILOT_38300_TMP" \
  https://www.3gpp.org/ftp/Specs/archive/38_series/38.300/38300-ia0.zip
chmod 600 "$SPECPILOT_38300_TMP"
ln "$SPECPILOT_38300_TMP" artifacts/restricted/sources/3gpp/38.300/18.10.0/38300-ia0.zip
rm -f -- "$SPECPILOT_38300_TMP"
trap - EXIT HUP INT TERM
SPECPILOT_38300_DOWNLOADED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
SPECPILOT_38300_INSPECTION="$(.venv/bin/python -m specpilot.cli archive inspect \
  --archive artifacts/restricted/sources/3gpp/38.300/18.10.0/38300-ia0.zip \
  --destination data/real/3gpp/38.300/18.10.0 \
  --quarantine data/quarantine/3gpp/38.300 \
  --expect-docx 38300-ia0.docx)"
printf '%s\n' "$SPECPILOT_38300_INSPECTION"
SPECPILOT_38300_ARCHIVE_SHA="$(printf '%s' "$SPECPILOT_38300_INSPECTION" | .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin)["archive_sha256"])')"
SPECPILOT_38300_DOCX_SHA="$(printf '%s' "$SPECPILOT_38300_INSPECTION" | .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin)["docx_sha256"])')"
SPECPILOT_38300_CREATED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
.venv/bin/python -m specpilot.cli source-manifest create \
  --manifest-dir manifests/local/task8-step4/source \
  --document-id 3gpp-ts-38.300 \
  --document-version 18.10.0 \
  --download-url https://www.3gpp.org/ftp/Specs/archive/38_series/38.300/38300-ia0.zip \
  --archive-sha256 "$SPECPILOT_38300_ARCHIVE_SHA" \
  --docx-sha256 "$SPECPILOT_38300_DOCX_SHA" \
  --downloaded-at "$SPECPILOT_38300_DOWNLOADED_AT" \
  --created-at "$SPECPILOT_38300_CREATED_AT"
```

Expected: inspection prints `"status":"accepted"`; manifest creation prints `"status":"created"`, `"cloud_egress_authorized":false`, and a 64-character manifest ID. If inspection exits non-zero, stop before manifest creation; preserve the stable refusal code and move only any newly extracted rejected DOCX into its dedicated quarantine directory after checking the exact path.

- [ ] **Step 4: Download, inspect, and manifest TS 38.321 v18.10.0**

Run:

```bash
set -euo pipefail
umask 077
SPECPILOT_38321_TMP="$(mktemp artifacts/restricted/sources/3gpp/38.321/18.10.0/.38321-ia0.zip.XXXXXX)"
trap 'rm -f -- "$SPECPILOT_38321_TMP"' EXIT HUP INT TERM
curl --fail --location --silent --show-error --compressed \
  --remove-on-error \
  --user-agent 'SpecPilot-W0-evidence/1.0' \
  --output "$SPECPILOT_38321_TMP" \
  https://www.3gpp.org/ftp/Specs/archive/38_series/38.321/38321-ia0.zip
chmod 600 "$SPECPILOT_38321_TMP"
ln "$SPECPILOT_38321_TMP" artifacts/restricted/sources/3gpp/38.321/18.10.0/38321-ia0.zip
rm -f -- "$SPECPILOT_38321_TMP"
trap - EXIT HUP INT TERM
SPECPILOT_38321_DOWNLOADED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
SPECPILOT_38321_INSPECTION="$(.venv/bin/python -m specpilot.cli archive inspect \
  --archive artifacts/restricted/sources/3gpp/38.321/18.10.0/38321-ia0.zip \
  --destination data/real/3gpp/38.321/18.10.0 \
  --quarantine data/quarantine/3gpp/38.321 \
  --expect-docx 38321-ia0.docx)"
printf '%s\n' "$SPECPILOT_38321_INSPECTION"
SPECPILOT_38321_ARCHIVE_SHA="$(printf '%s' "$SPECPILOT_38321_INSPECTION" | .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin)["archive_sha256"])')"
SPECPILOT_38321_DOCX_SHA="$(printf '%s' "$SPECPILOT_38321_INSPECTION" | .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin)["docx_sha256"])')"
SPECPILOT_38321_CREATED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
.venv/bin/python -m specpilot.cli source-manifest create \
  --manifest-dir manifests/local/task8-step4/source \
  --document-id 3gpp-ts-38.321 \
  --document-version 18.10.0 \
  --download-url https://www.3gpp.org/ftp/Specs/archive/38_series/38.321/38321-ia0.zip \
  --archive-sha256 "$SPECPILOT_38321_ARCHIVE_SHA" \
  --docx-sha256 "$SPECPILOT_38321_DOCX_SHA" \
  --downloaded-at "$SPECPILOT_38321_DOWNLOADED_AT" \
  --created-at "$SPECPILOT_38321_CREATED_AT"
```

Expected: the same accepted/default-deny shape as Step 3, with a different manifest ID.

- [ ] **Step 5: Verify both stored manifests are initial and default-deny**

Run:

```bash
.venv/bin/python - <<'PY'
import hashlib
import os
import stat
from pathlib import Path

from specpilot.manifests.store import ManifestStore

manifest_dir = Path("manifests/local/task8-step4/source")
expected = {
    "3gpp-ts-38.300": {
        "version": "18.10.0",
        "url": "https://www.3gpp.org/ftp/Specs/archive/38_series/38.300/38300-ia0.zip",
        "archive": Path("artifacts/restricted/sources/3gpp/38.300/18.10.0/38300-ia0.zip"),
        "docx": Path("data/real/3gpp/38.300/18.10.0/38300-ia0.docx"),
    },
    "3gpp-ts-38.321": {
        "version": "18.10.0",
        "url": "https://www.3gpp.org/ftp/Specs/archive/38_series/38.321/38321-ia0.zip",
        "archive": Path("artifacts/restricted/sources/3gpp/38.321/18.10.0/38321-ia0.zip"),
        "docx": Path("data/real/3gpp/38.321/18.10.0/38321-ia0.docx"),
    },
}
store = ManifestStore(manifest_dir)
paths = sorted(manifest_dir.glob("*.json"))
assert len(paths) == 2
manifests = [store.read_source(path.stem) for path in paths]
assert {item.document_id for item in manifests} == set(expected)
for item in manifests:
    target = expected[item.document_id]
    assert item.document_version == target["version"]
    assert str(item.download_url) == target["url"]
    assert item.archive_sha256 == hashlib.sha256(target["archive"].read_bytes()).hexdigest()
    assert item.docx_sha256 == hashlib.sha256(target["docx"].read_bytes()).hexdigest()
    assert item.predecessor_manifest_id is None
    assert item.compliance_assessment is None
    assert item.provider_route_binding is None
    assert not item.cloud_egress_authorized
for path in [*paths, *(value[key] for value in expected.values() for key in ("archive", "docx"))]:
    mode = path.lstat().st_mode
    assert stat.S_ISREG(mode) and not path.is_symlink()
    assert stat.S_IMODE(mode) == 0o600, (path, oct(stat.S_IMODE(mode)))
for directory in (
    Path("artifacts/restricted"),
    Path("data/real"),
    Path("data/quarantine"),
    Path("manifests/local/task8-step4"),
):
    assert stat.S_IMODE(directory.lstat().st_mode) == 0o700
print([(item.document_id, item.document_version, item.manifest_id) for item in manifests])
PY
set -e
for path in \
  artifacts/restricted/sources/3gpp/38.300/18.10.0/38300-ia0.zip \
  artifacts/restricted/sources/3gpp/38.321/18.10.0/38321-ia0.zip \
  data/real/3gpp/38.300/18.10.0/38300-ia0.docx \
  data/real/3gpp/38.321/18.10.0/38321-ia0.docx \
  manifests/local/task8-step4/source
do
  git check-ignore -q -- "$path"
done
test -z "$(git ls-files -- artifacts/restricted data/real data/quarantine manifests/local/task8-step4)"
```

Expected: exactly two `(document_id, 18.10.0, manifest_id)` tuples; every URL and live ZIP/DOCX hash matches its manifest; all restricted files are `0600`, restricted roots are `0700`, every target is ignored, and no restricted file is tracked.

---

### Task 2: Capture official policy snapshots and create route evidence indexes

**Files:**
- Create locally: `artifacts/restricted/compliance/2026-08-07/snapshots/*`
- Create locally: `artifacts/restricted/compliance/2026-08-07/evidence-indexes/*.json`

**Interfaces:**
- Consumes: exact source IDs/hashes from Task 1 and the official URLs below.
- Produces: one canonical evidence-index SHA-256 for the DeepSeek main route and one for the ChatAnywhere judge route.

- [ ] **Step 1: Create restricted snapshot directories**

Run:

```bash
.venv/bin/python - <<'PY'
import os
from pathlib import Path

target = Path("artifacts/restricted/compliance/2026-08-07")
current = Path()
for part in target.parts:
    current /= part
    if os.path.lexists(current):
        assert not current.is_symlink(), current
assert not os.path.lexists(target), target
print("dated evidence destination is absent and no existing parent is a symlink")
PY
umask 077
test ! -e artifacts/restricted/compliance/2026-08-07
mkdir -p artifacts/restricted/compliance/2026-08-07/snapshots
mkdir -p artifacts/restricted/compliance/2026-08-07/evidence-indexes
mkdir -p artifacts/restricted/compliance/2026-08-07/account-evidence
mkdir -p artifacts/restricted/compliance/2026-08-07/capture-metadata
```

Use `apply_patch` to create the restricted helper `artifacts/restricted/compliance/2026-08-07/capture-page` with this exact content, then run `chmod 700` on it:

```sh
#!/bin/sh
set -eu
umask 077

if [ "$#" -ne 3 ]; then
    exit 64
fi

capture_name=$1
requested_url=$2
output_path=$3
case "$capture_name" in
    *[!a-zA-Z0-9._-]*|'') exit 64 ;;
esac
metadata_path="artifacts/restricted/compliance/2026-08-07/capture-metadata/${capture_name}.json"

case "$output_path" in
    artifacts/restricted/compliance/2026-08-07/snapshots/*) ;;
    *) exit 64 ;;
esac

test ! -e "$output_path"
test ! -L "$output_path"
test ! -e "$metadata_path"
test ! -L "$metadata_path"
output_tmp="$(mktemp "$(dirname "$output_path")/.capture.XXXXXX")"
metadata_tmp="$(mktemp "$(dirname "$metadata_path")/.capture.XXXXXX")"
trap 'rm -f -- "$output_tmp" "$metadata_tmp"' EXIT HUP INT TERM
effective_url="$(curl --fail --location --silent --show-error --compressed \
    --remove-on-error \
    --user-agent 'SpecPilot-W0-evidence/1.0' \
    --write-out '%{url_effective}' \
    --output "$output_tmp" \
    "$requested_url")"
captured_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
sha256="$(shasum -a 256 "$output_tmp" | awk '{print $1}')"
byte_count="$(wc -c < "$output_tmp" | tr -d ' ')"

.venv/bin/python - \
    "$metadata_tmp" "$capture_name" "$requested_url" "$effective_url" \
    "$captured_at" "$sha256" "$output_path" "$byte_count" <<'PY'
import json
import os
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
value = {
    "capture_name": sys.argv[2],
    "requested_url": sys.argv[3],
    "effective_url": sys.argv[4],
    "captured_at": sys.argv[5],
    "sha256": sys.argv[6],
    "output_name": Path(sys.argv[7]).name,
    "byte_count": int(sys.argv[8]),
}
assert value["requested_url"].startswith("https://")
assert value["effective_url"].startswith("https://")
assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value["captured_at"])
assert re.fullmatch(r"[0-9a-f]{64}", value["sha256"])
assert value["byte_count"] > 0
path.write_text(
    json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
    encoding="utf-8",
)
os.chmod(path, 0o600)
PY
chmod 600 "$output_tmp" "$metadata_tmp"
ln "$output_tmp" "$output_path"
if ! ln "$metadata_tmp" "$metadata_path"; then
    rm -f -- "$output_path"
    exit 1
fi
rm -f -- "$output_tmp" "$metadata_tmp"
trap - EXIT HUP INT TERM
```

Run:

```bash
chmod 700 artifacts/restricted/compliance/2026-08-07/capture-page
sh -n artifacts/restricted/compliance/2026-08-07/capture-page
```

Expected: the dated evidence directory did not already exist; all four directories are mode `0700`, the helper is mode `0700`, and everything remains ignored by Git. If the dated directory already existed, stop and inspect it rather than overwriting or deleting evidence.

- [ ] **Step 2: Fetch the source-side official pages as exact response bodies**

Run the capture helper once per source page. It writes the response and a machine-bound metadata record containing the effective URL, post-response UTC timestamp, exact SHA-256, and byte count.

```bash
artifacts/restricted/compliance/2026-08-07/capture-page 3gpp-terms https://www.3gpp.org/terms-of-use artifacts/restricted/compliance/2026-08-07/snapshots/3gpp-terms.html
artifacts/restricted/compliance/2026-08-07/capture-page 3gpp-specifications-by-series https://www.3gpp.org/specifications-technologies/specifications-by-series artifacts/restricted/compliance/2026-08-07/snapshots/3gpp-specifications-by-series.html
artifacts/restricted/compliance/2026-08-07/capture-page 3gpp-file-name-conventions https://www.3gpp.org/specifications-technologies/specifications-by-series/file-name-conventions artifacts/restricted/compliance/2026-08-07/snapshots/3gpp-file-name-conventions.html
artifacts/restricted/compliance/2026-08-07/capture-page 3gpp-38.300-archive https://www.3gpp.org/ftp/Specs/archive/38_series/38.300/ artifacts/restricted/compliance/2026-08-07/snapshots/3gpp-38.300-archive.html
artifacts/restricted/compliance/2026-08-07/capture-page 3gpp-38.321-archive https://www.3gpp.org/ftp/Specs/archive/38_series/38.321/ artifacts/restricted/compliance/2026-08-07/snapshots/3gpp-38.321-archive.html
artifacts/restricted/compliance/2026-08-07/capture-page etsi-ipr https://www.etsi.org/resources/intellectual-property-rights/ artifacts/restricted/compliance/2026-08-07/snapshots/etsi-ipr.html
artifacts/restricted/compliance/2026-08-07/capture-page etsi-terms https://www.etsi.org/terms/ artifacts/restricted/compliance/2026-08-07/snapshots/etsi-terms.html
```

Expected: seven non-empty files and seven same-name metadata records. The archive pages list `38300-ia0.zip` and `38321-ia0.zip`; the filename convention maps `i` to major version 18 and `a` to technical version 10.

- [ ] **Step 3: Fetch the DeepSeek route pages**

Run:

```bash
artifacts/restricted/compliance/2026-08-07/capture-page deepseek-api-docs https://api-docs.deepseek.com/ artifacts/restricted/compliance/2026-08-07/snapshots/deepseek-api-docs.html
artifacts/restricted/compliance/2026-08-07/capture-page deepseek-privacy https://cdn.deepseek.com/policies/zh-CN/deepseek-privacy-policy.html artifacts/restricted/compliance/2026-08-07/snapshots/deepseek-privacy.html
artifacts/restricted/compliance/2026-08-07/capture-page deepseek-terms https://cdn.deepseek.com/policies/zh-CN/deepseek-terms-of-use.html artifacts/restricted/compliance/2026-08-07/snapshots/deepseek-terms.html
```

Expected: three non-empty files and three same-name metadata records.

- [ ] **Step 4: Fetch the ChatAnywhere and GLM route pages**

Run:

```bash
artifacts/restricted/compliance/2026-08-07/capture-page chatanywhere-models https://docs.chatanywhere.tech/doc-2694962 artifacts/restricted/compliance/2026-08-07/snapshots/chatanywhere-models.html
artifacts/restricted/compliance/2026-08-07/capture-page chatanywhere-terms https://docs.chatanywhere.tech/doc-8793258 artifacts/restricted/compliance/2026-08-07/snapshots/chatanywhere-terms.html
artifacts/restricted/compliance/2026-08-07/capture-page chatanywhere-privacy https://docs.chatanywhere.tech/doc-8793261 artifacts/restricted/compliance/2026-08-07/snapshots/chatanywhere-privacy.html
artifacts/restricted/compliance/2026-08-07/capture-page chatanywhere-regions https://docs.chatanywhere.tech/doc-9081297 artifacts/restricted/compliance/2026-08-07/snapshots/chatanywhere-regions.html
artifacts/restricted/compliance/2026-08-07/capture-page glm-5-2-official https://docs.bigmodel.cn/cn/guide/models/text/glm-5.2 artifacts/restricted/compliance/2026-08-07/snapshots/glm-5-2-official.html
```

Expected: five non-empty files and five same-name metadata records.

- [ ] **Step 5: Record the DeepSeek ordinary-personal-account training setting**

Use the browser skill with the user's existing session. Navigate only to the DeepSeek account/privacy setting that controls whether data may be used to optimize the service. Never request, display, or store a password, API key, account ID, balance, or unrelated account data.

- If the setting is visible, save one tightly cropped screenshot under `account-evidence/` and create `account-evidence/deepseek-training-setting.observation.json` with only `status=observed`, `setting_state` (`enabled` or `disabled`), `captured_at`, `url`, and `screenshot_sha256`. Compute the screenshot hash before writing the record. Do not interpret the observed state as authorization.
- If authentication is required, pause for the user to sign in; do not enter credentials for them.
- If the setting still cannot be observed, create `account-evidence/deepseek-training-setting.blocked.json` containing only `status`, `reason`, `captured_at`, and the attempted HTTPS URL. Use `status=not_captured` and a factual reason such as `authentication_required` or `setting_not_found`; do not guess the setting.

Run after either branch:

```bash
chmod 600 artifacts/restricted/compliance/2026-08-07/account-evidence/*
```

Expected: either a hashed restricted screenshot plus the exact observation record, or a hashed restricted blocked record. The DeepSeek evidence index uses the screenshot SHA-256 in the observed branch and the blocked-record SHA-256 in the blocked branch. Both outcomes keep the route default-deny; the blocked outcome must remain a Task 10 blocker.

- [ ] **Step 6: Compute every response and account-evidence hash**

Run:

```bash
shasum -a 256 artifacts/restricted/compliance/2026-08-07/snapshots/*
shasum -a 256 artifacts/restricted/compliance/2026-08-07/account-evidence/*
shasum -a 256 artifacts/restricted/compliance/2026-08-07/capture-metadata/*
```

Expected: one lowercase 64-character SHA-256 per response, account-evidence, and capture-metadata file. The response hashes must match their generated metadata records. Do not copy response bodies, screenshots, or account metadata into Git.

- [ ] **Step 7: Write the two evidence indexes with exact hashes and timestamps**

Use `apply_patch` to create two JSON files named `deepseek-index.working.json` and `chatanywhere-index.working.json`. Both use:

```text
schema_version: compliance-evidence-index/v1
route: {endpoint_purpose, provider_id, use}
entries[]: {captured_at, kind, scope, sha256, summary, url}
```

Populate `url`, `captured_at`, and `sha256` only from the generated capture-metadata records; set each web entry's `kind` exactly to its `capture_name`. For the DeepSeek account entry, use `kind=deepseek-account-setting` and the evidence mapping defined in Step 5. The summaries must be original paraphrases, not copied paragraphs:

- Source provenance: the two official archive listings identify `ia0`; the official filename convention maps it to version 18.10.0.
- Source terms: public access/download does not itself grant broad redistribution; 3GPP/ETSI retain copyright and describe consent/request processes for reproduction.
- DeepSeek API: official base URL and current `deepseek-v4-flash` alias are documented; the alias may point to an updated snapshot.
- DeepSeek policy: input/output may be used for training after protective processing unless the user opts out; mainland-China storage and purpose-limited retention are stated; network logs may require at least six months.
- DeepSeek account setting: record only the observed `enabled`/`disabled` state and restricted evidence hash, or record that the state was not captured. Do not turn either state into an authorization conclusion.
- ChatAnywhere model route: `glm-5.2` is listed, but the page describes it as supplied by a third party.
- ChatAnywhere policy: content is not actively stored except for service, troubleshooting, security, dispute, or legal needs; no fixed period is stated; third parties may process inputs/outputs and are not named completely.
- GLM model page: confirms the public `glm-5.2` model identity and capabilities only; it does not prove ChatAnywhere's upstream route.

Expected: every entry has a real HTTPS URL, RFC3339 timestamp, lowercase SHA-256, short summary, and explicit scope. Both route indexes include the shared source-provenance and source-terms entries; only the DeepSeek index includes the account-setting entry.

Run:

```bash
chmod 600 artifacts/restricted/compliance/2026-08-07/evidence-indexes/deepseek-index.working.json
chmod 600 artifacts/restricted/compliance/2026-08-07/evidence-indexes/chatanywhere-index.working.json
```

- [ ] **Step 8: Compute canonical index IDs and content-address the files**

Run the entire block in one shell; the two index variables are intentionally kept together:

```bash
set -euo pipefail
SPECPILOT_DEEPSEEK_INDEX_SHA="$(.venv/bin/python -c 'import hashlib,json,pathlib; p=pathlib.Path("artifacts/restricted/compliance/2026-08-07/evidence-indexes/deepseek-index.working.json"); v=json.loads(p.read_text(encoding="utf-8")); b=json.dumps(v,allow_nan=False,ensure_ascii=False,separators=(",",":"),sort_keys=True).encode(); print(hashlib.sha256(b).hexdigest())')"
mv artifacts/restricted/compliance/2026-08-07/evidence-indexes/deepseek-index.working.json "artifacts/restricted/compliance/2026-08-07/evidence-indexes/${SPECPILOT_DEEPSEEK_INDEX_SHA}.json"
SPECPILOT_CHATANYWHERE_INDEX_SHA="$(.venv/bin/python -c 'import hashlib,json,pathlib; p=pathlib.Path("artifacts/restricted/compliance/2026-08-07/evidence-indexes/chatanywhere-index.working.json"); v=json.loads(p.read_text(encoding="utf-8")); b=json.dumps(v,allow_nan=False,ensure_ascii=False,separators=(",",":"),sort_keys=True).encode(); print(hashlib.sha256(b).hexdigest())')"
mv artifacts/restricted/compliance/2026-08-07/evidence-indexes/chatanywhere-index.working.json "artifacts/restricted/compliance/2026-08-07/evidence-indexes/${SPECPILOT_CHATANYWHERE_INDEX_SHA}.json"
printf '%s\n%s\n' "$SPECPILOT_DEEPSEEK_INDEX_SHA" "$SPECPILOT_CHATANYWHERE_INDEX_SHA"
.venv/bin/python - <<'PY'
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

base = Path("artifacts/restricted/compliance/2026-08-07")
root = base / "evidence-indexes"
metadata_root = base / "capture-metadata"
snapshot_root = base / "snapshots"
account_root = base / "account-evidence"
paths = sorted(root.glob("*.json"))
assert len(paths) == 2
rfc3339 = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$"
)
shared_kinds = {
    "3gpp-terms",
    "3gpp-specifications-by-series",
    "3gpp-file-name-conventions",
    "3gpp-38.300-archive",
    "3gpp-38.321-archive",
    "etsi-ipr",
    "etsi-terms",
}
expected_kinds = {
    "deepseek": shared_kinds
    | {
        "deepseek-api-docs",
        "deepseek-privacy",
        "deepseek-terms",
        "deepseek-account-setting",
    },
    "chatanywhere": shared_kinds
    | {
        "chatanywhere-models",
        "chatanywhere-terms",
        "chatanywhere-privacy",
        "chatanywhere-regions",
        "glm-5-2-official",
    },
}
expected_routes = {
    "deepseek": {
        "provider_id": "deepseek",
        "endpoint_purpose": "online-main-deepseek-v4-flash-api",
        "use": "online_main",
    },
    "chatanywhere": {
        "provider_id": "chatanywhere",
        "endpoint_purpose": "offline-judge-glm-5-2-api",
        "use": "offline_judge",
    },
}

metadata = {}
for metadata_path in metadata_root.glob("*.json"):
    item = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert set(item) == {
        "byte_count",
        "capture_name",
        "captured_at",
        "effective_url",
        "output_name",
        "requested_url",
        "sha256",
    }
    assert metadata_path.stem == item["capture_name"]
    snapshot_path = snapshot_root / item["output_name"]
    body = snapshot_path.read_bytes()
    assert len(body) == item["byte_count"] > 0
    assert hashlib.sha256(body).hexdigest() == item["sha256"]
    assert item["effective_url"].startswith("https://")
    assert rfc3339.fullmatch(item["captured_at"])
    captured = datetime.fromisoformat(item["captured_at"].replace("Z", "+00:00"))
    assert captured.tzinfo is not None
    metadata[item["capture_name"]] = item
assert set(metadata) == (expected_kinds["deepseek"] | expected_kinds["chatanywhere"]) - {
    "deepseek-account-setting"
}

observed_path = account_root / "deepseek-training-setting.observation.json"
blocked_path = account_root / "deepseek-training-setting.blocked.json"
assert observed_path.exists() ^ blocked_path.exists()
if observed_path.exists():
    account = json.loads(observed_path.read_text(encoding="utf-8"))
    assert set(account) == {
        "captured_at", "screenshot_sha256", "setting_state", "status", "url"
    }
    assert account["status"] == "observed"
    assert account["setting_state"] in {"enabled", "disabled"}
    account_sha = account["screenshot_sha256"]
    candidates = [
        item for item in account_root.iterdir() if item != observed_path
    ]
    assert any(hashlib.sha256(item.read_bytes()).hexdigest() == account_sha for item in candidates)
else:
    account = json.loads(blocked_path.read_text(encoding="utf-8"))
    assert set(account) == {"captured_at", "reason", "status", "url"}
    assert account["status"] == "not_captured"
    account_sha = hashlib.sha256(blocked_path.read_bytes()).hexdigest()
assert account["url"].startswith("https://")
assert rfc3339.fullmatch(account["captured_at"])
account_time = datetime.fromisoformat(account["captured_at"].replace("Z", "+00:00"))
assert account_time.tzinfo is not None
assert re.fullmatch(r"[0-9a-f]{64}", account_sha)

for path in paths:
    value = json.loads(path.read_text(encoding="utf-8"))
    canonical = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    assert path.stem == hashlib.sha256(canonical).hexdigest()
    assert set(value) == {"entries", "route", "schema_version"}
    assert value["schema_version"] == "compliance-evidence-index/v1"
    assert set(value["route"]) == {"endpoint_purpose", "provider_id", "use"}
    provider_id = value["route"]["provider_id"]
    assert value["route"] == expected_routes[provider_id]
    entries = {entry["kind"]: entry for entry in value["entries"]}
    assert len(entries) == len(value["entries"])
    assert set(entries) == expected_kinds[provider_id]
    for entry in value["entries"]:
        assert set(entry) == {
            "captured_at", "kind", "scope", "sha256", "summary", "url"
        }
        assert entry["url"].startswith("https://")
        assert re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])
        assert entry["summary"].strip() and entry["scope"].strip()
        assert rfc3339.fullmatch(entry["captured_at"])
        captured = datetime.fromisoformat(entry["captured_at"].replace("Z", "+00:00"))
        assert captured.tzinfo is not None
        if entry["kind"] == "deepseek-account-setting":
            assert entry["url"] == account["url"]
            assert entry["captured_at"] == account["captured_at"]
            assert entry["sha256"] == account_sha
        else:
            item = metadata[entry["kind"]]
            assert entry["url"] == item["effective_url"]
            assert entry["captured_at"] == item["captured_at"]
            assert entry["sha256"] == item["sha256"]
print("validated 2 indexes against 15 response files and account evidence")
PY
```

Expected: two different 64-character IDs, two JSON files named by those IDs, and `validated 2 indexes against 15 response files and account evidence`. This must pass before Step 9 may remove any response body.

- [ ] **Step 9: Apply the source-page retention rule**

Review the captured pages' applicable terms after the hashes, timestamps, summaries, and indexes are complete. Keep a response body only when continued local retention is supported. If continued retention is not supported, delete only the newly created response body after checking its exact path and hash; retain the URL, capture time, SHA-256, original summary, and scope in the content-addressed index. Never delete the indexes or account-evidence status record.

Expected: every retained response body has a documented retention basis; every removed response remains auditable by URL, timestamp, hash, and original summary.

- [ ] **Step 10: Verify indexes, permitted snapshots, and account evidence remain ignored**

Run:

```bash
.venv/bin/python - <<'PY'
import stat
from pathlib import Path

base = Path("artifacts/restricted/compliance/2026-08-07")
for path in base.rglob("*"):
    mode = path.lstat().st_mode
    assert not path.is_symlink()
    if path.is_dir():
        assert stat.S_IMODE(mode) == 0o700, (path, oct(stat.S_IMODE(mode)))
    elif path == base / "capture-page":
        assert stat.S_IMODE(mode) == 0o700
    else:
        assert stat.S_ISREG(mode)
        assert stat.S_IMODE(mode) == 0o600, (path, oct(stat.S_IMODE(mode)))
print("restricted evidence permissions verified")
PY
git check-ignore -q -- artifacts/restricted/compliance/2026-08-07
test -z "$(git ls-files -- artifacts/restricted/compliance/2026-08-07)"
```

Expected: `restricted evidence permissions verified`; every directory is `0700`, every data file is `0600`, the helper is `0700`, the tree is ignored, and no file under it is tracked.

---

### Task 3: Write and validate four unsigned assessment drafts

**Files:**
- Create locally: `manifests/local/task8-step4/assessment-drafts/3gpp-ts-38.300-v18.10.0__deepseek__online-main-deepseek-v4-flash-api.json`
- Create locally: `manifests/local/task8-step4/assessment-drafts/3gpp-ts-38.321-v18.10.0__deepseek__online-main-deepseek-v4-flash-api.json`
- Create locally: `manifests/local/task8-step4/assessment-drafts/3gpp-ts-38.300-v18.10.0__chatanywhere__offline-judge-glm-5-2-api.json`
- Create locally: `manifests/local/task8-step4/assessment-drafts/3gpp-ts-38.321-v18.10.0__chatanywhere__offline-judge-glm-5-2-api.json`

**Interfaces:**
- Consumes: exact page hashes/times and evidence-index IDs from Task 2.
- Produces: four JSON objects whose `source_terms`, `provider_policy`, and `outbound_limit` sections independently validate; none is a complete `ComplianceAssessment`.

- [ ] **Step 1: Fix the exact outbound premise and compute its hash**

Use this exact one-line premise in all four drafts:

```text
Under egress-policy/v1 default-v1, evidence, compliance, and verifier stages may contain only l1_query, l2_design, or l2_atomic_claim payloads, and the judge stage may contain only a judge payload. Each excerpt is limited to 1 excerpt, 512 model tokens, and 8192 UTF-8 bytes. L1 online unique use is limited to 5 excerpts, 2560 tokens, and 40960 bytes, with 10240 transmitted tokens and 163840 transmitted bytes. L2 online unique use is limited to 12 excerpts, 6144 tokens, and 98304 bytes; each atomic claim is limited to 4 excerpts, 2048 tokens, and 32768 bytes; L2 online transmitted use is limited to 24576 tokens and 393216 bytes. Judge unique use is limited to 5 excerpts, 2560 tokens, and 40960 bytes, with 5120 transmitted tokens and 81920 transmitted bytes. Evaluation-root unique limits are 10 excerpts, 5120 tokens, and 81920 bytes for L1 and 17 excerpts, 8704 tokens, and 139264 bytes for L2; transmitted limits are 15360 tokens and 245760 bytes for L1 and 29696 tokens and 475136 bytes for L2. Corpus-wide unique use is limited to 1024 excerpts, 524288 tokens, and 8388608 bytes. TOC limits are 12 nodes per call and 24 per run; L1 query input is limited to 1024 tokens, L2 design input to 2048 tokens, and one run to 3 L2 claims.
```

Run:

```bash
.venv/bin/python -c 'import hashlib; p="Under egress-policy/v1 default-v1, evidence, compliance, and verifier stages may contain only l1_query, l2_design, or l2_atomic_claim payloads, and the judge stage may contain only a judge payload. Each excerpt is limited to 1 excerpt, 512 model tokens, and 8192 UTF-8 bytes. L1 online unique use is limited to 5 excerpts, 2560 tokens, and 40960 bytes, with 10240 transmitted tokens and 163840 transmitted bytes. L2 online unique use is limited to 12 excerpts, 6144 tokens, and 98304 bytes; each atomic claim is limited to 4 excerpts, 2048 tokens, and 32768 bytes; L2 online transmitted use is limited to 24576 tokens and 393216 bytes. Judge unique use is limited to 5 excerpts, 2560 tokens, and 40960 bytes, with 5120 transmitted tokens and 81920 transmitted bytes. Evaluation-root unique limits are 10 excerpts, 5120 tokens, and 81920 bytes for L1 and 17 excerpts, 8704 tokens, and 139264 bytes for L2; transmitted limits are 15360 tokens and 245760 bytes for L1 and 29696 tokens and 475136 bytes for L2. Corpus-wide unique use is limited to 1024 excerpts, 524288 tokens, and 8388608 bytes. TOC limits are 12 nodes per call and 24 per run; L1 query input is limited to 1024 tokens, L2 design input to 2048 tokens, and one run to 3 L2 claims."; print(hashlib.sha256(p.encode()).hexdigest())'
shasum -a 256 src/specpilot/egress/policies/default-v1.json
```

Before copying the premise, compare every number and payload kind in it against `src/specpilot/egress/policies/default-v1.json`; stop if any value differs. Expected premise hash: `330e17c024b2da7e2b06563f12f039389b37f1862444a9b388520bfb65406c22`. Expected policy-file hash at the verified checkpoint: `ef19b1b0edd0344060ff0b8b46ab14987801157222f647cc5bce99940035fdd3`. If either differs, stop and rewrite/review the premise against the changed policy. Copy the exact premise and premise hash into all four drafts, and carry the policy-file hash into the sanitized status record.

- [ ] **Step 2: Create the two DeepSeek main-route drafts**

Use `apply_patch` and the exact Task 2 hashes/timestamps. Both JSON objects contain only `source_terms`, `provider_policy`, and `outbound_limit`. Set `provider_policy.policy_snapshot` to the DeepSeek privacy-policy effective HTTPS URL, response SHA-256, and capture time; the API docs, terms, and account-setting record remain supplemental evidence in the bound index.

Use these provider summaries verbatim:

```text
retention_summary: The public policy says personal information is retained only for the period necessary to provide the service; conversation records are retained for history, and deletion or anonymization follows account deletion or expiry except for legal, financial, audit, and dispute needs, including network-log retention required by law.
region_summary: The public policy says personal information collected during mainland-China operations is stored in mainland China and is not currently transferred overseas, subject to future lawful transfer procedures.
subprocessor_summary: The public policy permits affiliates and third-party partners to process necessary information under agreements and references a separate third-party sharing list; this draft records only the public-policy scope.
```

Choose exactly one `training_summary` ending based on Task 2 Step 5; never infer a state:

```text
Captured-disabled: The public policy says inputs and outputs may be used for model training and service optimization after protective processing and de-identification, and describes an account setting to opt out. The account-level data-use setting was observed disabled at the recorded capture time; this observation is not an authorization conclusion.
Captured-enabled: The public policy says inputs and outputs may be used for model training and service optimization after protective processing and de-identification, and describes an account setting to opt out. The account-level data-use setting was observed enabled at the recorded capture time; this observation is not an authorization conclusion.
Not-captured: The public policy says inputs and outputs may be used for model training and service optimization after protective processing and de-identification, and describes an account setting to opt out. The account setting state was not captured, so this draft makes no claim about its state.
```

The `Captured-disabled`, `Captured-enabled`, and `Not-captured` labels select the branch; they are not part of the JSON string.

Use the matching exact provider uncertainty statement:

```text
Captured: 本评估仅依据 DeepSeek 官方公开政策和已记录的账户设置状态，不对文档未披露的处理细节作额外推断。
Not-captured: 本评估仅依据 DeepSeek 官方公开政策；未取得账户设置状态，不对其状态或文档未披露的处理细节作额外推断。
```

The `Captured` and `Not-captured` labels select the branch; they are not part of the JSON string.

For the 38.300 source summary, name `3GPP TS 38.300 version 18.10.0`; for the 38.321 source summary, name `3GPP TS 38.321 version 18.10.0`. Each summary states that public availability does not itself grant broad redistribution and appends `evidence_index_sha256=` followed immediately by the DeepSeek evidence-index ID from Task 2.

- [ ] **Step 3: Create the two ChatAnywhere judge-route drafts**

Use `apply_patch` and the exact Task 2 hashes/timestamps. Set `provider_policy.policy_snapshot` to the ChatAnywhere privacy-policy effective HTTPS URL, response SHA-256, and capture time; the model list, terms, region page, and GLM official page remain supplemental evidence in the bound index. Use these provider summaries verbatim:

```text
retention_summary: The public policy says ChatAnywhere does not actively store input and output content except as necessary for real-time service, troubleshooting, security and risk control, disputes, or legal requirements; it gives no fixed content-retention period.
training_summary: The public documents say ChatAnywhere itself does not operate or train the routed models, but they do not state whether the unnamed upstream provider for glm-5.2 uses inputs or outputs for training.
region_summary: The public region page lists where the service may be accessed, including mainland China, but does not identify the processing location of ChatAnywhere or the unnamed glm-5.2 upstream route.
subprocessor_summary: The public policy allows unnamed model, cloud, infrastructure, payment, security, logging, support, and content-safety providers to process necessary information; the model list identifies glm-5.2 as third-party supplied without naming the supplier.
```

Use the user-approved exact provider uncertainty statement:

```text
本评估仅依据 ChatAnywhere API 公布文档，不对文档未披露的上游处理链作额外推断。
```

Each source summary names its exact document/version and appends `evidence_index_sha256=` followed by the ChatAnywhere evidence-index ID from Task 2.

- [ ] **Step 4: Use the same source-side assessment rule in all four files**

Use the effective HTTPS URL printed for the requested `https://www.3gpp.org/terms-of-use` page as `terms_snapshot.snapshot_url`, with its exact Task 2 response hash and timestamp. The source summary must be an original paraphrase of the 3GPP/ETSI evidence and contain the appropriate route evidence-index ID.

Use this exact source uncertainty statement:

```text
本评估仅依据所列 3GPP 与 ETSI 官方公开页面，不对页面未明确说明的 API 片段处理是否属于允许行为作额外推断。
```

Do not add `author_conclusion`, `authorized`, `author_id`, an expiry, or a successor ID.

- [ ] **Step 5: Validate the three component sections and prove the whole draft remains unsigned**

Run:

```bash
chmod 600 manifests/local/task8-step4/assessment-drafts/*.json
.venv/bin/python - <<'PY'
import hashlib
import json
import os
import stat
from pathlib import Path

from pydantic import ValidationError

from specpilot.contracts.manifests import (
    ComplianceAssessment,
    OutboundLimitAssessment,
    ProviderPolicyAssessment,
    SourceTermsAssessment,
)
from specpilot.manifests.store import ManifestStore

base = Path("artifacts/restricted/compliance/2026-08-07")
draft_root = Path("manifests/local/task8-step4/assessment-drafts")
index_paths = sorted((base / "evidence-indexes").glob("*.json"))
indexes = {}
for index_path in index_paths:
    index = json.loads(index_path.read_text(encoding="utf-8"))
    indexes[index["route"]["provider_id"]] = {
        "id": index_path.stem,
        "value": index,
    }
assert set(indexes) == {"deepseek", "chatanywhere"}

metadata = {
    path.stem: json.loads(path.read_text(encoding="utf-8"))
    for path in (base / "capture-metadata").glob("*.json")
}
expected = {
    "3gpp-ts-38.300-v18.10.0__deepseek__online-main-deepseek-v4-flash-api.json": (
        "3GPP TS 38.300 version 18.10.0",
        "deepseek",
        "deepseek-privacy",
    ),
    "3gpp-ts-38.321-v18.10.0__deepseek__online-main-deepseek-v4-flash-api.json": (
        "3GPP TS 38.321 version 18.10.0",
        "deepseek",
        "deepseek-privacy",
    ),
    "3gpp-ts-38.300-v18.10.0__chatanywhere__offline-judge-glm-5-2-api.json": (
        "3GPP TS 38.300 version 18.10.0",
        "chatanywhere",
        "chatanywhere-privacy",
    ),
    "3gpp-ts-38.321-v18.10.0__chatanywhere__offline-judge-glm-5-2-api.json": (
        "3GPP TS 38.321 version 18.10.0",
        "chatanywhere",
        "chatanywhere-privacy",
    ),
}
source_dir = Path("manifests/local/task8-step4/source")
source_store = ManifestStore(source_dir)
source_manifests = [
    source_store.read_source(path.stem) for path in sorted(source_dir.glob("*.json"))
]
manifest_labels = {
    f"3GPP TS {item.document_id.removeprefix('3gpp-ts-')} version {item.document_version}"
    for item in source_manifests
}
assert manifest_labels == {item[0] for item in expected.values()}
paths = sorted(draft_root.glob("*.json"))
assert {path.name for path in paths} == set(expected)
terms = metadata["3gpp-terms"]
draft_hashes = {}
for path in paths:
    mode = path.lstat().st_mode
    assert stat.S_ISREG(mode) and not path.is_symlink()
    assert stat.S_IMODE(mode) == 0o600
    value = json.loads(path.read_text(encoding="utf-8"))
    assert set(value) == {"source_terms", "provider_policy", "outbound_limit"}
    document_label, provider_id, policy_capture = expected[path.name]
    index_id = indexes[provider_id]["id"]
    policy = metadata[policy_capture]
    assert value["source_terms"]["terms_snapshot"] == {
        "snapshot_url": terms["effective_url"],
        "snapshot_sha256": terms["sha256"],
        "captured_at": terms["captured_at"],
    }
    assert document_label in value["source_terms"]["summary"]
    assert f"evidence_index_sha256={index_id}" in value["source_terms"]["summary"]
    assert value["provider_policy"]["policy_snapshot"] == {
        "snapshot_url": policy["effective_url"],
        "snapshot_sha256": policy["sha256"],
        "captured_at": policy["captured_at"],
    }
    assert value["outbound_limit"]["premise_sha256"] == (
        "330e17c024b2da7e2b06563f12f039389b37f1862444a9b388520bfb65406c22"
    )
    assert hashlib.sha256(
        value["outbound_limit"]["premise"].encode("utf-8")
    ).hexdigest() == value["outbound_limit"]["premise_sha256"]
    SourceTermsAssessment.model_validate(value["source_terms"])
    ProviderPolicyAssessment.model_validate(value["provider_policy"])
    OutboundLimitAssessment.model_validate(value["outbound_limit"])
    try:
        ComplianceAssessment.model_validate(value)
    except ValidationError as error:
        assert {item["loc"] for item in error.errors()} == {("author_conclusion",)}
    else:
        raise AssertionError(f"unsigned draft unexpectedly authorized: {path.name}")
    draft_hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()

hash_record = Path("manifests/local/task8-step4/draft-hashes.json")
flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
if hasattr(os, "O_NOFOLLOW"):
    flags |= os.O_NOFOLLOW
descriptor = os.open(hash_record, flags, 0o600)
with os.fdopen(descriptor, "w", encoding="utf-8") as output:
    json.dump(draft_hashes, output, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
print("validated exact 4-file matrix; complete assessment refused; hashes recorded locally")
PY
```

Expected: `validated exact 4-file matrix; complete assessment refused; hashes recorded locally`. The four draft hashes remain restricted and are not copied into the public status record because they indirectly bind account-setting evidence.

- [ ] **Step 6: Prove no successor was created and drafts remain ignored**

Run:

```bash
.venv/bin/python - <<'PY'
import stat
from pathlib import Path

from specpilot.manifests.store import ManifestStore

root = Path("manifests/local/task8-step4")
source = root / "source"
store = ManifestStore(source)
manifests = [store.read_source(path.stem) for path in source.glob("*.json")]
assert len(manifests) == 2
assert all(
    item.predecessor_manifest_id is None
    and item.compliance_assessment is None
    and item.provider_route_binding is None
    and not item.cloud_egress_authorized
    for item in manifests
)
assert len(list((root / "assessment-drafts").glob("*.json"))) == 4
assert (root / "draft-hashes.json").is_file()
for path in root.rglob("*"):
    mode = path.lstat().st_mode
    assert not path.is_symlink()
    if path.is_dir():
        assert stat.S_IMODE(mode) == 0o700
    else:
        assert stat.S_ISREG(mode)
        assert stat.S_IMODE(mode) == 0o600
print("two initial default-deny manifests; zero successors; four restricted drafts")
PY
git check-ignore -q -- manifests/local/task8-step4
test -z "$(git ls-files -- manifests/local/task8-step4)"
```

Expected: exactly the printed default-deny statement; all local files are private, ignored, and untracked.

---

### Task 4: Record the sanitized result and align the local project proposal

**Files:**
- Create: `docs/compliance/2026-08-07-task8-step4-evidence-status.md`
- Modify without staging: `SpecPilot_项目方案.md`

**Interfaces:**
- Consumes: the two initial manifest IDs, two evidence-index IDs, and four validated draft paths from Tasks 1–3.
- Produces: a public, non-sensitive status record and an internally consistent local project proposal.

- [ ] **Step 1: Update the five obsolete judge-model references in the user-owned proposal**

Use `apply_patch` only. Make these exact semantic changes:

- The model table names `glm-5.2` and says it is accessed through the ChatAnywhere API route and must be smoke-tested.
- The route paragraph says `glm-5.2` becomes the judge only after ChatAnywhere route availability, data-policy evidence, and real-corpus authorization exist.
- The W0 route-smoke item names `deepseek-v4-flash` and ChatAnywhere `glm-5.2`.
- Section 8.3 says the judge uses `glm-5.2` through a separate ChatAnywhere API route.
- Remove the unsupported statement that the actual processing vendors are proven independent; state only that the requested model slugs and direct API routes differ, while third-party upstream identity remains unproven and human blind audit is still required.

Run:

```bash
if rg -n 'gpt-5\.6-luna' SpecPilot_项目方案.md; then
  exit 1
fi
test "$(rg -c 'glm-5\.2|ChatAnywhere' SpecPilot_项目方案.md)" -ge 5
rg -n -C 2 'glm-5\.2|ChatAnywhere' SpecPilot_项目方案.md
```

Expected: the old model check exits cleanly with zero matches, the new model/API count is at least five lines, and the displayed contexts are accurate. Do not stage this untracked file.

- [ ] **Step 2: Write the sanitized evidence-status record**

Use `apply_patch` to create `docs/compliance/2026-08-07-task8-step4-evidence-status.md` with:

- exact source URLs, document IDs, version `18.10.0`, and the two initial manifest IDs;
- route identities and requested model slugs;
- the two evidence-index SHA-256 IDs, but no snapshot bodies or account metadata;
- the exact SHA-256 of `src/specpilot/egress/policies/default-v1.json` used to derive the outbound premise;
- confirmation that four local drafts contain sections 1–3 only;
- confirmation that `author_conclusion` is absent and no successor exists;
- `route_eligibility: extend` and `task10_decision: not_recorded`, making clear this is not `artifacts/public/w0-verification.json` and does not complete Task 10;
- blockers: author review/conclusion, route-to-model technical binding, real DeepSeek/ChatAnywhere fixture smoke, policy recheck immediately before any successor creation, plus missing account-setting evidence when Task 2 Step 5 was blocked;
- warning that this is self-assessment evidence, not approval or legal advice.

Expected: the document contains no excerpts, credentials, account IDs, account-setting value, private paths, provider payloads, or quality metrics.

- [ ] **Step 3: Run focused verification**

Run:

```bash
.venv/bin/python -m pytest tests/cli/test_manifest_commands.py -q
.venv/bin/python -m pytest tests/unit/manifests -q
git diff --check
```

Expected: both suites pass and `git diff --check` produces no output.

- [ ] **Step 4: Audit the commit boundary**

Run:

```bash
git status --short
sed -n '1,240p' docs/compliance/2026-08-07-task8-step4-evidence-status.md
test -z "$(git diff --cached --name-only)"
test ! -e artifacts/public/w0-verification.json
test ! -e docs/reports/w0-foundation-report.md
git add -- docs/compliance/2026-08-07-task8-step4-evidence-status.md
git diff --cached --check
test "$(git diff --cached --name-only)" = "docs/compliance/2026-08-07-task8-step4-evidence-status.md"
git diff --cached -- docs/compliance/2026-08-07-task8-step4-evidence-status.md
```

Expected: the full new status record is visibly reviewed; the staged-name assertion passes with exactly one file; the cached whitespace check is clean. `SpecPilot_项目方案.md` remains untracked; restricted artifacts do not appear; this plan has not fabricated either Task 10 output.

- [ ] **Step 5: Commit only the sanitized status record**

Run:

```bash
git commit --only -m "docs: record unsigned task8 compliance evidence" -- docs/compliance/2026-08-07-task8-step4-evidence-status.md
test -z "$(git diff --cached --name-only)"
test "$(git diff-tree --no-commit-id --name-only -r HEAD)" = "docs/compliance/2026-08-07-task8-step4-evidence-status.md"
git status --short
```

Expected: one commit containing only the sanitized status record. Task 8 Step 4 evidence drafting is complete, but the author-owned conclusion remains open; Task 10 remains incomplete, default-deny, and route-ineligible except for `extend`.

---

## Task 10 Handoff Boundary

After this plan is complete, Task 10 starts from its own Step 1. It must freshly run and record every hard verification, including PostgreSQL integration, fixture smoke, Docker builds, and service startup; it must consume the sanitized status record plus the author's eventual conclusion. It may record route A only after real DeepSeek and ChatAnywhere synthetic-fixture route smokes and an enforced route-to-model binding exist. Until then, its only valid decision is `extend`.
