from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import re
import sys
import time
import uuid
from collections.abc import Sequence
from contextlib import redirect_stderr, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from pydantic import ValidationError

from specpilot.annotation.progress import (
    PoolingAuditProgress,
    PoolingRunProgress,
    read_progress,
)
from specpilot.annotation.review import (
    DeepReviewStore,
    ReviewStore,
    deep_review_required,
    review_statistics,
)
from specpilot.annotation.store import AnnotationStore
from specpilot.contracts.annotation import (
    AnnotationOrigin,
    DeepReviewFinding,
    DeepReviewOutcome,
    DeepReviewScope,
    GoldOrigin,
    GoldOriginEvent,
    L1Annotation,
    L2Annotation,
    ReviewDecision,
    ReviewOutcome,
    UnsupportedAnnotationSchemaError,
    annotation_model_for_schema,
)
from specpilot.contracts.archive import ArchivePolicy, UnsafeArchiveError
from specpilot.contracts.corpus_manifest import CorpusManifest
from specpilot.contracts.egress import (
    EgressRequest,
    EgressStage,
    EvidenceExcerpt,
    JudgePayload,
    L1OnlinePayload,
    L2AtomicClaimPayload,
    NormalizedExcerptSpan,
    ReservationOutcome,
    ScoringPoint,
    TaskLevel,
    TocNode,
    VersionMetadata,
)
from specpilot.contracts.manifests import (
    ComplianceAssessment,
    ProviderRouteBinding,
    ProviderUse,
    RfcSourceManifest,
    SourceManifest,
    SourceManifestDraft,
)
from specpilot.contracts.proposal import (
    Proposal,
    UnsupportedProposalSchemaError,
    proposal_for_schema,
)
from specpilot.contracts.rfc import RfcLimits, UnsafeRfcError
from specpilot.corpus.clauses import (
    EXCLUDED_SECTIONS,
    Clause,
    ClauseLimits,
    OversizedClauseError,
    build_clauses,
    build_normative_index,
    iter_clause_texts,
)
from specpilot.corpus.distractors import select_distractors
from specpilot.corpus.freezing import (
    CorpusManifestRefusal,
    CorpusSourceInput,
    FreezeCorpusRequest,
    VerifyCorpusRequest,
    freeze_corpus,
    verify_corpus,
)
from specpilot.corpus.overlap import question_gold_jaccard, restates
from specpilot.corpus.qa import QaThresholds, run_parse_qa
from specpilot.corpus.walk import (
    InvalidDocumentIdentityError,
    document_identity,
)
from specpilot.egress.enforcer import EgressPolicyEnforcer, EgressPolicyViolation
from specpilot.egress.policy import EgressPolicy
from specpilot.embedding.local_encoder import (
    EmbeddingRuntimeUnavailable,
    load_encoder,
    load_token_counter,
)
from specpilot.embedding.throughput import (
    BatchOrder,
    estimate_full_corpus_seconds,
    evenly_spaced,
    measure_throughput,
    weights_sha256,
)
from specpilot.evaluation.retrieval import (
    RetrievedItem,
    score_route,
    stratify_by_overlap,
)
from specpilot.ingestion.archive import extract_expected_docx
from specpilot.ingestion.ooxml import OoxmlLimits, UnsafeOoxmlError, inspect_docx
from specpilot.ingestion.rfc import VerifiedRfc, read_rfc_snapshot, verify_rfc_snapshot
from specpilot.manifests.corpus_store import CorpusManifestStore
from specpilot.manifests.store import ManifestStore, UnsupportedManifestVersionError
from specpilot.retrieval.bm25 import Bm25Index
from specpilot.retrieval.dense import DenseBackendUnavailable, DenseIndex
from specpilot.retrieval.hybrid import (
    RouteRanking,
    RrfParameters,
    reciprocal_rank_fusion,
)
from specpilot.retrieval.local import LocalCorpus
from specpilot.retrieval.pooling import (
    PoolingCandidate,
    PoolingDecision,
    PoolingItem,
    PoolingOutcome,
    PoolingRun,
    PoolingStore,
    PoolingUnitFact,
    apply_decision,
    build_pool,
    head_decisions,
    inventory_sha256,
    seal_run,
)
from specpilot.retrieval.protocol import locator_for_unit
from specpilot.rfc.structure import extract_structure

# Exit codes, matching the ingestion worker: 2 is a refused input or policy
# violation, 3 is an I/O fault, 4 is bad usage. Every non-zero exit prints one
# stable code to stderr and nothing else.
EXIT_REFUSED = 2
EXIT_IO = 3
EXIT_USAGE = 4

LIVE_ROUTE_NAMES = ("main", "judge")

_FIXTURE_CORPUS_ID = "c" * 64


def _emit(payload: dict[str, Any]) -> int:
    json.dump(payload, sys.stdout, sort_keys=True, separators=(",", ":"))
    sys.stdout.write("\n")
    return 0


def _refuse(code: str, exit_code: int = EXIT_REFUSED) -> int:
    """Print one machine-readable code. Never a path, message, or payload."""
    print(code, file=sys.stderr)
    return exit_code


def _source_inputs(
    arguments: argparse.Namespace,
) -> tuple[CorpusSourceInput, ...] | str:
    if len(arguments.manifest) != len(arguments.xml):
        return "source_pair_count_mismatch"
    pairs = tuple(
        CorpusSourceInput(manifest_id, xml_path)
        for manifest_id, xml_path in zip(
            arguments.manifest,
            arguments.xml,
            strict=True,
        )
    )
    if len({item.manifest_id for item in pairs}) != len(pairs):
        return "duplicate_source_manifest"
    return pairs


def _corpus_manifest_payload(
    status: str,
    manifest: CorpusManifest,
) -> dict[str, Any]:
    return {
        "status": status,
        "corpus_manifest_id": manifest.manifest_id,
        "source_manifest_ids": manifest.source_manifest_ids,
        "collection": manifest.collection_name,
        "point_count": manifest.point_count,
        "derived_corpus_sha256": manifest.derived_corpus_sha256,
        "inventory_root_sha256": manifest.inventory_root_sha256,
        "snapshot_name": manifest.snapshot.name,
        "snapshot_checksum": manifest.snapshot.checksum,
        "snapshot_size_bytes": manifest.snapshot.size_bytes,
    }


def _corpus_freeze(arguments: argparse.Namespace) -> int:
    sources = _source_inputs(arguments)
    if isinstance(sources, str):
        return _refuse(sources, EXIT_USAGE)
    try:
        result = freeze_corpus(
            FreezeCorpusRequest(
                sources=sources,
                model_dir=arguments.model_dir,
                qdrant_url=arguments.qdrant_url,
                collection_name=arguments.collection,
                predecessor_manifest_id=arguments.predecessor,
                created_at=arguments.created_at,
            ),
            source_store=ManifestStore(arguments.source_manifest_dir),
            corpus_store=CorpusManifestStore(arguments.corpus_manifest_dir),
        )
    except CorpusManifestRefusal as error:
        return _refuse(error.code)
    except (OSError, ValueError, RuntimeError):
        return _refuse("corpus_manifest_unavailable", EXIT_IO)
    return _emit(
        _corpus_manifest_payload(
            "replayed" if result.replayed else "frozen",
            result.manifest,
        )
    )


def _corpus_verify(arguments: argparse.Namespace) -> int:
    sources = _source_inputs(arguments)
    if isinstance(sources, str):
        return _refuse(sources, EXIT_USAGE)
    try:
        verified = verify_corpus(
            VerifyCorpusRequest(
                manifest_id=arguments.corpus_manifest,
                sources=sources,
                model_dir=arguments.model_dir,
                qdrant_url=arguments.qdrant_url,
            ),
            source_store=ManifestStore(arguments.source_manifest_dir),
            corpus_store=CorpusManifestStore(arguments.corpus_manifest_dir),
        )
        try:
            payload = _corpus_manifest_payload("verified", verified.manifest)
        finally:
            verified.close()
    except CorpusManifestRefusal as error:
        return _refuse(error.code)
    except (OSError, ValueError, RuntimeError):
        return _refuse("corpus_manifest_unavailable", EXIT_IO)
    return _emit(payload)


def _archive_inspect(arguments: argparse.Namespace) -> int:
    policy = ArchivePolicy(
        expected_docx_name=arguments.expect_docx,
        max_members=arguments.max_members,
        max_member_bytes=arguments.max_member_bytes,
        max_total_bytes=arguments.max_total_bytes,
    )
    try:
        extraction = extract_expected_docx(
            arguments.archive,
            arguments.destination,
            arguments.quarantine,
            policy,
        )
    except UnsafeArchiveError as error:
        return _refuse(error.code.value)
    except OSError:
        return _refuse("io_error", EXIT_IO)

    try:
        inspection = inspect_docx(
            arguments.destination / arguments.expect_docx,
            OoxmlLimits(),
        )
    except UnsafeOoxmlError as error:
        return _refuse(error.code.value)
    except OSError:
        return _refuse("io_error", EXIT_IO)

    return _emit(
        {
            "status": "accepted",
            "archive_sha256": extraction.archive_sha256,
            "docx_sha256": extraction.docx_sha256,
            "byte_count": extraction.byte_count,
            "member_count": inspection.member_count,
            "relationship_count": inspection.relationship_count,
        }
    )


def _manifest_create(arguments: argparse.Namespace) -> int:
    try:
        draft = SourceManifestDraft(
            document_id=arguments.document_id,
            document_version=arguments.document_version,
            download_url=arguments.download_url,
            archive_sha256=arguments.archive_sha256,
            docx_sha256=arguments.docx_sha256,
            downloaded_at=arguments.downloaded_at,
            created_at=arguments.created_at,
        )
    except ValidationError:
        return _refuse("invalid_manifest_fields", EXIT_USAGE)
    try:
        manifest = ManifestStore(arguments.manifest_dir).create_source(draft)
    except (OSError, ValueError, RuntimeError):
        return _refuse("manifest_not_written", EXIT_IO)
    return _emit(
        {
            "status": "created",
            "manifest_id": manifest.manifest_id,
            "cloud_egress_authorized": manifest.cloud_egress_authorized,
            "predecessor_manifest_id": manifest.predecessor_manifest_id,
        }
    )


def _manifest_authorize(arguments: argparse.Namespace) -> int:
    """Turn a completed self-assessment into a route-bound successor manifest.

    Every judgement here is the author's, recorded in the assessment file. This
    command only checks that the evidence is complete and internally consistent
    and that its conclusion matches the route being bound; it never infers,
    supplies, or upgrades a conclusion.
    """
    store = ManifestStore(arguments.manifest_dir)
    try:
        predecessor = store.read_source(arguments.predecessor)
    except UnsupportedManifestVersionError:
        return _refuse("unsupported_manifest_version")
    except (OSError, ValueError, RuntimeError):
        return _refuse("manifest_not_found")

    try:
        assessment = ComplianceAssessment.model_validate_json(
            arguments.assessment.read_text(encoding="utf-8")
        )
    except (ValidationError, OSError, ValueError):
        return _refuse("invalid_authorization_evidence")

    # Successors currently exist only for v1 sources. Anything else is refused
    # rather than coerced, so a corpus change can never silently authorize one.
    if not isinstance(predecessor, SourceManifest):
        return _refuse("invalid_authorization_evidence")

    binding = ProviderRouteBinding(
        provider_id=arguments.provider_id,
        endpoint_purpose=arguments.endpoint_purpose,
        use=ProviderUse(arguments.use),
    )
    try:
        successor = store.create_successor(
            predecessor,
            assessment=assessment,
            route_binding=binding,
            created_at=arguments.created_at,
        )
    except (ValidationError, ValueError):
        return _refuse("invalid_authorization_evidence")
    except (OSError, RuntimeError):
        return _refuse("manifest_not_written", EXIT_IO)

    return _emit(
        {
            "status": "authorized",
            "manifest_id": successor.manifest_id,
            "predecessor_manifest_id": predecessor.manifest_id,
            "cloud_egress_authorized": successor.cloud_egress_authorized,
            "provider_id": binding.provider_id,
            "endpoint_purpose": binding.endpoint_purpose,
            "use": binding.use.value,
        }
    )


@dataclass(frozen=True, slots=True)
class _FrozenRfcSource:
    manifest: RfcSourceManifest
    document: VerifiedRfc


def _frozen_source(arguments: argparse.Namespace) -> _FrozenRfcSource | str:
    """Resolve and verify the single snapshot frozen by one manifest.

    The manifest is the authority on which bytes are the corpus. A file that
    hashes differently is a different document, whatever its filename says, so
    it is refused before any reader ever sees it.
    """
    store = ManifestStore(arguments.manifest_dir)
    try:
        manifest = store.read_source(arguments.manifest)
    except UnsupportedManifestVersionError:
        return "unsupported_manifest_version"
    except (OSError, ValueError, RuntimeError):
        return "manifest_not_found"

    if not isinstance(manifest, RfcSourceManifest):
        # A DOCX-shaped manifest describes a different corpus; it has no XML
        # rendition to be the authority for.
        return "unsupported_manifest_version"

    try:
        snapshot = read_rfc_snapshot(arguments.xml, RfcLimits())
    except UnsafeRfcError as error:
        return error.code.value
    except OSError:
        return "io_error"
    if snapshot.document_sha256 != manifest.xml_sha256:
        return "document_hash_mismatch"

    try:
        document = verify_rfc_snapshot(snapshot)
        document_id, document_version = document_identity(document.root)
    except UnsafeRfcError as error:
        return error.code.value
    except InvalidDocumentIdentityError:
        return "invalid_document_identity"
    except OSError:
        return "io_error"
    if manifest.document_id != document_id:
        return "document_id_mismatch"
    if manifest.document_version != document_version:
        return "document_version_mismatch"
    return _FrozenRfcSource(manifest=manifest, document=document)


def _refuse_source(code: str) -> int:
    return _refuse(code, EXIT_IO if code == "io_error" else EXIT_REFUSED)


def _clause_limits(manifest: RfcSourceManifest) -> ClauseLimits:
    """Clause bounds, with the corpus's exclusions applied.

    Exclusions are declared once, by anchor, and applied to every document. The
    earlier version keyed them on `manifest.document_id`, which silently let RFC
    9112's identical collected-ABNF appendix into the index; the reason for the
    exclusion is a property of the content, not of which RFC happens to carry
    it. Reading them from a constant rather than a flag still means a run cannot
    quietly index a section this corpus decided to leave out.
    """
    del manifest  # identity no longer selects the exclusion set
    return ClauseLimits(excluded_sections=EXCLUDED_SECTIONS)


