from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import ValidationError

from specpilot.cli import EXIT_IO, EXIT_REFUSED, EXIT_USAGE, main
from specpilot.contracts.corpus_manifest import CorpusManifest
from specpilot.corpus.freezing import (
    CorpusManifestRefusal,
    CorpusSourceInput,
    FreezeCorpusRequest,
    FreezeResult,
    VerifiedCorpus,
    VerifyCorpusRequest,
)
from specpilot.embedding.local_encoder import EmbeddingRuntimeUnavailable
from specpilot.manifests.corpus_store import (
    CollectionLeaseError,
    CorpusManifestIntentConflictError,
    CorpusManifestStore,
)
from specpilot.manifests.store import ManifestStore
from specpilot.retrieval.dense import DenseBackendUnavailable
from tests.helpers.corpus_manifest_factory import SOURCE_IDS, corpus_draft

SOURCE_MARKER = "secret clause prose"
MANIFEST = CorpusManifest.from_draft(corpus_draft())


def _freeze_args() -> list[str]:
    return [
        "corpus",
        "freeze",
        "--source-manifest-dir",
        "source",
        "--corpus-manifest-dir",
        "corpus",
        "--manifest",
        SOURCE_IDS[0],
        "--xml",
        "first.xml",
        "--manifest",
        SOURCE_IDS[1],
        "--xml",
        "second.xml",
        "--model-dir",
        "model",
        "--qdrant-url",
        "http://127.0.0.1:6333",
        "--collection",
        MANIFEST.collection_name,
        "--created-at",
        "2026-08-09T11:00:00Z",
    ]


def _verify_args() -> list[str]:
    return [
        "corpus",
        "verify",
        "--source-manifest-dir",
        "source",
        "--corpus-manifest-dir",
        "corpus",
        "--corpus-manifest",
        MANIFEST.manifest_id,
        "--manifest",
        SOURCE_IDS[0],
        "--xml",
        "first.xml",
        "--manifest",
        SOURCE_IDS[1],
        "--xml",
        "second.xml",
        "--model-dir",
        "model",
        "--qdrant-url",
        "http://127.0.0.1:6333",
    ]


def _without_option(arguments: list[str], option: str) -> list[str]:
    result = list(arguments)
    while option in result:
        index = result.index(option)
        del result[index : index + 2]
        if option not in {"--manifest", "--xml"}:
            break
    return result


