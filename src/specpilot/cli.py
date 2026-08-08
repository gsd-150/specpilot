from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from specpilot.annotation.progress import read_progress
from specpilot.annotation.store import AnnotationStore
from specpilot.contracts.annotation import L1Annotation, L2Annotation
from specpilot.contracts.archive import ArchivePolicy, UnsafeArchiveError
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
from specpilot.contracts.rfc import RfcLimits, UnsafeRfcError
from specpilot.corpus.clauses import (
    ClauseLimits,
    OversizedClauseError,
    build_clauses,
    build_normative_index,
    iter_clause_texts,
)
from specpilot.corpus.overlap import question_gold_jaccard
from specpilot.egress.enforcer import EgressPolicyEnforcer, EgressPolicyViolation
from specpilot.egress.policy import EgressPolicy
from specpilot.embedding.local_encoder import EmbeddingRuntimeUnavailable, load_encoder
from specpilot.embedding.throughput import (
    BatchOrder,
    estimate_full_corpus_seconds,
    evenly_spaced,
    measure_throughput,
    weights_sha256,
)
from specpilot.ingestion.archive import extract_expected_docx
from specpilot.ingestion.ooxml import OoxmlLimits, UnsafeOoxmlError, inspect_docx
from specpilot.manifests.store import ManifestStore, UnsupportedManifestVersionError
from specpilot.rfc.structure import extract_structure

# Exit codes, matching the ingestion worker: 2 is a refused input or policy
# violation, 3 is an I/O fault, 4 is bad usage. Every non-zero exit prints one
# stable code to stderr and nothing else.
EXIT_REFUSED = 2
EXIT_IO = 3
EXIT_USAGE = 4

_FIXTURE_CORPUS_ID = "c" * 64


def _emit(payload: dict[str, Any]) -> int:
    json.dump(payload, sys.stdout, sort_keys=True, separators=(",", ":"))
    sys.stdout.write("\n")
    return 0


def _refuse(code: str, exit_code: int = EXIT_REFUSED) -> int:
    """Print one machine-readable code. Never a path, message, or payload."""
    print(code, file=sys.stderr)
    return exit_code


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