def _corpus_parse(arguments: argparse.Namespace) -> int:
    """Parse one frozen specification, reporting counts and never its text."""
    source = _frozen_source(arguments)
    if isinstance(source, str):
        return _refuse_source(source)
    manifest = source.manifest

    try:
        structure = extract_structure(source.document, RfcLimits())
        clauses = build_clauses(source.document, RfcLimits(), _clause_limits(manifest))
    except UnsafeRfcError as error:
        return _refuse(error.code.value)
    except OversizedClauseError:
        return _refuse("clause_too_large")
    except OSError:
        return _refuse("io_error", EXIT_IO)

    return _emit(
        {
            "status": "parsed",
            "document_id": manifest.document_id,
            "document_version": manifest.document_version,
            "source_manifest_id": manifest.manifest_id,
            "xml_sha256": structure.document_sha256,
            "section_count": structure.section_count,
            "clause_count": len(clauses),
            "cross_reference_count": len(structure.cross_references),
            "dangling_cross_references": structure.dangling_count,
        }
    )


def _section_matches(number: str | None, prefix: str) -> bool:
    """True when `number` is `prefix` or sits beneath it.

    Compared component by component so `--section 1` selects 1.2 but not 10,
    which a string prefix would wrongly include.
    """
    if number is None:
        return False
    parts, wanted = number.split("."), prefix.split(".")
    return parts[: len(wanted)] == wanted


def _corpus_clauses(arguments: argparse.Namespace) -> int:
    """List clause identities so a gold field has something to reference.

    Section 8.2.1 has the author navigate the frozen renditions themselves; this
    only translates "the third paragraph of §5.6.2", which they found there, into
    the identifier a record stores. It emits locators and counts — never the
    paragraph.
    """
    source = _frozen_source(arguments)
    if isinstance(source, str):
        return _refuse_source(source)
    manifest = source.manifest

    try:
        clauses = build_clauses(source.document, RfcLimits(), _clause_limits(manifest))
    except UnsafeRfcError as error:
        return _refuse(error.code.value)
    except OversizedClauseError:
        return _refuse("clause_too_large")
    except OSError:
        return _refuse("io_error", EXIT_IO)

    for clause in clauses:
        if arguments.section and not _section_matches(
            clause.section_number, arguments.section
        ):
            continue
        json.dump(
            {
                "clause_id": clause.clause_id,
                "section_number": clause.section_number,
                "section_path": clause.section_path,
                "ordinal": clause.ordinal,
                "anchor": clause.anchor,
                "word_count": clause.word_count,
                "byte_count": clause.byte_count,
            },
            sys.stdout,
            sort_keys=True,
            separators=(",", ":"),
        )
        sys.stdout.write("\n")
    return 0


def _corpus_qa(arguments: argparse.Namespace) -> int:
    """Run §4.1's blocking parse QA and report every line's measured value.

    Exits non-zero when any line fails, so `corpus freeze` can depend on it
    rather than on someone remembering. Every value is printed whether it passed
    or not: a gate that only says "pass" cannot show a regression coming.

    `--model-dir` is required rather than optional. The `excerpt_fit` line needs
    the tokenizer that the index will actually use, and without one it reports
    zero of zero and fails -- so making it optional would only offer a way to
    reach a failing gate more slowly.
    """
    source = _frozen_source(arguments)
    if isinstance(source, str):
        return _refuse_source(source)
    manifest = source.manifest

    try:
        count_tokens = load_token_counter(arguments.model_dir)
    except EmbeddingRuntimeUnavailable:
        return _refuse("embedding_runtime_unavailable")
    except OSError:
        return _refuse("io_error", EXIT_IO)

    try:
        report = run_parse_qa(
            source.document,
            RfcLimits(),
            _clause_limits(manifest),
            QaThresholds(),
            count_tokens=count_tokens,
        )
    except UnsafeRfcError as error:
        return _refuse(error.code.value)
    except OversizedClauseError:
        return _refuse("clause_too_large")
    except OSError:
        return _refuse("io_error", EXIT_IO)

    _emit(
        {
            "status": "passed" if report.passed else "failed",
            "document_id": report.document_id,
            "lines": {
                line.name: {
                    "measured": round(line.measured, 6),
                    "threshold": line.threshold,
                    "passed": line.passed,
                    "numerator": line.numerator,
                    "denominator": line.denominator,
                }
                for line in report.lines
            },
        }
    )
    return 0 if report.passed else EXIT_REFUSED


def _corpus_normative(arguments: argparse.Namespace) -> int:
    """List each section with how many requirements it states.

    A shortlist of where to read, built by literal search over the frozen bytes
    — §8.2.1's own second path, not the system's retriever. It narrows the
    reading; the author still reads the section, writes the question, and picks
    the gold clause, none of which this can do.

    Sections with no requirements are printed too unless `--min-keywords`
    excludes them, because a candidate list that silently drops what it did not
    like cannot be checked against the document.
    """
    source = _frozen_source(arguments)
    if isinstance(source, str):
        return _refuse_source(source)
    manifest = source.manifest

    try:
        index = build_normative_index(
            source.document, RfcLimits(), _clause_limits(manifest)
        )
    except UnsafeRfcError as error:
        return _refuse(error.code.value)
    except OversizedClauseError:
        return _refuse("clause_too_large")
    except OSError:
        return _refuse("io_error", EXIT_IO)

    for entry in index:
        if entry.normative_total < arguments.min_keywords:
            continue
        if arguments.section and not _section_matches(
            entry.section_number, arguments.section
        ):
            continue
        json.dump(
            {
                "section_number": entry.section_number,
                "section_path": entry.section_path,
                "section_anchor": entry.section_anchor,
                "clause_count": entry.clause_count,
                "word_count": entry.word_count,
                "normative_total": entry.normative_total,
                "keyword_counts": entry.keyword_counts,
            },
            sys.stdout,
            sort_keys=True,
            separators=(",", ":"),
        )
        sys.stdout.write("\n")
    return 0


def _corpus_overlap(arguments: argparse.Namespace) -> int:
    """Compute the literal overlap the annotation contract requires.

    The clause text is read here and never leaves: what comes back is one float.
    This is arithmetic over the author's own question and their own chosen gold,
    not a judgement about either.
    """
    source = _frozen_source(arguments)
    if isinstance(source, str):
        return _refuse_source(source)
    manifest = source.manifest

    try:
        texts = {
            clause.clause_id: text
            for clause, text in iter_clause_texts(
                source.document, RfcLimits(), _clause_limits(manifest)
            )
        }
    except UnsafeRfcError as error:
        return _refuse(error.code.value)
    except OversizedClauseError:
        return _refuse("clause_too_large")
    except OSError:
        return _refuse("io_error", EXIT_IO)

    wanted = tuple(dict.fromkeys(arguments.clause_id))
    if any(clause_id not in texts for clause_id in wanted):
        # An overlap figure against a clause this document does not contain
        # would be a number about nothing.
        return _refuse("unknown_clause_id")

    return _emit(
        {
            "status": "measured",
            "document_id": manifest.document_id,
            "gold_clause_count": len(wanted),
            "question_gold_jaccard": round(
                question_gold_jaccard(
                    arguments.question, [texts[clause_id] for clause_id in wanted]
                ),
                4,
            ),
        }
    )


_L1_TEMPLATE: dict[str, Any] = {
    "schema_version": "annotation-l1/v2",
    "item_id": "l1-dev-001",
    "split": "dev",
    "question": "",
    "direction": "clause_first",
    "content_origin": "human",
    "label_origin": "human",
    "document_id": "ietf-rfc-9110",
    "document_version": "2022-06",
    "gold_clause_ids": [],
    "gold_section_paths": [],
    "key_points": [{"point_id": "kp-1", "criterion": "", "factual_values": []}],
    "expected_refusal": False,
    "question_gold_jaccard": None,
    "gold_origins": [],
}
_L2_TEMPLATE: dict[str, Any] = {
    **_L1_TEMPLATE,
    "schema_version": "annotation-l2/v2",
    "item_id": "l2-dev-001",
    "claim_id": "l2-dev-001-c1",
    "expected_verdict": "compliant",
    "proposed_verdict": "compliant",
    "supports_verdict": True,
}


def _annotation_template(arguments: argparse.Namespace) -> int:
    """Print a skeleton record.

    Left deliberately invalid: `question` is empty and there is no gold, so the
    contract refuses it until the author has actually done the work. A template
    that validated as written would invite twenty-three copies of itself.
    """
    template = _L2_TEMPLATE if arguments.level == "l2" else _L1_TEMPLATE
    json.dump(template, sys.stdout, indent=2, ensure_ascii=False, sort_keys=True)
    sys.stdout.write("\n")
    return 0


def _check_gold_against_source(
    arguments: argparse.Namespace, record: L1Annotation | L2Annotation
) -> str | None:
    """Check an answerable record against the document it names.

    Three things a record can get wrong that no amount of shape validation
    catches: naming a clause the document does not contain, naming the wrong
    document, and writing a key point that restates its clause instead of
    stating a criterion. All three need the source, so the source is required
    whenever the record carries gold.
    """
    if arguments.xml is None or arguments.manifest is None:
        return "source_required_for_gold"

    source = _frozen_source(arguments)
    if isinstance(source, str):
        return source
    manifest = source.manifest
    if manifest.document_id != record.document_id:
        # A record pointed at the wrong document would resolve its gold against
        # clauses from a specification it does not cite.
        return "document_id_mismatch"
    if manifest.document_version != record.document_version:
        return "document_version_mismatch"

    try:
        texts = {
            clause.clause_id: text
            for clause, text in iter_clause_texts(
                source.document, RfcLimits(), _clause_limits(manifest)
            )
        }
    except UnsafeRfcError as error:
        return error.code.value
    except OversizedClauseError:
        return "clause_too_large"
    except OSError:
        return "io_error"

    missing = [
        clause_id for clause_id in record.gold_clause_ids if clause_id not in texts
    ]
    if missing:
        return "unknown_gold_clause"

    gold = [texts[clause_id] for clause_id in record.gold_clause_ids]
    for point in record.key_points:
        if any(restates(point.criterion, clause) for clause in gold):
            # §8.1: a key point may carry necessary factual values but may not
            # reproduce the clause's wording as a sentence.
            return "key_point_restates_clause"
    return None


def _annotation_add(arguments: argparse.Namespace) -> int:
    """Validate one authored record and store it.

    Every rule the contract encodes bites here: an answerable item needs gold,
    its source provenance, and an overlap figure, while an unanswerable one may
    not carry gold. A refusal writes nothing.
    """
    try:
        data = json.loads(arguments.record.read_text(encoding="utf-8"))
    except OSError:
        return _refuse("io_error", EXIT_IO)
    except ValueError:
        return _refuse("invalid_annotation_record")

    if not isinstance(data, dict):
        return _refuse("invalid_annotation_record")
    try:
        model = annotation_model_for_schema(data.get("schema_version"))
    except UnsupportedAnnotationSchemaError:
        return _refuse("unsupported_annotation_schema")
    try:
        record = model.model_validate(data)
    except ValidationError:
        return _refuse("invalid_annotation_record")

    if record.gold_clause_ids:
        refusal = _check_gold_against_source(arguments, record)
        if refusal is not None:
            return _refuse_source(refusal)

    try:
        stored = AnnotationStore(arguments.annotation_dir).create(record)
    except UnsupportedAnnotationSchemaError:
        return _refuse("unsupported_annotation_schema")
    except ValueError:
        return _refuse("item_id_already_annotated")
    except (OSError, RuntimeError):
        return _refuse("annotation_not_written", EXIT_IO)

    return _emit(
        {
            "status": "stored",
            "annotation_id": stored.annotation_id,
            "item_id": stored.item_id,
            "split": stored.split.value,
        }
    )


_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_REJECT = "none"
_CONFIRM = "confirm"


def _presentation_order(seed: str, item_id: str, size: int) -> tuple[int, ...]:
    """Where each candidate goes on the sheet.

    Shuffled, because a reviewer who learns that the proposal is always first
    is back to approving. Derived from the seed rather than drawn at random, so
    the sheet can be rebuilt from the record — which is also why a mistyped
    answer costs nothing: the same seed prints the same sheet again.
    """
    keyed = sorted(
        range(size),
        key=lambda index: hashlib.sha256(
            f"{seed}\x1f{item_id}\x1forder\x1f{index}".encode()
        ).digest(),
    )
    return tuple(keyed)


def _print_sheet(
    proposal: Proposal,
    candidates: tuple[tuple[Clause, str], ...],
    deep: bool,
) -> None:
    """Write the choice a human has to read, to a terminal and nowhere else.

    This is the one command that prints clause text. It has to — nobody can
    choose between four clauses without reading them. Nothing about which one
    was proposed appears here: a marked proposal is not a forced choice, it is
    a confirmation dialog.
    """
    print(f"item {proposal.item_id} · {proposal.split.value} · {proposal.document_id}")
    if deep:
        print("DEEP REVIEW — read the whole section, not only what is below.")
    print()
    print(f"Q. {proposal.question}")
    print()
    for letter, (clause, text) in zip(_LETTERS, candidates, strict=False):
        print(f"  [{letter}] §{clause.section_number} ¶{clause.ordinal}")
        print(f"      {text}")
        print()
    for point in proposal.key_points:
        print(f"  · {point.criterion}")
    if proposal.key_points:
        print()
    if candidates:
        offered = _LETTERS[: len(candidates)]
        print(f'Choose one of {offered}, or "{_REJECT}" to reject this item.')
    else:
        print(
            f'Type "{_CONFIRM}" if no clause in this document answers the '
            f'question, or "{_REJECT}" to reject the item.'
        )


def _read_choice(count: int) -> int | None | str:
    """Return the chosen index, None for a rejection, or a refusal code.

    One read, not a retry loop. The sheet is a pure function of the seed, so a
    reviewer who mistypes runs the command again and sees exactly the same
    thing — there is nothing to recover.
    """
    line = sys.stdin.readline().strip()
    if line.lower() == _REJECT:
        return None
    if count == 0:
        return 0 if line.lower() == _CONFIRM else "invalid_choice"
    if len(line) == 1 and line.upper() in _LETTERS[:count]:
        return _LETTERS.index(line.upper())
    return "invalid_choice"


