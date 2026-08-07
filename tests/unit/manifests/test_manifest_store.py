from __future__ import annotations

import importlib
import json
import os
import stat
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from specpilot.contracts.manifests import SourceManifest, SourceManifestDraft
from specpilot.manifests.canonical import canonical_json
from specpilot.manifests.store import ManifestStore, UnsupportedManifestVersionError
from tests.unit.manifests.test_source_manifest import initial_fields

store_module = importlib.import_module("specpilot.manifests.store")


def create_initial(store_dir: Path) -> tuple[ManifestStore, SourceManifest]:
    store = ManifestStore(store_dir)
    manifest = store.create_source(SourceManifestDraft(**initial_fields()))
    return store, manifest


def test_store_creates_private_canonical_manifest(tmp_path: Path) -> None:
    store, manifest = create_initial(tmp_path / "manifests")
    manifest_path = tmp_path / "manifests" / f"{manifest.manifest_id}.json"

    assert manifest_path.read_bytes() == canonical_json(
        manifest,
        include_manifest_id=True,
    )
    assert stat.S_IMODE(manifest_path.stat().st_mode) == 0o600
    assert store.read_source(manifest.manifest_id) == manifest


def test_byte_identical_replay_returns_existing_without_replacing_it(
    tmp_path: Path,
) -> None:
    store, manifest = create_initial(tmp_path / "manifests")
    manifest_path = tmp_path / "manifests" / f"{manifest.manifest_id}.json"
    fixed_timestamp_ns = 1_700_000_000_000_000_000
    os.utime(manifest_path, ns=(fixed_timestamp_ns, fixed_timestamp_ns))

    replay = store.create_source(SourceManifestDraft(**initial_fields()))

    assert replay == manifest
    assert manifest_path.stat().st_mtime_ns == fixed_timestamp_ns
    assert sorted(path.name for path in manifest_path.parent.iterdir()) == [
        manifest_path.name
    ]


def test_store_refuses_noncanonical_same_id_bytes(tmp_path: Path) -> None:
    store = ManifestStore(tmp_path / "manifests")
    draft = SourceManifestDraft(**initial_fields())
    manifest = store_module.SourceManifest.from_draft(draft)
    store_dir = tmp_path / "manifests"
    store_dir.mkdir()
    manifest_path = store_dir / f"{manifest.manifest_id}.json"
    manifest_path.write_text(
        json.dumps(manifest.model_dump(mode="json"), indent=2),
        encoding="utf-8",
    )
    manifest_path.chmod(0o600)

    with pytest.raises(FileExistsError):
        store.create_source(draft)


def test_store_refuses_a_preexisting_manifest_symlink(tmp_path: Path) -> None:
    store = ManifestStore(tmp_path / "manifests")
    draft = SourceManifestDraft(**initial_fields())
    manifest = store_module.SourceManifest.from_draft(draft)
    store_dir = tmp_path / "manifests"
    store_dir.mkdir()
    victim = tmp_path / "victim.json"
    victim.write_text("do not touch", encoding="utf-8")
    (store_dir / f"{manifest.manifest_id}.json").symlink_to(victim)

    with pytest.raises(FileExistsError):
        store.create_source(draft)

    assert victim.read_text(encoding="utf-8") == "do not touch"


@pytest.mark.parametrize("existing_kind", ["partial", "directory"])
def test_store_refuses_partial_or_nonregular_existing_manifest(
    tmp_path: Path,
    existing_kind: str,
) -> None:
    store = ManifestStore(tmp_path / "manifests")
    draft = SourceManifestDraft(**initial_fields())
    manifest = store_module.SourceManifest.from_draft(draft)
    store_dir = tmp_path / "manifests"
    store_dir.mkdir()
    manifest_path = store_dir / f"{manifest.manifest_id}.json"
    if existing_kind == "partial":
        manifest_path.write_bytes(b'{"partial":')
        manifest_path.chmod(0o600)
    else:
        manifest_path.mkdir()

    with pytest.raises(FileExistsError):
        store.create_source(draft)


def test_read_rejects_filename_and_content_id_mismatch(tmp_path: Path) -> None:
    store, manifest = create_initial(tmp_path / "manifests")
    other_fields = initial_fields()
    other_fields["document_version"] = "other-version"
    other = store_module.SourceManifest.from_draft(SourceManifestDraft(**other_fields))
    manifest_path = tmp_path / "manifests" / f"{manifest.manifest_id}.json"
    manifest_path.write_bytes(canonical_json(other, include_manifest_id=True))

    with pytest.raises(ValueError, match="manifest"):
        store.read_source(manifest.manifest_id)


def test_read_rejects_a_secure_manifest_with_an_unsupported_schema_version(
    tmp_path: Path,
) -> None:
    # v2 was this test's example of "unsupported" until the RFC corpus made it
    # a supported version. The behaviour under test is unchanged; only the
    # example of an unknown version had to move on.
    store_dir = tmp_path / "manifests"
    store_dir.mkdir(mode=0o700)
    manifest_id = "e" * 64
    manifest_path = store_dir / f"{manifest_id}.json"
    manifest_path.write_text(
        '{"schema_version":"source-manifest/v3"}',
        encoding="utf-8",
    )
    manifest_path.chmod(0o600)

    with pytest.raises(UnsupportedManifestVersionError):
        ManifestStore(store_dir).read_source(manifest_id)


