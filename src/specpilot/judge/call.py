"""The judge call path: strict reply parsing and request assembly.

The judge is an independent scorer: its own request builder, its own reply
parser, and its own `JUDGE` egress stage — none of which the Verifier or the
answer path shares. A reply that cannot be read is a stable fault string, never
a guessed output: the calibration joins only records whose outputs parsed
under the exact schema the judge prompt prints.
"""

from __future__ import annotations

import json

from pydantic import ValidationError

from specpilot.contracts.egress import (
    EgressRequest,
    EgressStage,
    JudgePayload,
    TaskLevel,
    VersionMetadata,
)
from specpilot.contracts.manifests import RfcSourceManifest, SourceManifest
from specpilot.contracts.scoring import JudgeOutput


def parse_judge_reply(content: str) -> tuple[JudgeOutput | None, str | None]:
    """Read the model reply into a `JudgeOutput`, refusing rather than guessing.

    Returns `(output, None)` on success and `(None, fault_code)` otherwise. The
    fault codes are stable because the calibration CLI branches on them.
    """
    try:
        payload = json.loads(content)
    except ValueError:
        return None, "judge_reply_unreadable"
    try:
        return JudgeOutput.model_validate(payload), None
    except ValidationError:
        return None, "judge_reply_invalid"


def build_judge_request(
    payload: JudgePayload,
    *,
    source_manifest: SourceManifest | RfcSourceManifest,
    corpus_manifest_id: str,
    model_id: str,
    task_level: TaskLevel,
    evaluation_root_id: str,
    run_id: str,
) -> EgressRequest:
    """Assemble the one judge request this case is allowed to send.

    `stage` is `JUDGE` and never `EVIDENCE`: the caps are scoped per stage, and
    a scoring call priced against the evidence budget would spend an allowance
    reserved for answering. A manifest with no authorized route raises rather
    than sending — the same default-deny as the answer path.
    """
    if source_manifest.provider_route_binding is None:
        raise ValueError("source manifest carries no authorized provider route")
    return EgressRequest(
        evaluation_root_id=evaluation_root_id,
        run_id=run_id,
        task_level=task_level,
        version=VersionMetadata(
            source_manifest_id=source_manifest.manifest_id,
            corpus_manifest_id=corpus_manifest_id,
            document_id=source_manifest.document_id,
            document_version=source_manifest.document_version,
        ),
        stage=EgressStage.JUDGE,
        route=source_manifest.provider_route_binding,
        model_id=model_id,
        source_manifest=source_manifest,
        payload=payload,
    )