def _annotation_review(arguments: argparse.Namespace) -> int:
    """Turn one drafted proposal into gold, or into a recorded rejection.

    The gap this closes: provenance v2 already records that a human reviewed a
    model proposal, and says nothing about what the review found. A reviewer
    who approves everything and one who catches real errors leave identical
    records, so "the author reviewed it" sits unverifiable underneath every
    downstream number — and gold is the ruler.

    A forced choice fixes that by construction. Someone who is not reading
    cannot score above chance, and disagreement becomes a number.

    Everything `annotation add` checks is still checked here. The chosen clause
    must exist in the named frozen document, a key point still may not restate
    its clause, and the overlap figure is computed rather than supplied.
    """
    try:
        data = json.loads(arguments.proposal.read_text(encoding="utf-8"))
    except OSError:
        return _refuse("io_error", EXIT_IO)
    except ValueError:
        return _refuse("invalid_proposal")
    if not isinstance(data, dict):
        return _refuse("invalid_proposal")
    try:
        proposal = proposal_for_schema(data.get("schema_version")).model_validate(data)
    except UnsupportedProposalSchemaError:
        return _refuse("unsupported_proposal_schema")
    except ValidationError:
        return _refuse("invalid_proposal")

    source = _frozen_source(arguments)
    if isinstance(source, str):
        return _refuse_source(source)
    manifest = source.manifest
    # Checked for every proposal, answerable or not. Confirming that nothing
    # answers a question is a claim about one specific document.
    if manifest.document_id != proposal.document_id:
        return _refuse("document_id_mismatch")
    if manifest.document_version != proposal.document_version:
        return _refuse("document_version_mismatch")

    try:
        texts = dict(
            iter_clause_texts(source.document, RfcLimits(), _clause_limits(manifest))
        )
    except UnsafeRfcError as error:
        return _refuse(error.code.value)
    except OversizedClauseError:
        return _refuse("clause_too_large")
    except OSError:
        return _refuse("io_error", EXIT_IO)
    clauses = tuple(texts)

    candidates: tuple[tuple[Clause, str], ...] = ()
    tiers: dict[str, int] = {}
    if proposal.proposed_gold_clause_id is not None:
        gold = next(
            (
                clause
                for clause in clauses
                if clause.clause_id == proposal.proposed_gold_clause_id
            ),
            None,
        )
        if gold is None:
            return _refuse("unknown_gold_clause")
        try:
            distractors = select_distractors(
                clauses,
                gold.clause_id,
                count=arguments.distractors,
                seed=arguments.seed,
            )
        except ValueError:
            return _refuse("invalid_distractor_count", EXIT_USAGE)
        for item in distractors:
            tiers[item.tier.value] = tiers.get(item.tier.value, 0) + 1
        pool = [gold, *(item.clause for item in distractors)]
        if len(pool) < 2:
            # The contract refuses this too. Refusing here means the reviewer is
            # not shown a choice that was never one.
            return _refuse("too_few_candidates")
        order = _presentation_order(arguments.seed, proposal.item_id, len(pool))
        candidates = tuple((pool[index], texts[pool[index]]) for index in order)

    deep = deep_review_required(
        proposal.item_id,
        rate=arguments.deep_review_rate,
        salt=arguments.deep_review_salt,
    )
    _print_sheet(proposal, candidates, deep)

    chosen = _read_choice(len(candidates))
    if isinstance(chosen, str):
        return _refuse(chosen)

    record: L1Annotation | None = None
    if chosen is not None and candidates:
        picked = candidates[chosen][0]
        record = _reviewed_record(proposal, picked, texts[picked])
    elif chosen is not None:
        record = _refusal_record(proposal)

    if record is not None:
        refusal = _check_gold_against_source(arguments, record)
        if refusal is not None:
            return _refuse_source(refusal)

    stored_id: str | None = None
    if record is not None:
        try:
            stored_id = AnnotationStore(arguments.annotation_dir).create(
                record
            ).annotation_id
        except UnsupportedAnnotationSchemaError:
            return _refuse("unsupported_annotation_schema")
        except ValueError:
            return _refuse("item_id_already_annotated")
        except (OSError, RuntimeError):
            return _refuse("annotation_not_written", EXIT_IO)

    outcome = _outcome(proposal, chosen, candidates)
    decision = ReviewDecision(
        reviewed_annotation_id=stored_id,
        item_id=proposal.item_id,
        outcome=outcome,
        candidates_shown=len(candidates),
        chose_proposal=outcome is ReviewOutcome.ACCEPTED_AS_PROPOSED,
        reviewer_id=arguments.reviewer,
        proposal_producer=proposal.proposal_producer,
        key_points_edited=proposal.key_points_edited,
        deep_reviewed=deep,
        unanswerable=proposal.expected_refusal,
    )
    try:
        review = ReviewStore(arguments.review_dir).create(decision)
    except (OSError, RuntimeError, ValueError):
        # The annotation is already stored. Task 5's progress report counts
        # records against reviews, so an annotation with no decision beside it
        # is visible rather than silently uncounted.
        return _refuse("review_not_written", EXIT_IO)

    return _emit(
        {
            "status": "stored" if stored_id else "rejected",
            "item_id": proposal.item_id,
            "outcome": decision.outcome.value,
            "annotation_id": stored_id,
            "review_id": review.review_id,
            "candidates_shown": decision.candidates_shown,
            "chose_proposal": decision.chose_proposal,
            "key_points_edited": decision.key_points_edited,
            "deep_reviewed": decision.deep_reviewed,
            "unanswerable": decision.unanswerable,
            "distractor_tiers": tiers,
            "seed": arguments.seed,
            "deep_review_rate": arguments.deep_review_rate,
            "deep_review_salt": arguments.deep_review_salt,
        }
    )


def _outcome(
    proposal: Proposal,
    chosen: int | None,
    candidates: tuple[tuple[Clause, str], ...],
) -> ReviewOutcome:
    if chosen is None:
        return ReviewOutcome.ITEM_REJECTED
    if not candidates:
        return ReviewOutcome.ACCEPTED_AS_PROPOSED
    picked = candidates[chosen][0].clause_id
    if picked == proposal.proposed_gold_clause_id:
        return ReviewOutcome.ACCEPTED_AS_PROPOSED
    return ReviewOutcome.GOLD_CHANGED


def _reviewed_record(
    proposal: Proposal, clause: Clause, text: str
) -> L1Annotation:
    """Build the record the review just decided on.

    `content_origin` is `model`, not `mixed`: the question is the drafter's
    wording throughout and the reviewer never edits it, only accepts or rejects
    the item that carries it. `label_origin` is `mixed`, because the gold and
    the key points are where the human's judgement actually lands. Overstating
    the human's share of the text would be the easiest thing to get wrong here
    and the hardest to notice later.
    """
    return L1Annotation(
        item_id=proposal.item_id,
        split=proposal.split,
        question=proposal.question,
        direction=proposal.direction,
        content_origin=AnnotationOrigin.MODEL,
        label_origin=AnnotationOrigin.MIXED,
        document_id=proposal.document_id,
        document_version=proposal.document_version,
        gold_clause_ids=(clause.clause_id,),
        gold_section_paths=(clause.section_path,),
        key_points=proposal.key_points,
        expected_refusal=False,
        question_gold_jaccard=round(
            question_gold_jaccard(proposal.question, [text]), 4
        ),
        gold_origins=(
            GoldOriginEvent(
                origin=GoldOrigin.MODEL_PROPOSAL,
                producer=proposal.proposal_producer,
            ),
            GoldOriginEvent(origin=GoldOrigin.HUMAN_SOURCE_REVIEW),
        ),
    )


def _refusal_record(proposal: Proposal) -> L1Annotation:
    """An item the reviewer confirmed no clause in the document answers."""
    return L1Annotation(
        item_id=proposal.item_id,
        split=proposal.split,
        question=proposal.question,
        direction=proposal.direction,
        content_origin=AnnotationOrigin.MODEL,
        label_origin=AnnotationOrigin.MIXED,
        document_id=proposal.document_id,
        document_version=proposal.document_version,
        key_points=proposal.key_points,
        expected_refusal=True,
    )


_DEEP_ANSWERS = {
    "complete": DeepReviewOutcome.GOLD_COMPLETE,
    "wrong": DeepReviewOutcome.GOLD_WRONG,
    "flawed": DeepReviewOutcome.QUESTION_FLAWED,
}


def _deep_review_scope(
    record: L1Annotation | L2Annotation,
    texts: dict[Clause, str],
) -> tuple[DeepReviewScope, tuple[Clause, ...]] | str:
    """What gets put in front of the reviewer, and why that is the right set.

    For an item with gold, the section it sits in. Everything the section says
    is on screen, so "I did not have it" is not available, and the failure a
    deep read exists to catch — a second clause that also answers — lives here
    far more often than anywhere else.

    For an unanswerable item there is no section to read, and the claim being
    checked is about the whole document. Literal search supplies the candidates.
    §8.2.1 forbids the retriever as a source of *initial* gold; this is §8.2.3's
    completeness audit, where pooling proposing candidates for a human to
    adjudicate is exactly the sanctioned use.
    """
    clauses = tuple(texts)
    if record.gold_clause_ids:
        gold = next(
            (c for c in clauses if c.clause_id == record.gold_clause_ids[0]), None
        )
        if gold is None:
            return "unknown_gold_clause"
        return DeepReviewScope.SECTION, tuple(
            c for c in clauses if c.section_anchor == gold.section_anchor
        )

    index = Bm25Index.build(
        [(clause.clause_id, text) for clause, text in texts.items()]
    )
    by_id = {clause.clause_id: clause for clause in clauses}
    hits = index.search(record.question, k=8)
    return DeepReviewScope.LITERAL_SEARCH, tuple(by_id[hit.unit_id] for hit in hits)


def _annotation_deep_review(arguments: argparse.Namespace) -> int:
    """Read the whole scope for one sampled item and record what it found.

    `ReviewDecision.deep_reviewed` records that the reviewer was told an item
    was sampled, and nothing about whether they then read anything. A pass with
    no deep reading in it reported complete coverage on exactly that basis. A
    finding is the deep read's output and cannot be produced without one.

    The command times itself. That is the cheapest signal separating a read
    from a keystroke — a thirteen-paragraph section closed in twelve seconds
    was not read — and it is measured here rather than asked for.
    """
    directory: Path = arguments.annotation_dir
    if not directory.is_dir():
        return _refuse("annotation_dir_not_found")
    try:
        records = {
            record.item_id: record
            for record in AnnotationStore(directory).iter_records()
            if record.annotation_id is not None
        }
    except UnsupportedAnnotationSchemaError:
        return _refuse("unsupported_annotation_schema")
    except ValueError:
        return _refuse("invalid_annotation_record")
    except OSError:
        return _refuse("io_error", EXIT_IO)

    record = records.get(arguments.item)
    if record is None or record.annotation_id is None:
        return _refuse("unknown_item")
    examined_annotation_id = record.annotation_id
    if not deep_review_required(
        record.item_id,
        rate=arguments.deep_review_rate,
        salt=arguments.deep_review_salt,
    ):
        # Deep-reviewing an unsampled item is choosing the sample, which is the
        # one thing pre-registration exists to prevent.
        return _refuse("item_not_sampled")

    source = _frozen_source(arguments)
    if isinstance(source, str):
        return _refuse_source(source)
    manifest = source.manifest
    if manifest.document_id != record.document_id:
        return _refuse("document_id_mismatch")
    if manifest.document_version != record.document_version:
        return _refuse("document_version_mismatch")

    try:
        texts = dict(
            iter_clause_texts(source.document, RfcLimits(), _clause_limits(manifest))
        )
    except UnsafeRfcError as error:
        return _refuse(error.code.value)
    except OversizedClauseError:
        return _refuse("clause_too_large")
    except OSError:
        return _refuse("io_error", EXIT_IO)

    resolved = _deep_review_scope(record, texts)
    if isinstance(resolved, str):
        return _refuse(resolved)
    scope, examined = resolved
    if not examined:
        return _refuse("empty_deep_review_scope")

    gold = set(record.gold_clause_ids)
    started = time.monotonic()
    print(f"item {record.item_id} · {record.document_id} · scope {scope.value}")
    print()
    print(f"Q. {record.question}")
    print()
    for letter, clause in zip(_LETTERS, examined, strict=False):
        mark = "GOLD" if clause.clause_id in gold else "    "
        print(f"  [{letter}] {mark} §{clause.section_number} ¶{clause.ordinal}")
        print(f"           {texts[clause]}")
        print()
    if record.expected_refusal:
        print(
            'This item claims no clause answers the question. Type "complete" to '
            'confirm that, "wrong" if one of the above does answer it, or '
            '"flawed" if the question cannot be answered as posed.'
        )
    else:
        print(
            "Name every further clause that is also needed to answer, as letters "
            '(e.g. "B,D"). Or "complete" if the gold above is the whole answer, '
            '"wrong" if it is not the answer at all, "flawed" if the question is.'
        )

    answered = _read_deep_answer(
        len(examined), allow_clauses=not record.expected_refusal
    )
    if isinstance(answered, str):
        return _refuse(answered)
    outcome, chosen = answered
    elapsed = int(time.monotonic() - started)

    added = tuple(
        examined[index].clause_id
        for index in chosen
        if examined[index].clause_id not in gold
    )
    if chosen and not added:
        # Every letter named was already gold, so nothing was found. Recording
        # `gold_extended` here would report work the finding did not do.
        return _refuse("no_new_clause_named")

    finding = DeepReviewFinding(
        reviewed_annotation_id=examined_annotation_id,
        item_id=record.item_id,
        outcome=DeepReviewOutcome.GOLD_EXTENDED if added else outcome,
        scope=scope,
        clauses_examined=len(examined),
        additional_gold_clause_ids=added,
        elapsed_seconds=elapsed,
        reviewer_id=arguments.reviewer,
    )
    try:
        # Stored before the amendment: the finding is the evidence, and if the
        # amendment fails it names exactly which clauses were still owed.
        stored = DeepReviewStore(arguments.deep_review_dir).create(finding)
    except (OSError, RuntimeError, ValueError):
        return _refuse("deep_review_not_written", EXIT_IO)

    amended_id: str | None = None
    if added:
        try:
            amended_id = (
                AnnotationStore(directory)
                .amend(
                    examined_annotation_id,
                    added_gold_clause_ids=added,
                    added_gold_section_paths=tuple(
                        clause.section_path
                        for clause in examined
                        if clause.clause_id in added
                    ),
                    added_gold_origins=(
                        GoldOriginEvent(origin=GoldOrigin.HUMAN_SOURCE_REVIEW),
                    ),
                    adjudication=f"deep review of the {scope.value} scope",
                )
                .annotation_id
            )
        except (OSError, RuntimeError, ValueError):
            return _refuse("annotation_not_amended", EXIT_IO)

    return _emit(
        {
            "status": "recorded",
            "item_id": record.item_id,
            "outcome": finding.outcome.value,
            "scope": scope.value,
            "clauses_examined": finding.clauses_examined,
            "additional_gold_clause_count": len(added),
            "elapsed_seconds": elapsed,
            "finding_id": stored.finding_id,
            "amended_annotation_id": amended_id,
        }
    )