def test_read_does_not_classify_nonstandard_json_as_an_unsupported_version(
    tmp_path: Path,
) -> None:
    store_dir = tmp_path / "manifests"
    store_dir.mkdir(mode=0o700)
    manifest_id = "d" * 64
    manifest_path = store_dir / f"{manifest_id}.json"
    manifest_path.write_bytes(b'{"schema_version":"source-manifest/v2","extra":NaN}')
    manifest_path.chmod(0o600)

    with pytest.raises(ValueError) as raised:
        ManifestStore(store_dir).read_source(manifest_id)

    assert not isinstance(raised.value, UnsupportedManifestVersionError)


def test_store_publishes_with_atomic_no_replace_link(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[tuple[int, bool]] = []
    original_link = os.link

    def recording_link(
        source: str,
        destination: str,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> None:
        source_status = os.stat(source, dir_fd=src_dir_fd, follow_symlinks=False)
        observed.append((stat.S_IMODE(source_status.st_mode), follow_symlinks))
        original_link(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )

    monkeypatch.setattr(store_module.os, "link", recording_link)
    monkeypatch.setattr(os, "supports_dir_fd", os.supports_dir_fd | {recording_link})
    monkeypatch.setattr(
        os,
        "supports_follow_symlinks",
        os.supports_follow_symlinks | {recording_link},
    )

    create_initial(tmp_path / "manifests")

    assert observed == [(0o600, False)]


def test_store_directory_swap_cannot_redirect_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store_dir = tmp_path / "manifests"
    moved_store_dir = tmp_path / "validated-manifests"
    outside = tmp_path / "outside"
    outside.mkdir()
    original_link = os.link

    def swapping_link(
        source: str,
        destination: str,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> None:
        store_dir.rename(moved_store_dir)
        store_dir.symlink_to(outside, target_is_directory=True)
        original_link(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )

    monkeypatch.setattr(store_module.os, "link", swapping_link)
    monkeypatch.setattr(os, "supports_dir_fd", os.supports_dir_fd | {swapping_link})
    monkeypatch.setattr(
        os,
        "supports_follow_symlinks",
        os.supports_follow_symlinks | {swapping_link},
    )

    with pytest.raises(FileExistsError):
        create_initial(store_dir)

    assert list(outside.iterdir()) == []


def test_store_closes_the_pinned_directory_after_publication_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened_descriptors: list[int] = []
    original_open_directory = store_module.open_directory_path

    def recording_open_directory(path: Path, *, create: bool) -> int:
        descriptor = original_open_directory(path, create=create)
        opened_descriptors.append(descriptor)
        return descriptor

    def reject_revalidation(path: Path, descriptor: int) -> None:
        del descriptor
        raise FileExistsError(path)

    monkeypatch.setattr(
        store_module,
        "open_directory_path",
        recording_open_directory,
    )
    monkeypatch.setattr(
        store_module,
        "revalidate_directory_path",
        reject_revalidation,
    )

    with pytest.raises(FileExistsError):
        create_initial(tmp_path / "manifests")

    assert len(opened_descriptors) == 1
    with pytest.raises(OSError):
        os.fstat(opened_descriptors[0])


@pytest.mark.parametrize("missing_primitive", ["O_NOFOLLOW", "O_DIRECTORY"])
def test_store_fails_closed_without_secure_filesystem_primitive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    missing_primitive: str,
) -> None:
    store_dir = tmp_path / "manifests"
    monkeypatch.delattr(os, missing_primitive)

    with pytest.raises(RuntimeError, match="secure filesystem primitives"):
        create_initial(store_dir)

    assert not store_dir.exists()


def test_store_rejects_an_invalid_manifest_id_before_filesystem_access(
    tmp_path: Path,
) -> None:
    store_dir = tmp_path / "manifests"
    store = ManifestStore(store_dir)

    with pytest.raises(ValueError, match="manifest_id"):
        store.read_source("../escape")

    assert not store_dir.exists()


def test_store_normalizes_successor_creation_time_to_utc(tmp_path: Path) -> None:
    from tests.unit.manifests.test_source_manifest import assessment, route

    store, initial = create_initial(tmp_path / "manifests")
    successor = store.create_successor(
        initial,
        assessment=assessment(),
        route_binding=route(),
        created_at=datetime(2026, 8, 6, 11, tzinfo=UTC),
    )

    assert successor.created_at.tzinfo is UTC


def test_store_rejects_fifo_manifest_without_blocking(tmp_path: Path) -> None:
    store_dir = tmp_path / "manifests"
    store_dir.mkdir(mode=0o700)
    manifest_id = "f" * 64
    fifo_path = store_dir / f"{manifest_id}.json"
    os.mkfifo(fifo_path, mode=0o600)
    probe = """
import sys
from pathlib import Path
from specpilot.manifests.store import ManifestStore

try:
    ManifestStore(Path(sys.argv[1])).read_source(sys.argv[2])
except FileExistsError:
    raise SystemExit(73)
except BaseException:
    raise SystemExit(74)
raise SystemExit(75)
"""

    completed = subprocess.run(
        [sys.executable, "-c", probe, str(store_dir), manifest_id],
        check=False,
        timeout=1,
    )
    assert completed.returncode == 73

    started = time.monotonic()
    with pytest.raises(FileExistsError) as raised:
        ManifestStore(store_dir).read_source(manifest_id)
    assert time.monotonic() - started < 0.5
    assert raised.value.args == (fifo_path,)