def _frozen_source(arguments: argparse.Namespace) -> RfcSourceManifest | str:
    """Resolve the manifest that froze this document, or return a refusal code.

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
        actual = hashlib.sha256(arguments.xml.read_bytes()).hexdigest()
    except OSError:
        return "io_error"
    if actual != manifest.xml_sha256:
        return "document_hash_mismatch"
    return manifest


def _refuse_source(code: str) -> int:
    return _refuse(code, EXIT_IO if code == "io_error" else EXIT_REFUSED)


def _corpus_parse(arguments: argparse.Namespace) -> int:
    """Parse one frozen specification, reporting counts and never its text."""
    manifest = _frozen_source(arguments)
    if isinstance(manifest, str):
        return _refuse_source(manifest)

    try:
        structure = extract_structure(arguments.xml, RfcLimits())
        clauses = build_clauses(arguments.xml, RfcLimits(), ClauseLimits())
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
    manifest = _frozen_source(arguments)
    if isinstance(manifest, str):
        return _refuse_source(manifest)

    try:
        clauses = build_clauses(arguments.xml, RfcLimits(), ClauseLimits())
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
    manifest = _frozen_source(arguments)
    if isinstance(manifest, str):
        return _refuse_source(manifest)

    try:
        index = build_normative_index(arguments.xml, RfcLimits(), ClauseLimits())
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
    manifest = _frozen_source(arguments)
    if isinstance(manifest, str):
        return _refuse_source(manifest)

    try:
        texts = {
            clause.clause_id: text
            for clause, text in iter_clause_texts(
                arguments.xml, RfcLimits(), ClauseLimits()
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
    "schema_version": "annotation-l1/v1",
    "item_id": "l1-dev-001",
    "split": "dev",
    "question": "",
    "direction": "clause_first",
    "independent_path": "literal_search",
    "document_id": "ietf-rfc-9110",
    "document_version": "2022-06",
    "gold_clause_ids": [],
    "gold_section_paths": [],
    "key_points": [{"point_id": "kp-1", "criterion": "", "factual_values": []}],
    "expected_refusal": False,
    "question_gold_jaccard": None,
}
_L2_TEMPLATE: dict[str, Any] = {
    **_L1_TEMPLATE,
    "schema_version": "annotation-l2/v1",
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


def _annotation_add(arguments: argparse.Namespace) -> int:
    """Validate one authored record and store it.

    Every rule the contract encodes bites here: a record whose gold came from
    the retriever names a path `IndependentPath` has no value for, an answerable
    item without gold has no overlap figure to stratify by, and an unanswerable
    one may not carry gold. A refusal writes nothing.
    """
    try:
        data = json.loads(arguments.record.read_text(encoding="utf-8"))
    except OSError:
        return _refuse("io_error", EXIT_IO)
    except ValueError:
        return _refuse("invalid_annotation_record")

    declared = data.get("schema_version") if isinstance(data, dict) else None
    model: type[L1Annotation] | type[L2Annotation] = (
        L2Annotation if declared == "annotation-l2/v1" else L1Annotation
    )
    try:
        record = model.model_validate(data)
    except ValidationError:
        return _refuse("invalid_annotation_record")

    try:
        stored = AnnotationStore(arguments.annotation_dir).create(record)
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


class _FixtureCounter:
    provider_id = "fixture-provider"
    model_id = "fixture-model-v1"

    def count_tokens(self, text: str) -> int:
        return max(len(text.split()), 1)


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
    try:
        report = read_progress(directory)
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
    manifest = _frozen_source(arguments)
    if isinstance(manifest, str):
        return _refuse_source(manifest)

    try:
        texts = tuple(
            text
            for _, text in iter_clause_texts(
                arguments.xml, RfcLimits(), ClauseLimits()
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
) -> tuple[ManifestStore, Any]:
    """An authorized manifest bound to one synthetic route, for smoke runs."""
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
        provider_id="fixture-provider",
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


def _route_smoke(arguments: argparse.Namespace) -> int:
    """Send synthetic fixture text through the real transport and ledger.

    This proves the send path is wired and policy-bound. It does not prove any
    real provider works: that needs credentials and a route, and their absence
    is reported as blocked rather than passed.
    """
    if arguments.ledger_dsn is None:
        return _refuse("ledger_not_configured")
    return asyncio.run(_route_smoke_async(arguments))


async def _route_smoke_async(arguments: argparse.Namespace) -> int:
    from specpilot.egress.ledger import LedgerError
    from specpilot.egress.postgres import PostgresEgressLedger
    from specpilot.providers.base import ProviderError
    from specpilot.providers.fake import FakeProvider
    from specpilot.providers.transport import PolicyBoundTransport

    # --route must actually change the route. A judge smoke that quietly
    # exercises the online chain is false evidence for the go/no-go checklist.
    judging = arguments.route == "judge"
    store, manifest = _fixture_manifest(
        use=ProviderUse.OFFLINE_JUDGE if judging else ProviderUse.ONLINE_MAIN,
        endpoint_purpose="fixture-judge" if judging else "fixture-smoke",
    )
    provider = FakeProvider(
        provider_id="fixture-provider",
        model_id="fixture-model-v1",
    )
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
        model_id="fixture-model-v1",
        source_manifest=manifest,
        payload=payload,
    )
    try:
        response = await transport.send(request, idempotency_key="route-smoke-1")
    except EgressPolicyViolation as error:
        return _refuse(f"refused:{error.code}")
    except LedgerError as error:
        return _refuse(f"blocked:{error.code}")
    except ProviderError as error:
        return _refuse(f"blocked:{error.public_error_code}")

    return _emit(
        {
            "status": "passed",
            "route": arguments.route,
            "provider_use": manifest.provider_route_binding.use.value,
            "adapter": "fixture",
            "provider_id": response.provider_id,
            "model_id": response.model_id,
            "finish_reason": response.metadata.finish_reason,
            "proves": "transport, enforcer and ledger are wired and policy-bound",
            "does_not_prove": "any real provider route, credential, or model",
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

    provider = commands.add_parser("provider").add_subparsers(
        dest="command", required=True
    )
    route = provider.add_parser("route-smoke")
    route.add_argument("--fixture-only", action="store_true", required=True)
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

    annotation = commands.add_parser("annotation").add_subparsers(
        dest="command", required=True
    )
    progress = annotation.add_parser("progress")
    progress.add_argument("--annotation-dir", type=Path, required=True)
    progress.set_defaults(handler=_annotation_progress)

    template = annotation.add_parser("template")
    template.add_argument("--level", choices=["l1", "l2"], required=True)
    template.set_defaults(handler=_annotation_template)

    add = annotation.add_parser("add")
    add.add_argument("--record", type=Path, required=True)
    add.add_argument("--annotation-dir", type=Path, required=True)
    add.set_defaults(handler=_annotation_add)

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


def _aware_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("timestamp must carry an offset")
    return parsed.astimezone(UTC)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    handler: Any = arguments.handler
    result = handler(arguments)
    assert isinstance(result, int)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