def _pool_sources(
    arguments: argparse.Namespace,
) -> tuple[tuple[RfcSourceManifest, VerifiedRfc], ...] | str:
    manifests: list[str] = arguments.manifest
    xml_paths: list[Path] = arguments.xml
    if len(manifests) != len(xml_paths):
        return "source_pair_count_mismatch"
    sources: list[tuple[RfcSourceManifest, VerifiedRfc]] = []
    for manifest_id, xml_path in zip(manifests, xml_paths, strict=True):
        scoped = argparse.Namespace(
            **{
                **vars(arguments),
                "manifest": manifest_id,
                "xml": xml_path,
            }
        )
        source = _frozen_source(scoped)
        if isinstance(source, str):
            return source
        sources.append((source.manifest, source.document))
    return tuple(sources)


def _annotation_heads(directory: Path) -> tuple[L1Annotation, ...]:
    records = tuple(AnnotationStore(directory).iter_records())
    predecessors = {
        record.predecessor_annotation_id
        for record in records
        if record.predecessor_annotation_id is not None
    }
    heads = tuple(
        record
        for record in records
        if record.annotation_id not in predecessors
        and not isinstance(record, L2Annotation)
    )
    by_item: dict[str, L1Annotation] = {}
    for record in heads:
        if record.item_id in by_item:
            raise ValueError("one item owns more than one annotation head")
        by_item[record.item_id] = record
    return tuple(by_item[item_id] for item_id in sorted(by_item))


def _pool_corpus(
    sources: tuple[tuple[RfcSourceManifest, VerifiedRfc], ...],
) -> LocalCorpus:
    return LocalCorpus.load(
        [
            (document, _clause_limits(manifest))
            for manifest, document in sources
        ],
        RfcLimits(),
    )


def _retrieval_evaluate(arguments: argparse.Namespace) -> int:
    """Score the frozen retrieval protocol against one split's gold.

    Development-set diagnostics. §11 and §8.5 keep the locked splits unread
    until W6, so `--split` is required and printed back: a number whose split
    is implicit is one that ends up quoted as a test result.

    The protocol comes from the frozen corpus manifest, not from flags. Top-k
    per route, the fusion constant and the final cut-off were bound at freeze
    time precisely so an evaluation cannot quietly run a different retriever
    than the one the corpus was frozen with.
    """
    try:
        corpus_manifest = CorpusManifestStore(arguments.corpus_manifest_dir).read(
            arguments.corpus_manifest
        )
    except (OSError, ValueError, RuntimeError):
        return _refuse("corpus_manifest_not_found")

    resolved = _pool_sources(arguments)
    if isinstance(resolved, str):
        return _refuse_source(resolved)
    if {manifest.manifest_id for manifest, _ in resolved} != set(
        corpus_manifest.source_manifest_ids
    ):
        # Scoring against a different set of documents than the corpus was
        # frozen over would report retrieval over a corpus that never existed.
        return _refuse("corpus_source_mismatch")

    try:
        heads = _annotation_heads(arguments.annotation_dir)
        corpus = _pool_corpus(resolved)
    except (OSError, ValueError):
        return _refuse("invalid_annotation_record")
    except (UnsafeRfcError, OversizedClauseError):
        return _refuse("invalid_corpus")

    scored_heads = tuple(
        record
        for record in heads
        if record.split.value == arguments.split
        and not record.expected_refusal
        and record.gold_clause_ids
    )
    if not scored_heads:
        return _refuse("no_scorable_annotations")

    if weights_sha256(arguments.model_dir) != corpus_manifest.embedding_weights_sha256:
        return _refuse("embedding_weights_mismatch")
    try:
        encoder = load_encoder(arguments.model_dir, arguments.device)
    except EmbeddingRuntimeUnavailable:
        return _refuse("embedding_runtime_unavailable")

    protocol = corpus_manifest.retrieval
    dense: DenseIndex | None = None
    # Catches the search too, not only the open. The backend can go away
    # between the two, and a handler that lets that escape prints a traceback
    # where every other failure in this CLI prints one stable code.
    try:
        try:
            dense = DenseIndex.open(
                arguments.qdrant_url, corpus_manifest.collection_name
            )
            if dense.point_count() != corpus_manifest.point_count:
                return _refuse("dense_point_count_mismatch")
        except EgressPolicyViolation:
            raise
        except Exception:
            return _refuse("dense_index_unavailable", EXIT_IO)

        sparse = Bm25Index.build(corpus.indexable())
        if sparse.fingerprint != corpus_manifest.bm25.index_fingerprint:
            return _refuse("bm25_fingerprint_mismatch")

        locators = {
            unit_id: locator_for_unit(
                corpus_manifest.manifest_id, corpus.get_clause(unit_id)
            )
            for unit_id in corpus.unit_ids()
        }
        rankings: dict[str, list[RetrievedItem]] = {
            "bm25": [],
            "dense": [],
            "rrf": [],
        }
        for record in scored_heads:
            vector = encoder([record.question])[0].tolist()
            bm25_ids = tuple(
                hit.unit_id
                for hit in sparse.search(record.question, protocol.bm25_top_k)
            )
            dense_ids = tuple(
                hit.unit_id for hit in dense.search(vector, protocol.dense_top_k)
            )
            fused = reciprocal_rank_fusion(
                [
                    RouteRanking(route="bm25", unit_ids=bm25_ids),
                    RouteRanking(route="dense", unit_ids=dense_ids),
                ],
                locators=locators,
                parameters=RrfParameters(k=protocol.rrf_k),
            )
            for route, ranked in (
                ("bm25", bm25_ids),
                ("dense", dense_ids),
                ("rrf", tuple(hit.unit_id for hit in fused.hits)),
            ):
                rankings[route].append(
                    RetrievedItem(
                        item_id=record.item_id,
                        gold_unit_ids=tuple(record.gold_clause_ids),
                        ranked_unit_ids=ranked,
                        question_gold_jaccard=record.question_gold_jaccard or 0.0,
                    )
                )
    except DenseBackendUnavailable:
        return _refuse("dense_index_unavailable", EXIT_IO)
    finally:
        if dense is not None:
            dense.close()

    cut = protocol.final_top_k
    routes: dict[str, Any] = {}
    for route, items in rankings.items():
        metrics = score_route(route, items, k=cut)
        strata = stratify_by_overlap(route, items, k=cut)
        routes[route] = {
            **metrics.payload(),
            "overlap_strata": strata.payload() if strata else None,
        }

    return _emit(
        {
            "status": "scored",
            # Printed, never inferred. §8.5 forbids reading a locked split
            # before W6, and a result without its split named is one that gets
            # quoted as a test number.
            "split": arguments.split,
            "diagnostic_only": arguments.split == "dev",
            "corpus_manifest_id": corpus_manifest.manifest_id,
            "collection_name": corpus_manifest.collection_name,
            "bm25_index_fingerprint": corpus_manifest.bm25.index_fingerprint,
            "embedding_weights_sha256": corpus_manifest.embedding_weights_sha256,
            "protocol": {
                "bm25_top_k": protocol.bm25_top_k,
                "dense_top_k": protocol.dense_top_k,
                "final_top_k": cut,
                "rrf_k": protocol.rrf_k,
            },
            "scored_item_count": len(scored_heads),
            "not_reported": {
                "ndcg_at_10": "§8.4 excludes it: binary single-annotator labels",
                "unanswerable_false_trigger_rate": (
                    "needs a frozen confidence threshold and the answer path"
                ),
                "cross_reference_expansion_hit_rate": (
                    "no annotation field marks which items require following one"
                ),
            },
            "routes": routes,
        }
    )


def _answer(arguments: argparse.Namespace) -> int:
    """Answer one question from the frozen corpus, or refuse.

    The whole vertical path in one command: retrieve under the frozen protocol,
    disclose bounded evidence through the ledger-backed gate, and verify what
    comes back against what this request actually sent.

    Evidence is scoped to a single document — `VersionMetadata` names one, and
    an excerpt set spanning two would be priced and cited under a version
    statement that covers only one of them. The document is the one the top hit
    belongs to, so a question whose answer genuinely spans both RFCs cannot be
    answered in a single call. That is a real limitation of this slice rather
    than a detail: it is recorded in the output as `scoped_document_id`.
    """
    return asyncio.run(_answer_async(arguments))


def _answer_outcome_projection(outcome: Any) -> dict[str, Any]:
    """Project provider failure before verifier verdict at the CLI boundary."""
    answer = outcome.verified
    if outcome.provider_error is not None:
        return {
            "status": "failed",
            "refusal_reason": None,
            "citation_faults": [],
            "provider_error": outcome.provider_error,
        }
    return {
        "status": answer.verdict.value,
        "refusal_reason": (
            answer.refusal_reason.value if answer.refusal_reason else None
        ),
        "citation_faults": list(answer.citation_faults),
        "provider_error": None,
    }


def _authorized_answer_endpoint(route_name: str, manifest: Any) -> Any:
    from specpilot.providers.http import LIVE_ROUTES

    endpoint = LIVE_ROUTES[route_name].endpoint
    binding = manifest.provider_route_binding
    if (
        binding is None
        or binding.provider_id != endpoint.provider_id
        or binding.use is not ProviderUse.ONLINE_MAIN
    ):
        raise EgressPolicyViolation(
            "route_unauthorized",
            "selected answer route is not authorized by the source manifest",
        )
    return endpoint


async def _answer_async(arguments: argparse.Namespace) -> int:
    from specpilot.answer.evidence import build_evidence_from_unit
    from specpilot.answer.run import run_answer
    from specpilot.egress.ledger import LedgerError
    from specpilot.egress.postgres import PostgresEgressLedger
    from specpilot.providers.http import (
        HttpChatAdapter,
        ProviderCredentialMissing,
        resolve_credential,
    )
    from specpilot.providers.transport import (
        NoAdapterForRoute,
        PolicyBoundTransport,
        TransportReplayError,
    )

    try:
        corpus_manifest = CorpusManifestStore(arguments.corpus_manifest_dir).read(
            arguments.corpus_manifest
        )
    except (OSError, ValueError, RuntimeError):
        return _refuse("corpus_manifest_not_found")

    resolved = _pool_sources(arguments)
    if isinstance(resolved, str):
        return _refuse_source(resolved)
    store = ManifestStore(arguments.manifest_dir)
    try:
        authorized = store.read_source(arguments.source_manifest)
    except (OSError, ValueError, RuntimeError):
        return _refuse("manifest_not_found")
    try:
        corpus = _pool_corpus(resolved)
    except (UnsafeRfcError, OversizedClauseError, OSError):
        return _refuse("invalid_corpus")

    if weights_sha256(arguments.model_dir) != corpus_manifest.embedding_weights_sha256:
        return _refuse("embedding_weights_mismatch")
    try:
        encoder = load_encoder(arguments.model_dir, arguments.device)
    except EmbeddingRuntimeUnavailable:
        return _refuse("embedding_runtime_unavailable")

    protocol = corpus_manifest.retrieval
    dense: DenseIndex | None = None
    # Catches the search too, not only the open. The backend can go away
    # between the two, and a handler that lets that escape prints a traceback
    # where every other failure in this CLI prints one stable code.
    try:
        try:
            dense = DenseIndex.open(
                arguments.qdrant_url, corpus_manifest.collection_name
            )
        except Exception:
            return _refuse("dense_index_unavailable", EXIT_IO)
        sparse = Bm25Index.build(corpus.indexable())
        if sparse.fingerprint != corpus_manifest.bm25.index_fingerprint:
            return _refuse("bm25_fingerprint_mismatch")

        locators = {
            unit_id: locator_for_unit(
                corpus_manifest.manifest_id, corpus.get_clause(unit_id)
            )
            for unit_id in corpus.unit_ids()
        }
        vector = encoder([arguments.question])[0].tolist()
        fused = reciprocal_rank_fusion(
            [
                RouteRanking(
                    route="bm25",
                    unit_ids=tuple(
                        hit.unit_id
                        for hit in sparse.search(
                            arguments.question, protocol.bm25_top_k
                        )
                    ),
                ),
                RouteRanking(
                    route="dense",
                    unit_ids=tuple(
                        hit.unit_id
                        for hit in dense.search(vector, protocol.dense_top_k)
                    ),
                ),
            ],
            locators=locators,
            parameters=RrfParameters(k=protocol.rrf_k),
        )
    except DenseBackendUnavailable:
        return _refuse("dense_index_unavailable", EXIT_IO)
    finally:
        if dense is not None:
            dense.close()

    ranked = [corpus.get_clause(hit.unit_id) for hit in fused.hits][
        : protocol.final_top_k
    ]
    if not ranked:
        return _emit({"status": "refused", "refusal_reason": "no_evidence_retrieved"})
    scoped = ranked[0].document_id
    if authorized.document_id != scoped:
        return _refuse("source_manifest_document_mismatch")
    try:
        evidence = tuple(
            build_evidence_from_unit(
                unit, corpus_manifest_id=corpus_manifest.manifest_id
            )
            for unit in ranked
            if unit.document_id == scoped
        )
    except ValueError:
        return _refuse("invalid_evidence_set")

    try:
        endpoint = _authorized_answer_endpoint(arguments.route, authorized)
    except EgressPolicyViolation as violation:
        return _refuse(f"blocked:{violation.code}")

    enforcer = EgressPolicyEnforcer(EgressPolicy.load(), manifests=store)
    ledger = PostgresEgressLedger(
        arguments.ledger_dsn, policy=EgressPolicy.load(), manifests=store
    )
    try:
        key = resolve_credential(endpoint)
    except ProviderCredentialMissing:
        return _refuse("provider_credential_missing", EXIT_USAGE)
    adapter = HttpChatAdapter(endpoint, api_key=key)
    transport = PolicyBoundTransport(
        enforcer=enforcer,
        ledger=ledger,
        adapters=(cast(Any, adapter),),
    )
    try:
        outcome = await run_answer(
            arguments.question,
            evidence,
            transport=transport,
            model_id=endpoint.model_id,
            source_manifest=authorized,
            corpus_manifest_id=corpus_manifest.manifest_id,
            evaluation_root_id=arguments.evaluation_root_id,
            run_id=arguments.run_id,
        )
    except EgressPolicyViolation as violation:
        return _refuse(f"blocked:{violation.code}")
    except TransportReplayError as error:
        return _refuse(f"failed:{error.code}", EXIT_IO)
    except NoAdapterForRoute as error:
        return _refuse(f"failed:{error.code}", EXIT_IO)
    except LedgerError as error:
        return _refuse(f"blocked:{error.code}", EXIT_IO)
    finally:
        await adapter.aclose()

    answer = outcome.verified
    projection = _answer_outcome_projection(outcome)
    return _emit(
        {
            "status": projection["status"],
            "answer": answer.answer,
            "citations": [
                {
                    "clause_id": citation.clause_id,
                    "section_number": citation.section_number,
                    "document_id": citation.document_id,
                    "document_version": citation.document_version,
                    "content_hash": citation.content_hash,
                    "corpus_manifest_id": citation.corpus_manifest_id,
                }
                for citation in answer.citations
            ],
            "refusal_reason": projection["refusal_reason"],
            "citation_faults": projection["citation_faults"],
            "scoped_document_id": scoped,
            "evidence_count": len(evidence),
            "retrieved_clause_ids": [unit.unit_id for unit in ranked],
            "reservation_id": outcome.reservation_id,
            # The measured request, not the enforcer's content figure. They are
            # different numbers — 2,432 against 1,144 on a real call — and only
            # the second is what a cap binds, so reporting either under the
            # other's name makes the ledger look inconsistent with the output.
            "request_bytes": (
                outcome.request_size.request_bytes if outcome.request_size else None
            ),
            "provider_error": projection["provider_error"],
            "source_manifest_id": authorized.manifest_id,
            "corpus_manifest_id": corpus_manifest.manifest_id,
        }
    )