@pytest.mark.parametrize(
    ("arguments", "required_option"),
    [
        *(
            (_freeze_args(), option)
            for option in (
                "--source-manifest-dir",
                "--corpus-manifest-dir",
                "--manifest",
                "--xml",
                "--model-dir",
                "--qdrant-url",
                "--collection",
                "--created-at",
            )
        ),
        *(
            (_verify_args(), option)
            for option in (
                "--source-manifest-dir",
                "--corpus-manifest-dir",
                "--corpus-manifest",
                "--manifest",
                "--xml",
                "--model-dir",
                "--qdrant-url",
            )
        ),
    ],
)
def test_corpus_manifest_commands_require_every_documented_argument(
    arguments: list[str],
    required_option: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(_without_option(arguments, required_option))
    captured = capsys.readouterr()

    assert code == EXIT_USAGE
    assert captured.out == ""
    assert captured.err == "invalid_corpus_manifest_arguments\n"


def test_freeze_refuses_unpaired_sources(
    capsys: pytest.CaptureFixture[str],
) -> None:
    arguments = _freeze_args()
    second_xml = len(arguments) - 1 - arguments[::-1].index("--xml")
    del arguments[second_xml : second_xml + 2]

    code = main(arguments)
    captured = capsys.readouterr()

    assert code == EXIT_USAGE
    assert captured.out == ""
    assert captured.err == "source_pair_count_mismatch\n"


def test_verify_refuses_unpaired_sources(
    capsys: pytest.CaptureFixture[str],
) -> None:
    arguments = _verify_args()
    second_manifest = len(arguments) - 1 - arguments[::-1].index("--manifest")
    del arguments[second_manifest : second_manifest + 2]

    code = main(arguments)
    captured = capsys.readouterr()

    assert code == EXIT_USAGE
    assert captured.out == ""
    assert captured.err == "source_pair_count_mismatch\n"


@pytest.mark.parametrize("command", ["freeze", "verify"])
def test_corpus_manifest_commands_refuse_duplicate_source_manifests(
    command: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    arguments = _freeze_args() if command == "freeze" else _verify_args()
    indexes = [index for index, value in enumerate(arguments) if value == "--manifest"]
    arguments[indexes[1] + 1] = SOURCE_IDS[0]

    code = main(arguments)
    captured = capsys.readouterr()

    assert code == EXIT_USAGE
    assert captured.out == ""
    assert captured.err == "duplicate_source_manifest\n"


def test_verify_has_no_collection_override(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    called = False

    def fake_verify(*args: object, **kwargs: object) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr("specpilot.cli.verify_corpus", fake_verify, raising=False)

    code = main([*_verify_args(), "--collection", SOURCE_MARKER])
    captured = capsys.readouterr()

    assert code == EXIT_USAGE
    assert called is False
    assert captured.out == ""
    assert captured.err == "invalid_corpus_manifest_arguments\n"
    assert SOURCE_MARKER not in captured.err


@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-08-09 11:00:00Z",
        "2026-08-09T11:00:00",
        "2026-08-09T11:00:00.1234567Z",
        "2026-13-09T11:00:00Z",
    ],
)
def test_freeze_refuses_non_rfc3339_timestamps_without_echoing_them(
    timestamp: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    arguments = _freeze_args()
    arguments[arguments.index("--created-at") + 1] = timestamp

    code = main(arguments)
    captured = capsys.readouterr()

    assert code == EXIT_USAGE
    assert captured.out == ""
    assert captured.err == "invalid_corpus_manifest_arguments\n"
    assert timestamp not in captured.err


@pytest.mark.parametrize(
    ("timestamp", "expected"),
    [
        ("2026-08-09T11:00:00Z", datetime(2026, 8, 9, 11, tzinfo=UTC)),
        (
            "2026-08-09T19:00:00.1+08:00",
            datetime(2026, 8, 9, 11, 0, 0, 100000, tzinfo=UTC),
        ),
        (
            "2026-08-09T11:00:00.123456+00:00",
            datetime(2026, 8, 9, 11, 0, 0, 123456, tzinfo=UTC),
        ),
    ],
)
def test_freeze_passes_a_normalized_strict_timestamp_to_the_service(
    timestamp: str,
    expected: datetime,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    arguments = _freeze_args()
    arguments[arguments.index("--created-at") + 1] = timestamp

    def fake_freeze(
        request: FreezeCorpusRequest,
        *,
        source_store: ManifestStore,
        corpus_store: CorpusManifestStore,
    ) -> FreezeResult:
        assert request.created_at == expected
        assert isinstance(source_store, ManifestStore)
        assert isinstance(corpus_store, CorpusManifestStore)
        return FreezeResult(MANIFEST, replayed=False)

    monkeypatch.setattr("specpilot.cli.freeze_corpus", fake_freeze, raising=False)

    assert main(arguments) == 0
    capsys.readouterr()


def _expected_payload(status: str) -> dict[str, object]:
    return {
        "status": status,
        "corpus_manifest_id": MANIFEST.manifest_id,
        "source_manifest_ids": list(MANIFEST.source_manifest_ids),
        "collection": MANIFEST.collection_name,
        "point_count": MANIFEST.point_count,
        "derived_corpus_sha256": MANIFEST.derived_corpus_sha256,
        "inventory_root_sha256": MANIFEST.inventory_root_sha256,
        "snapshot_name": MANIFEST.snapshot.name,
        "snapshot_checksum": MANIFEST.snapshot.checksum,
        "snapshot_size_bytes": MANIFEST.snapshot.size_bytes,
    }


@pytest.mark.parametrize(
    ("replayed", "status"),
    [(False, "frozen"), (True, "replayed")],
)
def test_freeze_delegates_once_and_emits_only_manifest_metadata(
    replayed: bool,
    status: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[FreezeCorpusRequest] = []

    def fake_freeze(
        request: FreezeCorpusRequest,
        *,
        source_store: ManifestStore,
        corpus_store: CorpusManifestStore,
    ) -> FreezeResult:
        calls.append(request)
        assert isinstance(source_store, ManifestStore)
        assert isinstance(corpus_store, CorpusManifestStore)
        return FreezeResult(MANIFEST, replayed=replayed)

    monkeypatch.setattr("specpilot.cli.freeze_corpus", fake_freeze, raising=False)

    code = main([*_freeze_args(), "--predecessor", "9" * 64])
    captured = capsys.readouterr()

    assert code == 0
    assert captured.err == ""
    assert json.loads(captured.out) == _expected_payload(status)
    assert len(calls) == 1
    assert calls[0] == FreezeCorpusRequest(
        sources=(
            CorpusSourceInput(SOURCE_IDS[0], Path("first.xml")),
            CorpusSourceInput(SOURCE_IDS[1], Path("second.xml")),
        ),
        model_dir=Path("model"),
        qdrant_url="http://127.0.0.1:6333",
        collection_name=MANIFEST.collection_name,
        predecessor_manifest_id="9" * 64,
        created_at=datetime(2026, 8, 9, 11, tzinfo=UTC),
    )
    assert tuple((item.manifest_id, item.xml_path) for item in calls[0].sources) == (
        (SOURCE_IDS[0], Path("first.xml")),
        (SOURCE_IDS[1], Path("second.xml")),
    )
    assert SOURCE_MARKER not in captured.out + captured.err


class _ClosableDense:
    def __init__(self, *, close_error: BaseException | None = None) -> None:
        self.closes = 0
        self.close_error = close_error

    def close(self) -> None:
        self.closes += 1
        if self.close_error is not None:
            raise self.close_error


def _verified(dense: _ClosableDense) -> VerifiedCorpus:
    return VerifiedCorpus(
        MANIFEST,
        cast(Any, object()),
        cast(Any, object()),
        cast(Any, dense),
    )


def test_verify_delegates_once_closes_and_emits_only_manifest_metadata(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[VerifyCorpusRequest] = []
    dense = _ClosableDense()

    def fake_verify(
        request: VerifyCorpusRequest,
        *,
        source_store: ManifestStore,
        corpus_store: CorpusManifestStore,
    ) -> VerifiedCorpus:
        calls.append(request)
        assert isinstance(source_store, ManifestStore)
        assert isinstance(corpus_store, CorpusManifestStore)
        return _verified(dense)

    monkeypatch.setattr("specpilot.cli.verify_corpus", fake_verify, raising=False)

    code = main(_verify_args())
    captured = capsys.readouterr()

    assert code == 0
    assert captured.err == ""
    assert json.loads(captured.out) == _expected_payload("verified")
    assert len(calls) == 1
    assert calls[0] == VerifyCorpusRequest(
        manifest_id=MANIFEST.manifest_id,
        sources=(
            CorpusSourceInput(SOURCE_IDS[0], Path("first.xml")),
            CorpusSourceInput(SOURCE_IDS[1], Path("second.xml")),
        ),
        model_dir=Path("model"),
        qdrant_url="http://127.0.0.1:6333",
    )
    assert tuple((item.manifest_id, item.xml_path) for item in calls[0].sources) == (
        (SOURCE_IDS[0], Path("first.xml")),
        (SOURCE_IDS[1], Path("second.xml")),
    )
    assert dense.closes == 1
    assert SOURCE_MARKER not in captured.out + captured.err


@pytest.mark.parametrize("command", ["freeze", "verify"])
def test_domain_refusals_keep_their_stable_code(
    command: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise CorpusManifestRefusal("corpus_source_mismatch")

    monkeypatch.setattr(f"specpilot.cli.{command}_corpus", refuse, raising=False)

    code = main(_freeze_args() if command == "freeze" else _verify_args())
    captured = capsys.readouterr()

    assert code == EXIT_REFUSED
    assert captured.out == ""
    assert captured.err == "corpus_source_mismatch\n"


def test_unsupported_schema_keeps_its_own_refusal_code(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise CorpusManifestRefusal("unsupported_corpus_manifest_version")

    monkeypatch.setattr("specpilot.cli.verify_corpus", refuse, raising=False)

    code = main(_verify_args())
    captured = capsys.readouterr()

    assert code == EXIT_REFUSED
    assert captured.out == ""
    assert captured.err == "unsupported_corpus_manifest_version\n"


@pytest.mark.parametrize("command", ["freeze", "verify"])
@pytest.mark.parametrize(
    "failure",
    [
        OSError(f"{SOURCE_MARKER} /private/restricted/source.xml"),
        DenseBackendUnavailable(f"{SOURCE_MARKER} backend"),
        EmbeddingRuntimeUnavailable(f"{SOURCE_MARKER} model"),
    ],
)
def test_unavailable_dependencies_emit_no_exception_path_or_source_text(
    command: str,
    failure: BaseException,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def unavailable(*args: object, **kwargs: object) -> None:
        raise failure

    monkeypatch.setattr(
        f"specpilot.cli.{command}_corpus", unavailable, raising=False
    )

    code = main(_freeze_args() if command == "freeze" else _verify_args())
    captured = capsys.readouterr()

    assert code == EXIT_IO
    assert captured.out == ""
    assert captured.err == "corpus_manifest_unavailable\n"
    assert SOURCE_MARKER not in captured.out + captured.err
    assert "/private/restricted" not in captured.out + captured.err


def test_verify_close_failure_is_unavailable_and_emits_no_success_payload(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    dense = _ClosableDense(
        close_error=DenseBackendUnavailable(f"{SOURCE_MARKER} close")
    )
    monkeypatch.setattr(
        "specpilot.cli.verify_corpus",
        lambda *args, **kwargs: _verified(dense),
        raising=False,
    )

    code = main(_verify_args())
    captured = capsys.readouterr()

    assert code == EXIT_IO
    assert dense.closes == 1
    assert captured.out == ""
    assert captured.err == "corpus_manifest_unavailable\n"
    assert SOURCE_MARKER not in captured.out + captured.err


def _replace_option(arguments: list[str], option: str, value: str) -> list[str]:
    changed = list(arguments)
    changed[changed.index(option) + 1] = value
    return changed


@pytest.mark.parametrize(
    ("arguments", "option", "invalid_value"),
    [
        (_freeze_args(), "--manifest", SOURCE_MARKER),
        (_freeze_args(), "--manifest", "A" * 64),
        (_freeze_args(), "--manifest", "a" * 63),
        (
            [*_freeze_args(), "--predecessor", SOURCE_MARKER],
            "--predecessor",
            SOURCE_MARKER,
        ),
        (_verify_args(), "--manifest", SOURCE_MARKER),
        (_verify_args(), "--corpus-manifest", SOURCE_MARKER),
    ],
)
def test_corpus_manifest_commands_reject_invalid_ids_at_the_parser_boundary(
    arguments: list[str],
    option: str,
    invalid_value: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    called = False

    def service_trap(*args: object, **kwargs: object) -> None:
        nonlocal called
        called = True

    command = arguments[1]
    monkeypatch.setattr(f"specpilot.cli.{command}_corpus", service_trap)

    code = main(_replace_option(arguments, option, invalid_value))
    captured = capsys.readouterr()

    assert code == EXIT_USAGE
    assert called is False
    assert captured.out == ""
    assert captured.err == "invalid_corpus_manifest_arguments\n"
    assert invalid_value not in captured.out + captured.err


@pytest.mark.parametrize(
    "invalid_collection",
    [
        SOURCE_MARKER,
        " leading-space",
        "trailing-space ",
        "a" * 256,
        "specpilot/other",
    ],
)
def test_freeze_rejects_invalid_collection_names_at_the_parser_boundary(
    invalid_collection: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    called = False

    def service_trap(*args: object, **kwargs: object) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr("specpilot.cli.freeze_corpus", service_trap)

    code = main(
        _replace_option(_freeze_args(), "--collection", invalid_collection)
    )
    captured = capsys.readouterr()

    assert code == EXIT_USAGE
    assert called is False
    assert captured.out == ""
    assert captured.err == "invalid_corpus_manifest_arguments\n"
    assert invalid_collection not in captured.out + captured.err


def _contract_validation_error() -> ValidationError:
    with pytest.raises(ValidationError) as caught:
        CorpusManifest.model_validate({})
    return caught.value


@pytest.mark.parametrize("command", ["freeze", "verify"])
@pytest.mark.parametrize(
    "failure",
    [
        ValueError(f"{SOURCE_MARKER} /private/restricted/manifest.json"),
        _contract_validation_error(),
        CorpusManifestIntentConflictError(f"{SOURCE_MARKER} intent"),
        CollectionLeaseError(f"{SOURCE_MARKER} lease namespace"),
        RuntimeError(f"{SOURCE_MARKER} storage runtime"),
    ],
)
def test_storage_contract_and_lease_failures_are_unavailable_without_text(
    command: str,
    failure: Exception,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise failure

    monkeypatch.setattr(f"specpilot.cli.{command}_corpus", fail)

    code = main(_freeze_args() if command == "freeze" else _verify_args())
    captured = capsys.readouterr()

    assert code == EXIT_IO
    assert captured.out == ""
    assert captured.err == "corpus_manifest_unavailable\n"
    assert SOURCE_MARKER not in captured.out + captured.err
    assert "/private/restricted" not in captured.out + captured.err


def test_verify_maps_a_real_malformed_corpus_store_to_unavailable(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    corpus_root = tmp_path / SOURCE_MARKER
    corpus_root.mkdir(mode=0o700)
    (corpus_root / ".locks").mkdir(mode=0o700)
    record = corpus_root / f"{MANIFEST.manifest_id}.json"
    record.write_text(
        '{"schema_version":"corpus-manifest/v1","secret":"source marker"}',
        encoding="utf-8",
    )
    os.chmod(record, 0o600)
    with pytest.raises(ValueError, match="stored corpus manifest is invalid"):
        CorpusManifestStore(corpus_root).read(MANIFEST.manifest_id)
    arguments = _verify_args()
    arguments[arguments.index("--corpus-manifest-dir") + 1] = str(corpus_root)

    code = main(arguments)
    captured = capsys.readouterr()

    assert code == EXIT_IO
    assert captured.out == ""
    assert captured.err == "corpus_manifest_unavailable\n"
    assert SOURCE_MARKER not in captured.out + captured.err
    assert str(corpus_root) not in captured.out + captured.err