def _annotation_pool_register(arguments: argparse.Namespace) -> int:
    if not arguments.annotation_dir.is_dir():
        return _refuse("annotation_dir_not_found")
    resolved = _pool_sources(arguments)
    if isinstance(resolved, str):
        return _refuse_source(resolved)
    try:
        heads = _annotation_heads(arguments.annotation_dir)
        if not heads:
            return _refuse("no_l1_annotations")
        corpus = _pool_corpus(resolved)
    except (OSError, ValueError):
        return _refuse("invalid_annotation_record")
    except (UnsafeRfcError, OversizedClauseError):
        return _refuse("invalid_corpus")

    actual_weights = weights_sha256(arguments.model_dir)
    if actual_weights != arguments.weights_sha256:
        return _refuse("embedding_weights_mismatch")
    try:
        encoder = load_encoder(arguments.model_dir, arguments.device)
    except EmbeddingRuntimeUnavailable:
        return _refuse("embedding_runtime_unavailable")

    dense: DenseIndex | None = None
    try:
        try:
            dense = DenseIndex.open(
                arguments.qdrant_url,
                arguments.collection,
            )
            if dense.vector_size() != 1024:
                return _refuse("dense_vector_size_mismatch")
            if dense.point_count() != corpus.unit_count():
                return _refuse("dense_point_count_mismatch")
            dense_unit_ids = dense.unit_ids()
            if dense_unit_ids != frozenset(corpus.unit_ids()):
                return _refuse("dense_point_inventory_mismatch")
        except Exception:
            return _refuse("dense_index_unavailable", EXIT_IO)

        units = {
            unit_id: corpus.get_clause(unit_id) for unit_id in corpus.unit_ids()
        }
        facts = {
            unit_id: PoolingUnitFact(
                unit_id=unit.unit_id,
                document_id=unit.document_id,
                document_version=unit.document_version,
                section_number=unit.section_number,
                section_path=unit.section_path,
                content_sha256=hashlib.sha256(
                    unit.text.encode("utf-8")
                ).hexdigest(),
            )
            for unit_id, unit in units.items()
        }
        sparse = Bm25Index.build(corpus.indexable())
        items: list[PoolingItem] = []
        try:
            for record in heads:
                encoded = encoder([record.question])
                row = encoded[0]
                vector: list[float] = row.tolist()
                bm25 = RouteRanking(
                    route="bm25",
                    unit_ids=tuple(
                        hit.unit_id for hit in sparse.search(record.question, 5)
                    ),
                )
                dense_ranking = RouteRanking(
                    route="dense",
                    unit_ids=tuple(hit.unit_id for hit in dense.search(vector, 5)),
                )
                items.append(
                    PoolingItem(
                        item_id=record.item_id,
                        annotation_id=cast(str, record.annotation_id),
                        candidates=build_pool(bm25, dense_ranking, units=facts),
                    )
                )
            run = PoolingStore(arguments.pool_dir).create_run(
                PoolingRun(
                    source_manifest_ids=tuple(
                        manifest.manifest_id for manifest, _ in resolved
                    ),
                    bm25_fingerprint=sparse.fingerprint,
                    dense_collection=arguments.collection,
                    dense_inventory_sha256=inventory_sha256(
                        tuple(dense_unit_ids)
                    ),
                    embedding_weights_sha256=actual_weights,
                    vector_size=dense.vector_size(),
                    point_count=dense.point_count(),
                    items=tuple(items),
                    author_id=arguments.author_id,
                    created_at=arguments.created_at,
                )
            )
        except (OSError, RuntimeError, TypeError, ValueError):
            return _refuse("pooling_run_not_registered", EXIT_IO)

        return _emit(
            {
                "status": "registered",
                "run_id": run.run_id,
                "item_count": len(run.items),
                "candidate_count": sum(len(item.candidates) for item in run.items),
                "bm25_fingerprint": run.bm25_fingerprint,
                "embedding_weights_sha256": run.embedding_weights_sha256,
            }
        )
    finally:
        if dense is not None:
            with suppress(Exception):
                dense.close()


def _print_pool_sheet(
    record: L1Annotation,
    candidates: tuple[PoolingCandidate, ...],
    unit_texts: dict[str, str],
) -> None:
    print(f"item {record.item_id} · {record.document_id}")
    print()
    print(f"Q. {record.question}")
    print()
    print("Current gold:")
    if record.gold_clause_ids:
        for unit_id in record.gold_clause_ids:
            print(f"  {unit_texts[unit_id]}")
    else:
        print("  (expected refusal; no gold clause)")
    print()
    print("Retrieved candidates:")
    for letter, candidate in zip(_LETTERS, candidates, strict=False):
        routes = ", ".join(
            f"{route}#{rank}" for route, rank in candidate.route_ranks.items()
        )
        print(f"  [{letter}] §{candidate.section_number} · {routes}")
        print(f"      {unit_texts[candidate.unit_id]}")
    print()
    print('Type "complete", candidate letters such as "A,C", or "blocked".')


def _pool_choice(count: int) -> tuple[PoolingOutcome, tuple[int, ...]] | str:
    line = sys.stdin.readline()
    if line == "":
        return "pooling_review_paused"
    value = line.strip().lower()
    if value == "complete":
        return PoolingOutcome.GOLD_COMPLETE, ()
    if value == "blocked":
        return PoolingOutcome.AUDIT_BLOCKED, ()
    letters = [part.strip().upper() for part in line.split(",") if part.strip()]
    if not letters or len(set(letters)) != len(letters):
        return "invalid_choice"
    if any(letter not in _LETTERS[:count] for letter in letters):
        return "invalid_choice"
    return PoolingOutcome.GOLD_EXTENDED, tuple(
        _LETTERS.index(letter) for letter in letters
    )


def _annotation_pool_review(arguments: argparse.Namespace) -> int:
    store = PoolingStore(arguments.pool_dir)
    try:
        run = store.read_run(arguments.run_id)
    except (OSError, ValueError):
        return _refuse("pooling_run_not_found")
    existing_seals = store.read_seals(arguments.run_id)
    if existing_seals:
        return _emit(
            {
                "status": "sealed",
                "run_id": run.run_id,
                "adjudicated_items": len(run.items),
                "seal_id": existing_seals[0].seal_id,
            }
        )

    resolved = _pool_sources(arguments)
    if isinstance(resolved, str):
        return _refuse_source(resolved)
    source_ids = tuple(manifest.manifest_id for manifest, _ in resolved)
    if source_ids != run.source_manifest_ids:
        return _refuse("pooling_source_mismatch")
    try:
        corpus = _pool_corpus(resolved)
        units = {unit_id: corpus.get_clause(unit_id) for unit_id in corpus.unit_ids()}
        unit_texts = {unit_id: unit.text for unit_id, unit in units.items()}
        heads = {
            item.item_id: item
            for item in _annotation_heads(arguments.annotation_dir)
        }
    except (OSError, RuntimeError, ValueError):
        return _refuse("pooling_corpus_unavailable", EXIT_IO)

    decisions = {
        head.item_id: head
        for head in head_decisions(
            store.read_decisions(arguments.run_id),
            store.read_supersessions(arguments.run_id),
        )
    }
    applications = {
        item.decision_id: item for item in store.read_applications(arguments.run_id)
    }
    for item in run.items:
        existing = decisions.get(item.item_id)
        if existing is not None and existing.decision_id in applications:
            continue
        # A blocked head is provisional and must be re-presented. Falling
        # through to `apply_decision` with it — which is what happened before —
        # raises "a blocked decision cannot be applied", so a single mistyped
        # `blocked` wedged the run on its first item with no way past it.
        superseded: PoolingDecision | None = None
        if existing is not None and existing.outcome is PoolingOutcome.AUDIT_BLOCKED:
            superseded = existing
            existing = None
        if existing is None:
            current = heads.get(item.item_id)
            if current is None or current.annotation_id != item.annotation_id:
                return _refuse("pooling_annotation_head_changed")
            for candidate in item.candidates:
                text = unit_texts.get(candidate.unit_id)
                if text is None:
                    return _refuse("pooling_candidate_missing")
                actual_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
                if actual_hash != candidate.content_sha256:
                    return _refuse("pooling_candidate_hash_mismatch")
            _print_pool_sheet(current, item.candidates, unit_texts)
            started = time.monotonic()
            chosen = _pool_choice(len(item.candidates))
            if isinstance(chosen, str):
                if chosen == "pooling_review_paused":
                    return _emit(
                        {
                            "status": "paused",
                            "run_id": run.run_id,
                            "adjudicated_items": len(applications),
                        }
                    )
                return _refuse(chosen)
            outcome, indexes = chosen
            existing = PoolingDecision(
                run_id=cast(str, run.run_id),
                item_id=item.item_id,
                reviewed_annotation_id=item.annotation_id,
                outcome=outcome,
                selected_unit_ids=tuple(
                    item.candidates[index].unit_id for index in indexes
                ),
                reviewer_id=arguments.reviewer,
                elapsed_seconds=int(time.monotonic() - started),
            )
            if superseded is not None:
                try:
                    existing = store.supersede_decision(
                        superseded, existing, reviewer_id=arguments.reviewer
                    )
                except ValueError:
                    return _refuse("pooling_supersession_refused")
            if outcome is PoolingOutcome.AUDIT_BLOCKED:
                if superseded is None:
                    store.create_decision(existing)
                return _refuse("pooling_audit_blocked")
        try:
            apply_decision(
                store,
                AnnotationStore(arguments.annotation_dir),
                run,
                existing,
                unit_texts=unit_texts,
            )
        except (OSError, RuntimeError, ValueError):
            return _refuse("pooling_decision_not_applied", EXIT_IO)
        decisions[item.item_id] = existing
        applications = {
            applied.decision_id: applied
            for applied in store.read_applications(arguments.run_id)
        }

    try:
        final_decisions = store.read_decisions(arguments.run_id)
        final_applications = store.read_applications(arguments.run_id)
        sealed = store.create_seal(
            seal_run(
                run,
                decisions=final_decisions,
                applications=final_applications,
                supersessions=store.read_supersessions(arguments.run_id),
            )
        )
    except (OSError, RuntimeError, ValueError):
        return _refuse("pooling_run_not_sealed", EXIT_IO)
    return _emit(
        {
            "status": "sealed",
            "run_id": run.run_id,
            "adjudicated_items": len(final_decisions),
            "seal_id": sealed.seal_id,
        }
    )


def _annotation_pool_status(arguments: argparse.Namespace) -> int:
    store = PoolingStore(arguments.pool_dir)
    try:
        run = store.read_run(arguments.run_id)
        decisions = store.read_decisions(arguments.run_id)
        applications = store.read_applications(arguments.run_id)
        seals = store.read_seals(arguments.run_id)
    except (OSError, ValueError):
        return _refuse("pooling_run_not_found")
    outcomes: dict[str, int] = {}
    for decision in decisions:
        outcomes[decision.outcome.value] = outcomes.get(decision.outcome.value, 0) + 1
    return _emit(
        {
            "status": "reported",
            "run_id": run.run_id,
            "registered_items": len(run.items),
            "adjudicated_items": len(applications),
            "outcomes": dict(sorted(outcomes.items())),
            "sealed": bool(seals),
            "seal_id": seals[0].seal_id if seals else None,
        }
    )


def _read_deep_answer(
    count: int, *, allow_clauses: bool
) -> tuple[DeepReviewOutcome, tuple[int, ...]] | str:
    line = sys.stdin.readline().strip()
    keyword = _DEEP_ANSWERS.get(line.lower())
    if keyword is not None:
        return keyword, ()
    if not allow_clauses:
        return "invalid_choice"
    letters = [part.strip().upper() for part in line.split(",") if part.strip()]
    if not letters or any(
        letter not in _LETTERS[:count] for letter in letters
    ):
        return "invalid_choice"
    if len(set(letters)) != len(letters):
        return "invalid_choice"
    return DeepReviewOutcome.GOLD_EXTENDED, tuple(
        _LETTERS.index(letter) for letter in letters
    )


class _FixtureCounter:
    provider_id = "fixture-provider"
    model_id = "fixture-model-v1"

    def count_tokens(self, text: str) -> int:
        return max(len(text.split()), 1)


def _retrieval_search(arguments: argparse.Namespace) -> int:
    """Search the local corpus and print locators, never text.

    Sparse only. The dense route needs the encoder and a running collection,
    which belong to the indexing path rather than to a command meant to be
    cheap; `--route` exists so adding dense later cannot silently change what
    this already prints.

    The candidate pool does not leave the machine — this writes locators and
    scores to stdout, and nothing here reaches the enforcer or a provider.
    """
    source = _frozen_source(arguments)
    if isinstance(source, str):
        return _refuse_source(source)
    manifest = source.manifest

    try:
        corpus = LocalCorpus.load(
            [(source.document, _clause_limits(manifest))], RfcLimits()
        )
    except UnsafeRfcError as error:
        return _refuse(error.code.value)
    except OversizedClauseError:
        return _refuse("clause_too_large")
    except OSError:
        return _refuse("io_error", EXIT_IO)

    index = Bm25Index.build(corpus.indexable())
    hits = index.search(arguments.query, k=arguments.k)
    return _emit(
        {
            "status": "searched",
            "route": arguments.route,
            "document_id": manifest.document_id,
            "index_fingerprint": index.fingerprint,
            "hit_count": len(hits),
            "hits": [
                {
                    "unit_id": hit.unit_id,
                    "score": round(hit.score, 4),
                    "kind": corpus.get_clause(hit.unit_id).kind,
                    "section_number": corpus.get_clause(hit.unit_id).section_number,
                    "section_path": corpus.get_clause(hit.unit_id).section_path,
                }
                for hit in hits
            ],
        }
    )


def _annotation_progress(arguments: argparse.Namespace) -> int:
    """Report how far gold annotation has got, without reporting any of it.

    Every record is re-verified against its own content ID on the way in, so a
    count is only ever reported over records that are still what they were when
    written. One tampered file refuses the whole report rather than quietly
    dropping a record and reporting a smaller number.
    """
    directory: Path = arguments.annotation_dir
    if not directory.is_dir():
        return _refuse("annotation_dir_not_found")

    gold_review = None
    review_dir: Path | None = arguments.review_dir
    if review_dir is not None:
        if arguments.deep_review_rate is None or arguments.deep_review_salt is None:
            # Coverage against an undeclared rate is a number about nothing, and
            # the sample is the only thing separating "the proposals were good"
            # from "the review was shallow".
            return _refuse("deep_review_sample_undeclared", EXIT_USAGE)
        if not review_dir.is_dir():
            # Not an empty report: zero reviews for a store that is not there
            # would read as zero reviews having happened.
            return _refuse("review_dir_not_found")
        findings_dir: Path | None = arguments.deep_review_dir
        if findings_dir is not None and not findings_dir.is_dir():
            return _refuse("deep_review_dir_not_found")
        try:
            gold_review = review_statistics(
                ReviewStore(review_dir).read_all(),
                DeepReviewStore(findings_dir).read_all() if findings_dir else (),
                rate=arguments.deep_review_rate,
                salt=arguments.deep_review_salt,
            )
        except ValueError:
            return _refuse("invalid_review_record")
        except OSError:
            return _refuse("io_error", EXIT_IO)

    pooling_audit = None
    pool_dir: Path | None = arguments.pool_dir
    if pool_dir is not None:
        store = PoolingStore(pool_dir)
        try:
            runs = store.read_runs()
            if not runs:
                return _refuse("pooling_run_missing")
            per_run: list[PoolingRunProgress] = []
            # Per item, not per decision: a later run re-registers everything an
            # earlier one covered, so decision counts would double-count them.
            registered_items: set[str] = set()
            audited_items: dict[str, PoolingOutcome] = {}
            added_gold: dict[str, int] = {}
            blocked_items: set[str] = set()
            for run in runs:
                run_id = cast(str, run.run_id)
                heads = head_decisions(
                    store.read_decisions(run_id),
                    store.read_supersessions(run_id),
                )
                applied = {
                    item.decision_id
                    for item in store.read_applications(run_id)
                }
                sealed = bool(store.read_seals(run_id))
                counts = {
                    outcome: sum(head.outcome.value == outcome for head in heads)
                    for outcome in (
                        PoolingOutcome.GOLD_COMPLETE.value,
                        PoolingOutcome.GOLD_EXTENDED.value,
                        PoolingOutcome.AUDIT_BLOCKED.value,
                    )
                }
                registered_items |= {item.item_id for item in run.items}
                for head in heads:
                    if head.outcome is PoolingOutcome.AUDIT_BLOCKED:
                        blocked_items.add(head.item_id)
                        continue
                    if sealed and head.decision_id in applied:
                        blocked_items.discard(head.item_id)
                        audited_items[head.item_id] = head.outcome
                        added_gold[head.item_id] = len(head.selected_unit_ids)
                per_run.append(
                    PoolingRunProgress(
                        run_id=run_id,
                        registered_items=len(run.items),
                        adjudicated_items=len(applied),
                        gold_complete=counts[PoolingOutcome.GOLD_COMPLETE.value],
                        gold_extended=counts[PoolingOutcome.GOLD_EXTENDED.value],
                        blocked=counts[PoolingOutcome.AUDIT_BLOCKED.value],
                        added_gold_clauses=sum(
                            len(head.selected_unit_ids)
                            for head in heads
                            if head.outcome is PoolingOutcome.GOLD_EXTENDED
                        ),
                        sealed=sealed,
                    )
                )
        except (OSError, ValueError):
            return _refuse("invalid_pooling_record")
        pooling_audit = PoolingAuditProgress(
            registered_items=len(registered_items),
            adjudicated_items=len(audited_items),
            gold_complete=sum(
                outcome is PoolingOutcome.GOLD_COMPLETE
                for outcome in audited_items.values()
            ),
            gold_extended=sum(
                outcome is PoolingOutcome.GOLD_EXTENDED
                for outcome in audited_items.values()
            ),
            blocked=len(blocked_items),
            added_gold_clauses=sum(added_gold.values()),
            fully_sealed=(
                len(audited_items) == len(registered_items)
                and all(entry.sealed for entry in per_run)
            ),
            runs=tuple(per_run),
        )

    try:
        report = read_progress(directory, gold_review, pooling_audit)
    except UnsupportedAnnotationSchemaError:
        return _refuse("unsupported_annotation_schema")
    except ValueError:
        return _refuse("invalid_annotation_record")
    except OSError:
        return _refuse("io_error", EXIT_IO)
    return _emit(report.payload())


def _embedding_measure(arguments: argparse.Namespace) -> int:
    """Measure encoding throughput on this machine, over the frozen corpus.

    Product plan section 7 forbids writing a full-corpus wall clock down before
    one has been measured, so the estimate this prints is derived here from an
    observed rate and the document's real clause count — never from a default.

    The sample is timed after a warm-up batch. A cold first batch on MPS pays
    for graph compilation and the initial host-to-device copy, which is a real
    cost but a one-off, and folding it into the rate would misstate every
    subsequent batch.
    """
    source = _frozen_source(arguments)
    if isinstance(source, str):
        return _refuse_source(source)
    manifest = source.manifest

    try:
        texts = tuple(
            text
            for _, text in iter_clause_texts(
                source.document, RfcLimits(), _clause_limits(manifest)
            )
        )
    except UnsafeRfcError as error:
        return _refuse(error.code.value)
    except OversizedClauseError:
        return _refuse("clause_too_large")
    except OSError:
        return _refuse("io_error", EXIT_IO)

    try:
        sample = evenly_spaced(texts, arguments.sample)
    except ValueError:
        return _refuse("empty_sample")

    try:
        digest = weights_sha256(arguments.model_dir)
        encode = load_encoder(arguments.model_dir, arguments.device)
        length_of = load_token_counter(arguments.model_dir)
    except EmbeddingRuntimeUnavailable:
        return _refuse("embedding_runtime_unavailable")
    except (OSError, ValueError):
        return _refuse("model_dir_unusable")

    encode(sample[: arguments.batch_size])
    measurement = measure_throughput(
        sample,
        encode,
        model_id=arguments.model_id,
        weights_sha256=digest,
        device=arguments.device,
        batch_order=arguments.batch_order,
        batch_size=arguments.batch_size,
        length_of=length_of,
    )

    return _emit(
        {
            "status": "measured",
            "document_id": manifest.document_id,
            "model_id": measurement.model_id,
            "weights_sha256": measurement.weights_sha256,
            "pipeline_version": measurement.pipeline_version,
            "device": measurement.device,
            "batch_order": measurement.batch_order.value,
            "length_metric": measurement.length_metric.value,
            "batch_size": measurement.batch_size,
            "sample_size": measurement.sample_size,
            "sample_words": measurement.sample_words,
            "corpus_words": sum(len(text.split()) for text in texts),
            "elapsed_seconds": round(measurement.elapsed_seconds, 3),
            "clauses_per_second": round(measurement.clauses_per_second, 2),
            "words_per_second": round(measurement.words_per_second, 1),
            "clause_count": len(texts),
            "estimated_full_corpus_seconds": round(
                estimate_full_corpus_seconds(measurement, len(texts)), 1
            ),
        }
    )


def _fixture_excerpt(index: int, tokens: int, byte_count: int) -> EvidenceExcerpt:
    import hashlib

    filler = byte_count - (tokens - 1)
    quote = " ".join(["x" * (filler - (tokens - 1)), *("x" for _ in range(tokens - 1))])
    return EvidenceExcerpt(
        corpus_manifest_id=_FIXTURE_CORPUS_ID,
        content_hash=f"{index % 16:x}" * 64,
        quote=quote,
        quote_hash=hashlib.sha256(quote.encode("utf-8")).hexdigest(),
        span=NormalizedExcerptSpan(
            paragraph_start=index,
            paragraph_end=index,
            token_start=index * 600,
            token_end=index * 600 + tokens,
        ),
    )


def _envelope_smoke(arguments: argparse.Namespace) -> int:
    """Exercise the maximum legal L2 envelope, then one step past each boundary.

    Synthetic text only. This proves the gate arithmetic, and deliberately
    reports no quality figure of any kind.
    """
    del arguments
    from specpilot.egress.policy import EgressPolicy as _Policy

    policy = _Policy.load()
    store, manifest = _fixture_manifest()
    enforcer = EgressPolicyEnforcer(
        policy,
        manifests=store,
        clock=lambda: datetime(2026, 8, 6, 4, tzinfo=UTC),
    )
    version = VersionMetadata(
        source_manifest_id=manifest.manifest_id,
        corpus_manifest_id=_FIXTURE_CORPUS_ID,
        document_id=manifest.document_id,
        document_version=manifest.document_version,
    )

    def reserve(payload: Any, stage: EgressStage) -> Any:
        return enforcer.prepare(
            EgressRequest(
                evaluation_root_id="envelope-smoke",
                run_id="run-1",
                task_level=TaskLevel.L2,
                version=version,
                stage=stage,
                route=manifest.provider_route_binding,
                model_id="fixture-model-v1",
                source_manifest=manifest,
                payload=payload,
            ),
            _FixtureCounter(),
        )

    def claim(index: int, excerpts: tuple[EvidenceExcerpt, ...], toc: int) -> Any:
        return L2AtomicClaimPayload(
            atomic_claim_id=f"claim-{index}",
            atomic_claim=f"Atomic claim {index}",
            version=version,
            toc_nodes=tuple(
                TocNode(node_id=f"claim-{index}-toc-{n}", title=f"Section {n}")
                for n in range(toc)
            ),
            evidence_excerpts=excerpts,
        )

    stages = (
        EgressStage.EVIDENCE,
        EgressStage.COMPLIANCE,
        EgressStage.VERIFIER,
        EgressStage.VERIFIER,
    )
    outcome: ReservationOutcome | None = None
    try:
        for claim_index in range(3):
            excerpts = tuple(
                _fixture_excerpt(claim_index * 4 + n + 1, 512, 8192) for n in range(4)
            )
            payload = claim(claim_index, excerpts, toc=2)
            for stage in stages:
                outcome = enforcer.apply_reservation(
                    outcome.usage if outcome else None,
                    outcome.corpus_usage if outcome else None,
                    reserve(payload, stage),
                    _FixtureCounter(),
                )
    except EgressPolicyViolation as error:
        return _refuse(f"envelope_rejected:{error.code}")

    judge_store, judge_manifest = _fixture_manifest(
        use=ProviderUse.OFFLINE_JUDGE,
        endpoint_purpose="fixture-judge",
    )
    judge_enforcer = EgressPolicyEnforcer(
        policy,
        manifests=judge_store,
        clock=lambda: datetime(2026, 8, 6, 4, tzinfo=UTC),
    )
    judge_version = VersionMetadata(
        source_manifest_id=judge_manifest.manifest_id,
        corpus_manifest_id=_FIXTURE_CORPUS_ID,
        document_id=judge_manifest.document_id,
        document_version=judge_manifest.document_version,
    )
    judge_request = EgressRequest(
        evaluation_root_id="envelope-smoke",
        run_id="judge-run",
        task_level=TaskLevel.L2,
        version=judge_version,
        stage=EgressStage.JUDGE,
        route=judge_manifest.provider_route_binding,
        model_id="fixture-model-v1",
        source_manifest=judge_manifest,
        payload=JudgePayload(
            final_answer="Synthetic fixture answer.",
            scoring_points=(ScoringPoint(point_id="p1", text="Fixture point"),),
            gold_excerpts=tuple(_fixture_excerpt(100 + n, 512, 8192) for n in range(5)),
        ),
    )
    try:
        judge_reservation = judge_enforcer.prepare(judge_request, _FixtureCounter())
        for _ in range(2):
            outcome = judge_enforcer.apply_reservation(
                outcome.usage if outcome else None,
                outcome.corpus_usage if outcome else None,
                judge_reservation,
                _FixtureCounter(),
            )
    except EgressPolicyViolation as error:
        return _refuse(f"envelope_rejected:{error.code}")

    assert outcome is not None
    refusals: dict[str, str] = {}
    probes = (
        ("one_more_excerpt", claim(0, (_fixture_excerpt(90, 512, 8192),), 0)),
        ("one_more_toc_node", claim(0, (), 1)),
        (
            "one_more_token_in_an_excerpt",
            claim(0, (_fixture_excerpt(91, 513, 8194),), 0),
        ),
        (
            "one_more_byte_in_an_excerpt",
            claim(0, (_fixture_excerpt(92, 512, 8193),), 0),
        ),
    )
    for name, probe in probes:
        try:
            enforcer.apply_reservation(
                outcome.usage,
                outcome.corpus_usage,
                reserve(probe, EgressStage.EVIDENCE),
                _FixtureCounter(),
            )
        except EgressPolicyViolation as error:
            refusals[name] = error.code
        else:
            return _refuse(f"boundary_not_enforced:{name}")

    return _emit(
        {
            "status": "passed",
            "unique_disclosures": len(outcome.usage.disclosures),
            "root_transmitted_tokens": outcome.usage.root_transmitted_tokens,
            "root_transmitted_bytes": outcome.usage.root_transmitted_bytes,
            "corpus_unique_disclosures": len(outcome.corpus_usage.disclosure_ids),
            "refusals": refusals,
        }
    )


def _fixture_manifest(
    *,
    use: ProviderUse = ProviderUse.ONLINE_MAIN,
    endpoint_purpose: str = "fixture-smoke",
    provider_id: str = "fixture-provider",
) -> tuple[ManifestStore, Any]:
    """An authorized manifest bound to one synthetic route, for smoke runs.

    `provider_id` may name a real provider, and for `--live` it does. What it
    can never name is a real document: this manifest is bound to
    `synthetic-fixture-spec`, so the authorization it carries covers the
    generated fixture corpus and nothing else. Reaching real source text needs
    a manifest for `ietf-rfc-9110` or `ietf-rfc-9112`, which no command here
    creates and which only a completed assessment produces.
    """
    import hashlib
    import tempfile

    directory = Path(tempfile.mkdtemp(prefix="specpilot-smoke-manifests-")).resolve()
    store = ManifestStore(directory)
    premise = "Only bounded evidence excerpts may leave the local trust boundary."
    initial = store.create_source(
        SourceManifestDraft.model_validate(
            {
                "document_id": "synthetic-fixture-spec",
                "document_version": "fixture-edition",
                "download_url": "https://fixtures.invalid/synthetic.zip",
                "archive_sha256": "1" * 64,
                "docx_sha256": "2" * 64,
                "downloaded_at": "2026-08-06T01:00:00Z",
                "created_at": "2026-08-06T01:31:00Z",
            }
        )
    )
    binding = ProviderRouteBinding(
        provider_id=provider_id,
        endpoint_purpose=endpoint_purpose,
        use=use,
    )
    assessment = ComplianceAssessment.model_validate(
        {
            "source_terms": {
                "terms_snapshot": {
                    "snapshot_url": "https://fixtures.invalid/source-terms",
                    "snapshot_sha256": "3" * 64,
                    "captured_at": "2026-08-06T01:00:00Z",
                },
                "summary": "Synthetic fixture terms for smoke runs only.",
                "uncertainty": ("This corpus is generated, not licensed material.",),
            },
            "provider_policy": {
                "policy_snapshot": {
                    "snapshot_url": "https://fixtures.invalid/provider-policy",
                    "snapshot_sha256": "4" * 64,
                    "captured_at": "2026-08-06T01:00:00Z",
                },
                "retention_summary": "Fixture route retains nothing.",
                "training_summary": "Fixture route trains on nothing.",
                "region_summary": "Fixture route is local.",
                "subprocessor_summary": "Fixture route has no subprocessor.",
                "uncertainty": ("Applies to the fixture route only.",),
            },
            "outbound_limit": {
                "premise": premise,
                "premise_sha256": hashlib.sha256(premise.encode("utf-8")).hexdigest(),
            },
            "author_conclusion": {
                "authorized": True,
                "authorization_statement": (
                    "Synthetic fixture route: no real source text can reach it."
                ),
                "author_id": "fixture-smoke",
                "provider_id": binding.provider_id,
                "endpoint_purpose": binding.endpoint_purpose,
                "authored_at": "2026-08-06T02:00:00Z",
                "expires_at": "2036-08-06T02:00:00Z",
            },
        }
    )
    manifest = store.create_successor(
        initial,
        assessment=assessment,
        route_binding=binding,
        created_at=datetime(2026, 8, 6, 3, tzinfo=UTC),
    )
    return store, manifest


def _live_findings(response: Any) -> str:
    """What this run showed, assembled from this run.

    The earlier version was one fixed sentence claiming a tool call, which was
    false the moment a route answered without emitting one -- and one did. A
    smoke reporting the same finding whatever happened is not evidence.
    """
    findings = [
        "the named model slug answered on this route",
        "usage metadata was returned",
    ]
    if response.metadata.tool_call_count:
        findings.append("a tool call was emitted")
    else:
        findings.append(
            "NO tool call was emitted; tool calling on this route is unproven "
            "rather than refuted, since a model may decline a tool it is offered"
        )
    return "; ".join(findings)


def _route_smoke(arguments: argparse.Namespace) -> int:
    """Send synthetic fixture text through the real transport and ledger.

    This proves the send path is wired and policy-bound. It does not prove any
    real provider works: that needs credentials and a route, and their absence
    is reported as blocked rather than passed.
    """
    if arguments.ledger_dsn is None:
        return _refuse("ledger_not_configured")
    return asyncio.run(_route_smoke_async(arguments))


def _egress_rebind_policy(arguments: argparse.Namespace) -> int:
    return asyncio.run(_egress_rebind_policy_async(arguments))


async def _egress_rebind_policy_async(arguments: argparse.Namespace) -> int:
    from specpilot.egress.ledger import (
        LedgerIntegrityError,
        LedgerUnavailable,
        PolicyRebindAmbiguous,
        PolicyRebindConflict,
    )
    from specpilot.egress.postgres import PostgresEgressLedger

    try:
        policy = EgressPolicy.load(arguments.policy)
    except OSError:
        return _refuse("egress_policy_unavailable", EXIT_IO)
    except (ValidationError, ValueError):
        return _refuse("invalid_egress_policy", EXIT_USAGE)
    ledger = PostgresEgressLedger(
        arguments.ledger_dsn,
        policy=policy,
        manifests=ManifestStore(arguments.manifest_dir),
    )
    try:
        result = await ledger.rebind_policy(
            arguments.corpus_manifest_id,
            expected_ledger_id=arguments.expected_ledger_id,
            expected_policy_hash=arguments.expected_policy_hash,
        )
    except PolicyRebindConflict as error:
        return _refuse(error.code, EXIT_REFUSED)
    except (PolicyRebindAmbiguous, LedgerIntegrityError, LedgerUnavailable) as error:
        return _refuse(error.code, EXIT_IO)
    return _emit(
        {
            "status": "rebound" if result.rebound else "unchanged",
            "corpus_manifest_id": result.corpus_manifest_id,
            "predecessor_ledger_id": result.predecessor_ledger_id,
            "successor_ledger_id": result.successor_ledger_id,
            "old_policy_hash": result.old_policy_hash,
            "new_policy_hash": result.new_policy_hash,
            "inherited_unique_excerpts": result.inherited_unique_excerpts,
            "inherited_unique_tokens": result.inherited_unique_tokens,
            "inherited_unique_bytes": result.inherited_unique_bytes,
        }
    )


async def _route_smoke_async(arguments: argparse.Namespace) -> int:
    from specpilot.egress.ledger import LedgerError
    from specpilot.egress.postgres import PostgresEgressLedger
    from specpilot.providers.fake import FakeProvider
    from specpilot.providers.transport import (
        NoAdapterForRoute,
        PolicyBoundTransport,
        ProviderAttemptError,
        TransportReplayError,
    )

    # --route must actually change the route. A judge smoke that quietly
    # exercises the online chain is false evidence for the go/no-go checklist.
    judging = arguments.route == "judge"
    live = bool(getattr(arguments, "live", False))

    provider: Any
    if live:
        from specpilot.providers.http import (
            LIVE_ROUTES,
            HttpChatAdapter,
            ProviderCredentialMissing,
            resolve_credential,
        )

        endpoint = LIVE_ROUTES[arguments.route].endpoint
        try:
            api_key = resolve_credential(endpoint)
        except ProviderCredentialMissing:
            return _refuse(f"credential_missing:{endpoint.api_key_env}")
        store, manifest = _fixture_manifest(
            use=ProviderUse.OFFLINE_JUDGE if judging else ProviderUse.ONLINE_MAIN,
            endpoint_purpose="live-judge" if judging else "live-main",
            provider_id=endpoint.provider_id,
        )
        provider = HttpChatAdapter(endpoint, api_key=api_key, probe_tools=True)
        model_id = endpoint.model_id
    else:
        store, manifest = _fixture_manifest(
            use=ProviderUse.OFFLINE_JUDGE if judging else ProviderUse.ONLINE_MAIN,
            endpoint_purpose="fixture-judge" if judging else "fixture-smoke",
        )
        provider = FakeProvider(
            provider_id="fixture-provider",
            model_id="fixture-model-v1",
        )
        model_id = "fixture-model-v1"
    transport = PolicyBoundTransport(
        enforcer=EgressPolicyEnforcer(
            EgressPolicy.load(),
            manifests=store,
            clock=lambda: datetime(2026, 8, 6, 4, tzinfo=UTC),
        ),
        ledger=PostgresEgressLedger(
            arguments.ledger_dsn,
            policy=EgressPolicy.load(),
            manifests=store,
            clock=lambda: datetime(2026, 8, 6, 4, tzinfo=UTC),
        ),
        adapters=(provider,),
    )
    version = VersionMetadata(
        source_manifest_id=manifest.manifest_id,
        corpus_manifest_id=_FIXTURE_CORPUS_ID,
        document_id=manifest.document_id,
        document_version=manifest.document_version,
    )
    payload: Any
    if judging:
        payload = JudgePayload(
            final_answer="Synthetic fixture answer.",
            scoring_points=(ScoringPoint(point_id="p1", text="Fixture point"),),
            gold_excerpts=(_fixture_excerpt(1, 4, 16),),
        )
    else:
        payload = L1OnlinePayload(
            query="Synthetic fixture probe.",
            version=version,
            evidence_excerpts=(_fixture_excerpt(1, 4, 16),),
        )
    request = EgressRequest(
        evaluation_root_id=f"route-smoke-{arguments.route}",
        run_id="run-1",
        task_level=TaskLevel.L1,
        version=version,
        stage=EgressStage.JUDGE if judging else EgressStage.EVIDENCE,
        route=manifest.provider_route_binding,
        model_id=model_id,
        source_manifest=manifest,
        payload=payload,
    )
    projected_tokens = sum(
        provider.token_counter.count_tokens(item.quote)
        for item in (
            payload.gold_excerpts if judging else payload.evidence_excerpts
        )
    )
    try:
        receipt = await transport.send(request, idempotency_key="route-smoke-1")
    except EgressPolicyViolation as error:
        return _refuse(f"blocked:{error.code}")
    except TransportReplayError as error:
        return _refuse(f"failed:{error.code}", EXIT_IO)
    except NoAdapterForRoute as error:
        return _refuse(f"failed:{error.code}", EXIT_IO)
    except LedgerError as error:
        return _refuse(f"blocked:{error.code}")
    except ProviderAttemptError as error:
        return _refuse(f"failed:{error.public_error_code}", EXIT_IO)
    finally:
        aclose = getattr(provider, "aclose", None)
        if aclose is not None:
            await aclose()

    return _emit(
        {
            "status": "passed",
            "route": arguments.route,
            "provider_use": manifest.provider_route_binding.use.value,
            "adapter": "live" if live else "fixture",
            "provider_id": receipt.response.provider_id,
            "model_id": receipt.response.model_id,
            "finish_reason": receipt.response.metadata.finish_reason,
            "tool_call_count": receipt.response.metadata.tool_call_count,
            # Priced by the caps: source text only. Deliberately NOT placed
            # beside prompt_tokens, which covers the whole prompt including the
            # system message and the tool schema. Comparing those two reads as
            # a calibration and is not one.
            "projected_excerpt_tokens": projected_tokens,
            # These three are like for like. A byte-level BPE cannot emit more
            # tokens than the request has bytes, so the bound is checked here
            # against a live route rather than asserted from construction.
            "request_bytes": receipt.response.metadata.request_bytes,
            "provider_prompt_tokens": receipt.response.metadata.prompt_tokens,
            "token_upper_bound_held": (
                receipt.response.metadata.prompt_tokens
                <= receipt.response.metadata.request_bytes
            ),
            "discloses": "synthetic-fixture-spec only",
            "proves": (
                _live_findings(receipt.response)
                if live
                else "transport, enforcer and ledger are wired and policy-bound"
            ),
            "does_not_prove": (
                "anything about real corpus egress, which needs a source "
                "manifest this command cannot create"
                if live
                else "any real provider route, credential, or model"
            ),
        }
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="specpilot")
    commands = parser.add_subparsers(dest="group", required=True)

    archive = commands.add_parser("archive").add_subparsers(
        dest="command", required=True
    )
    inspect = archive.add_parser("inspect")
    inspect.add_argument("--archive", type=Path, required=True)
    inspect.add_argument("--destination", type=Path, required=True)
    inspect.add_argument("--quarantine", type=Path, required=True)
    inspect.add_argument("--expect-docx", required=True)
    inspect.add_argument("--max-members", type=int, default=8)
    inspect.add_argument("--max-member-bytes", type=int, default=256 * 1024 * 1024)
    inspect.add_argument("--max-total-bytes", type=int, default=512 * 1024 * 1024)
    inspect.set_defaults(handler=_archive_inspect)

    manifest = commands.add_parser("source-manifest").add_subparsers(
        dest="command", required=True
    )
    create = manifest.add_parser("create")
    create.add_argument("--manifest-dir", type=Path, required=True)
    for field in (
        "document-id",
        "document-version",
        "download-url",
        "archive-sha256",
        "docx-sha256",
        "downloaded-at",
        "created-at",
    ):
        create.add_argument(f"--{field}", required=True)
    create.set_defaults(handler=_manifest_create)

    authorize = manifest.add_parser("authorize-successor")
    authorize.add_argument("--manifest-dir", type=Path, required=True)
    authorize.add_argument("--predecessor", required=True)
    authorize.add_argument("--assessment", type=Path, required=True)
    authorize.add_argument("--provider-id", required=True)
    authorize.add_argument("--endpoint-purpose", required=True)
    authorize.add_argument(
        "--use",
        required=True,
        choices=[item.value for item in ProviderUse],
    )
    authorize.add_argument("--created-at", type=_aware_timestamp, required=True)
    authorize.set_defaults(handler=_manifest_authorize)

    egress = commands.add_parser("egress").add_subparsers(
        dest="command", required=True
    )
    smoke = egress.add_parser("envelope-smoke")
    smoke.set_defaults(handler=_envelope_smoke)

    rebind = egress.add_parser("rebind-policy")
    rebind.add_argument("--ledger-dsn", required=True)
    rebind.add_argument("--manifest-dir", type=Path, required=True)
    rebind.add_argument("--policy", type=Path, default=None)
    rebind.add_argument(
        "--corpus-manifest-id", type=_sha256_argument, required=True
    )
    rebind.add_argument("--expected-ledger-id", type=_uuid_argument, required=True)
    rebind.add_argument(
        "--expected-policy-hash", type=_sha256_argument, required=True
    )
    rebind.set_defaults(handler=_egress_rebind_policy)

    provider = commands.add_parser("provider").add_subparsers(
        dest="command", required=True
    )
    route = provider.add_parser("route-smoke")
    # Exactly one, and neither has a default. A fixture smoke proves nothing
    # about a real route and a live smoke costs money and reaches a third
    # party, so which one ran must be a deliberate word on the command line.
    mode = route.add_mutually_exclusive_group(required=True)
    mode.add_argument("--fixture-only", action="store_true")
    mode.add_argument("--live", action="store_true")
    route.add_argument("--route", choices=["main", "judge"], required=True)
    route.add_argument("--ledger-dsn", default=None)
    route.set_defaults(handler=_route_smoke)

    corpus = commands.add_parser("corpus").add_subparsers(
        dest="command", required=True
    )
    parse = corpus.add_parser("parse")
    parse.add_argument("--manifest", required=True)
    parse.add_argument("--manifest-dir", type=Path, required=True)
    parse.add_argument("--xml", type=Path, required=True)
    parse.set_defaults(handler=_corpus_parse)

    clauses = corpus.add_parser("clauses")
    clauses.add_argument("--manifest", required=True)
    clauses.add_argument("--manifest-dir", type=Path, required=True)
    clauses.add_argument("--xml", type=Path, required=True)
    clauses.add_argument("--section", default=None)
    clauses.set_defaults(handler=_corpus_clauses)

    qa = corpus.add_parser("qa")
    qa.add_argument("--manifest", required=True)
    qa.add_argument("--manifest-dir", type=Path, required=True)
    qa.add_argument("--xml", type=Path, required=True)
    qa.add_argument("--model-dir", type=Path, required=True)
    qa.set_defaults(handler=_corpus_qa)

    normative = corpus.add_parser("normative")
    normative.add_argument("--manifest", required=True)
    normative.add_argument("--manifest-dir", type=Path, required=True)
    normative.add_argument("--xml", type=Path, required=True)
    normative.add_argument("--section", default=None)
    normative.add_argument("--min-keywords", type=int, default=0)
    normative.set_defaults(handler=_corpus_normative)

    overlap = corpus.add_parser("overlap")
    overlap.add_argument("--manifest", required=True)
    overlap.add_argument("--manifest-dir", type=Path, required=True)
    overlap.add_argument("--xml", type=Path, required=True)
    overlap.add_argument("--clause-id", action="append", required=True)
    overlap.add_argument("--question", required=True)
    overlap.set_defaults(handler=_corpus_overlap)

    freeze = corpus.add_parser("freeze")
    freeze.add_argument("--source-manifest-dir", type=Path, required=True)
    freeze.add_argument("--corpus-manifest-dir", type=Path, required=True)
    freeze.add_argument(
        "--manifest",
        action="append",
        type=_sha256_argument,
        required=True,
    )
    freeze.add_argument("--xml", action="append", type=Path, required=True)
    freeze.add_argument("--model-dir", type=Path, required=True)
    freeze.add_argument("--qdrant-url", required=True)
    freeze.add_argument("--collection", type=_collection_name_argument, required=True)
    freeze.add_argument("--predecessor", type=_sha256_argument, default=None)
    freeze.add_argument("--created-at", type=_aware_timestamp, required=True)
    freeze.set_defaults(handler=_corpus_freeze)

    verify = corpus.add_parser("verify")
    verify.add_argument("--source-manifest-dir", type=Path, required=True)
    verify.add_argument("--corpus-manifest-dir", type=Path, required=True)
    verify.add_argument("--corpus-manifest", type=_sha256_argument, required=True)
    verify.add_argument(
        "--manifest",
        action="append",
        type=_sha256_argument,
        required=True,
    )
    verify.add_argument("--xml", action="append", type=Path, required=True)
    verify.add_argument("--model-dir", type=Path, required=True)
    verify.add_argument("--qdrant-url", required=True)
    verify.set_defaults(handler=_corpus_verify)

    retrieval = commands.add_parser("retrieval").add_subparsers(
        dest="command", required=True
    )
    search = retrieval.add_parser("search")
    search.add_argument("--manifest", required=True)
    search.add_argument("--manifest-dir", type=Path, required=True)
    search.add_argument("--xml", type=Path, required=True)
    search.add_argument("--query", required=True)
    search.add_argument("--route", choices=["bm25"], default="bm25")
    search.add_argument("--k", type=int, default=5)
    search.set_defaults(handler=_retrieval_search)

    evaluate = retrieval.add_parser("evaluate")
    evaluate.add_argument("--annotation-dir", type=Path, required=True)
    evaluate.add_argument("--corpus-manifest", required=True)
    evaluate.add_argument("--corpus-manifest-dir", type=Path, required=True)
    evaluate.add_argument("--manifest-dir", type=Path, required=True)
    evaluate.add_argument("--manifest", action="append", required=True)
    evaluate.add_argument("--xml", action="append", type=Path, required=True)
    evaluate.add_argument("--model-dir", type=Path, required=True)
    evaluate.add_argument("--device", choices=["mps", "cpu"], required=True)
    evaluate.add_argument("--qdrant-url", required=True)
    # Required and echoed. §8.5 keeps the locked splits unread until W6, and a
    # number whose split is implicit is one that gets quoted as a test result.
    evaluate.add_argument("--split", choices=["dev", "locked"], required=True)
    evaluate.set_defaults(handler=_retrieval_evaluate)

    answer = commands.add_parser("answer")
    answer.add_argument("--question", required=True)
    answer.add_argument("--corpus-manifest", required=True)
    answer.add_argument("--corpus-manifest-dir", type=Path, required=True)
    answer.add_argument("--manifest-dir", type=Path, required=True)
    answer.add_argument("--manifest", action="append", required=True)
    answer.add_argument("--xml", action="append", type=Path, required=True)
    # The authorized successor, named separately from the corpus sources: the
    # sources say which documents the index covers, this one says which
    # compliance decision permits sending from them.
    answer.add_argument("--source-manifest", required=True)
    answer.add_argument("--model-dir", type=Path, required=True)
    answer.add_argument("--device", choices=["mps", "cpu"], required=True)
    answer.add_argument("--qdrant-url", required=True)
    answer.add_argument("--ledger-dsn", required=True)
    answer.add_argument("--route", choices=sorted(LIVE_ROUTE_NAMES), default="main")
    answer.add_argument("--evaluation-root-id", required=True)
    answer.add_argument("--run-id", required=True)
    answer.set_defaults(handler=_answer)

    annotation = commands.add_parser("annotation").add_subparsers(
        dest="command", required=True
    )
    progress = annotation.add_parser("progress")
    progress.add_argument("--annotation-dir", type=Path, required=True)
    # Optional as a group: reviews are reported only when asked for, and asking
    # for them requires declaring the sample they should be measured against.
    progress.add_argument("--review-dir", type=Path, default=None)
    # Optional even alongside --review-dir. Absent, coverage reports 0 of N,
    # which is the truth about a pass that recorded no findings rather than an
    # error about a missing flag.
    progress.add_argument("--deep-review-dir", type=Path, default=None)
    progress.add_argument("--deep-review-rate", type=float, default=None)
    progress.add_argument("--deep-review-salt", default=None)
    progress.add_argument("--pool-dir", type=Path, default=None)
    progress.set_defaults(handler=_annotation_progress)

    template = annotation.add_parser("template")
    template.add_argument("--level", choices=["l1", "l2"], required=True)
    template.set_defaults(handler=_annotation_template)

    add = annotation.add_parser("add")
    add.add_argument("--record", type=Path, required=True)
    add.add_argument("--annotation-dir", type=Path, required=True)
    # Required whenever the record carries gold. A record's gold lives in the
    # one document it names, so one source is enough.
    add.add_argument("--manifest", default=None)
    add.add_argument("--manifest-dir", type=Path, default=None)
    add.add_argument("--xml", type=Path, default=None)
    add.set_defaults(handler=_annotation_add)

    review = annotation.add_parser("review")
    review.add_argument("--proposal", type=Path, required=True)
    review.add_argument("--annotation-dir", type=Path, required=True)
    review.add_argument("--review-dir", type=Path, required=True)
    review.add_argument("--reviewer", required=True)
    # Required, unlike `annotation add`: confirming that no clause answers a
    # question is still a claim about one specific frozen document.
    review.add_argument("--manifest", required=True)
    review.add_argument("--manifest-dir", type=Path, required=True)
    review.add_argument("--xml", type=Path, required=True)
    review.add_argument("--seed", required=True)
    review.add_argument("--distractors", type=int, default=3)
    # No defaults. A deep-review sample that silently ran at some rate nobody
    # chose, under a salt nobody recorded, cannot be checked afterwards — which
    # is the entire reason the sample exists.
    review.add_argument("--deep-review-rate", type=float, required=True)
    review.add_argument("--deep-review-salt", required=True)
    review.set_defaults(handler=_annotation_review)

    deep = annotation.add_parser("deep-review")
    deep.add_argument("--item", required=True)
    deep.add_argument("--annotation-dir", type=Path, required=True)
    deep.add_argument("--deep-review-dir", type=Path, required=True)
    deep.add_argument("--reviewer", required=True)
    deep.add_argument("--manifest", required=True)
    deep.add_argument("--manifest-dir", type=Path, required=True)
    deep.add_argument("--xml", type=Path, required=True)
    # The same rate and salt the pass was registered under. Supplied rather
    # than defaulted, so a deep review of an unsampled item is refused instead
    # of silently redefining which items the sample contained.
    deep.add_argument("--deep-review-rate", type=float, required=True)
    deep.add_argument("--deep-review-salt", required=True)
    deep.set_defaults(handler=_annotation_deep_review)

    pool_register = annotation.add_parser("pool-register")
    pool_register.add_argument("--annotation-dir", type=Path, required=True)
    pool_register.add_argument("--pool-dir", type=Path, required=True)
    pool_register.add_argument("--manifest-dir", type=Path, required=True)
    pool_register.add_argument("--manifest", action="append", required=True)
    pool_register.add_argument("--xml", action="append", type=Path, required=True)
    pool_register.add_argument("--model-dir", type=Path, required=True)
    pool_register.add_argument("--model-id", required=True)
    pool_register.add_argument("--device", choices=["mps", "cpu"], required=True)
    pool_register.add_argument("--qdrant-url", required=True)
    pool_register.add_argument("--collection", required=True)
    pool_register.add_argument("--weights-sha256", required=True)
    pool_register.add_argument("--author-id", required=True)
    pool_register.add_argument("--created-at", type=_aware_timestamp, required=True)
    pool_register.set_defaults(handler=_annotation_pool_register)

    pool_review = annotation.add_parser("pool-review")
    pool_review.add_argument("--annotation-dir", type=Path, required=True)
    pool_review.add_argument("--pool-dir", type=Path, required=True)
    pool_review.add_argument("--run-id", required=True)
    pool_review.add_argument("--manifest-dir", type=Path, required=True)
    pool_review.add_argument("--manifest", action="append", required=True)
    pool_review.add_argument("--xml", action="append", type=Path, required=True)
    pool_review.add_argument("--reviewer", required=True)
    pool_review.set_defaults(handler=_annotation_pool_review)

    pool_status = annotation.add_parser("pool-status")
    pool_status.add_argument("--pool-dir", type=Path, required=True)
    pool_status.add_argument("--run-id", required=True)
    pool_status.set_defaults(handler=_annotation_pool_status)

    embedding = commands.add_parser("embedding").add_subparsers(
        dest="command", required=True
    )
    measure = embedding.add_parser("measure")
    measure.add_argument("--manifest", required=True)
    measure.add_argument("--manifest-dir", type=Path, required=True)
    measure.add_argument("--xml", type=Path, required=True)
    measure.add_argument("--model-dir", type=Path, required=True)
    measure.add_argument("--model-id", default="BAAI/bge-m3")
    measure.add_argument("--device", choices=["mps", "cpu"], required=True)
    measure.add_argument(
        "--batch-order", type=BatchOrder, choices=list(BatchOrder), default="document"
    )
    measure.add_argument("--batch-size", type=int, default=16)
    measure.add_argument("--sample", type=int, default=200)
    measure.set_defaults(handler=_embedding_measure)

    return parser


_RFC3339_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
    r"(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$"
)
_SHA256_ARGUMENT = re.compile(r"^[0-9a-f]{64}$")
_COLLECTION_NAME_ARGUMENT = re.compile(r"^[A-Za-z0-9._-]{1,255}$")


def _sha256_argument(value: str) -> str:
    if _SHA256_ARGUMENT.fullmatch(value) is None:
        raise argparse.ArgumentTypeError("invalid SHA-256 identifier")
    return value


def _uuid_argument(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except (AttributeError, ValueError):
        raise argparse.ArgumentTypeError("invalid UUID identifier") from None


def _collection_name_argument(value: str) -> str:
    if _COLLECTION_NAME_ARGUMENT.fullmatch(value) is None:
        raise argparse.ArgumentTypeError("invalid collection name")
    return value


def _aware_timestamp(value: str) -> datetime:
    if _RFC3339_TIMESTAMP.fullmatch(value) is None:
        raise argparse.ArgumentTypeError("invalid RFC3339 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise argparse.ArgumentTypeError("invalid RFC3339 timestamp") from None
    return parsed.astimezone(UTC)


def main(argv: Sequence[str] | None = None) -> int:
    raw_arguments = list(sys.argv[1:] if argv is None else argv)
    is_corpus_manifest_command = raw_arguments[:2] in (
        ["corpus", "freeze"],
        ["corpus", "verify"],
    )
    is_egress_rebind_command = raw_arguments[:2] == ["egress", "rebind-policy"]
    sanitized_usage_code = (
        "invalid_corpus_manifest_arguments"
        if is_corpus_manifest_command
        else (
            "invalid_egress_rebind_policy_arguments"
            if is_egress_rebind_command
            else None
        )
    )
    if sanitized_usage_code is not None:
        # These commands may receive restricted local paths. Argparse's default
        # diagnostics interpolate the rejected value, so consume them and emit
        # the same stable, aggregate-only usage code as the handlers.
        parse_failed = False
        with redirect_stderr(io.StringIO()):
            try:
                arguments = _parser().parse_args(raw_arguments)
            except SystemExit as error:
                if error.code == 0:
                    raise
                parse_failed = True
        if parse_failed:
            return _refuse(sanitized_usage_code, EXIT_USAGE)
    else:
        arguments = _parser().parse_args(raw_arguments)
    handler: Any = arguments.handler
    result = handler(arguments)
    assert isinstance(result, int)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
