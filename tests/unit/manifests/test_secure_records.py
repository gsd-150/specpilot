from __future__ import annotations

import errno
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from specpilot.manifests._secure_records import SecureRecordDirectory

MAX_BYTES = 256 * 1024
RECORD_ID = "a" * 64
RECORD_NAME = f"{RECORD_ID}.json"
ENUMERATE_PROBE = """
import sys
from pathlib import Path

from specpilot.manifests._secure_records import SecureRecordDirectory

try:
    with SecureRecordDirectory.open(Path(sys.argv[1]), create=False) as records:
        records.content_ids()
except BaseException:
    raise SystemExit(73)
raise SystemExit(0)
"""


def _private_file(path: Path, data: bytes = b"record") -> None:
    path.write_bytes(data)
    path.chmod(0o600)


def _secure_root(tmp_path: Path) -> Path:
    root = tmp_path / "records"
    root.mkdir(mode=0o700)
    return root


def _replace_private_entry(
    directory_descriptor: int,
    name: str,
    data: bytes,
) -> os.stat_result:
    os.unlink(name, dir_fd=directory_descriptor)
    descriptor = os.open(
        name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
        dir_fd=directory_descriptor,
    )
    try:
        assert os.write(descriptor, data) == len(data)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)


def _assert_enumeration_probe_rejects(root: Path) -> None:
    completed = subprocess.run(
        [sys.executable, "-c", ENUMERATE_PROBE, str(root)],
        check=False,
        timeout=1,
    )

    assert completed.returncode == 73


def _assert_descriptor_closed(descriptor: int) -> None:
    with pytest.raises(OSError) as raised:
        os.fstat(descriptor)
    assert raised.value.errno == errno.EBADF


def test_open_creates_an_exactly_private_root_and_closes_it(tmp_path: Path) -> None:
    root = tmp_path / "records"

    with SecureRecordDirectory.open(root, create=True) as records:
        descriptor = records.fd
        assert stat.S_IMODE(os.fstat(descriptor).st_mode) == 0o700
        assert stat.S_IMODE(root.stat().st_mode) == 0o700

    with pytest.raises(OSError):
        os.fstat(descriptor)


def test_open_repairs_an_existing_root_only_for_create(tmp_path: Path) -> None:
    root = _secure_root(tmp_path)
    root.chmod(0o755)

    with pytest.raises(PermissionError):
        SecureRecordDirectory.open(root, create=False)

    with SecureRecordDirectory.open(root, create=True):
        pass

    assert stat.S_IMODE(root.stat().st_mode) == 0o700


def test_from_fd_closes_only_owned_descriptors_and_only_once(tmp_path: Path) -> None:
    root = _secure_root(tmp_path)
    borrowed = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    owned = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)

    try:
        borrowed_records = SecureRecordDirectory.from_fd(root, borrowed)
        with borrowed_records:
            pass
        os.fstat(borrowed)

        owned_records = SecureRecordDirectory.from_fd(root, owned, close_fd=True)
        with owned_records:
            pass
        with owned_records:
            pass
        with pytest.raises(OSError):
            os.fstat(owned)
    finally:
        os.close(borrowed)


def test_from_fd_rejects_a_nonprivate_root_without_closing_borrowed_fd(
    tmp_path: Path,
) -> None:
    root = _secure_root(tmp_path)
    root.chmod(0o750)
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)

    try:
        with pytest.raises(PermissionError):
            SecureRecordDirectory.from_fd(root, descriptor)
        os.fstat(descriptor)
    finally:
        os.close(descriptor)


def test_content_ids_returns_sorted_validated_record_ids(tmp_path: Path) -> None:
    root = _secure_root(tmp_path)
    _private_file(root / f"{'b' * 64}.json", b"second")
    _private_file(root / RECORD_NAME, b"first")
    _private_file(root / ".manifest-recoverable", b"temporary")

    with SecureRecordDirectory.open(root, create=False) as records:
        identifiers = records.content_ids()

    assert identifiers == (RECORD_ID, "b" * 64)


@pytest.mark.parametrize(
    "attack",
    ["symlink", "directory", "hardlink", "bad-mode", "oversize"],
)
def test_enumeration_rejects_attacker_shaped_records(
    tmp_path: Path,
    attack: str,
) -> None:
    root = _secure_root(tmp_path)
    record = root / RECORD_NAME
    if attack == "symlink":
        victim = tmp_path / "victim"
        _private_file(victim)
        record.symlink_to(victim)
    elif attack == "directory":
        record.mkdir(mode=0o600)
    elif attack == "hardlink":
        victim = tmp_path / "victim"
        _private_file(victim)
        os.link(victim, record)
    elif attack == "bad-mode":
        _private_file(record)
        record.chmod(0o640)
    else:
        _private_file(record, b"x" * (MAX_BYTES + 1))

    with (
        SecureRecordDirectory.open(root, create=False) as records,
        pytest.raises(FileExistsError) as raised,
    ):
        records.content_ids()

    assert raised.value.args == (record,)


def test_enumeration_rejects_a_fifo_without_blocking(tmp_path: Path) -> None:
    root = _secure_root(tmp_path)
    os.mkfifo(root / RECORD_NAME, mode=0o600)

    _assert_enumeration_probe_rejects(root)


@pytest.mark.parametrize(
    "attack",
    ["symlink", "directory", "hardlink", "bad-mode", "oversize"],
)
def test_enumeration_rejects_attacker_shaped_manifest_temporaries(
    tmp_path: Path,
    attack: str,
) -> None:
    root = _secure_root(tmp_path)
    temporary = root / ".manifest-attacker"
    if attack == "symlink":
        victim = tmp_path / "victim"
        _private_file(victim)
        temporary.symlink_to(victim)
    elif attack == "directory":
        temporary.mkdir(mode=0o600)
    elif attack == "hardlink":
        victim = tmp_path / "victim"
        _private_file(victim)
        os.link(victim, temporary)
    elif attack == "bad-mode":
        _private_file(temporary)
        temporary.chmod(0o640)
    else:
        _private_file(temporary, b"x" * (MAX_BYTES + 1))

    with (
        SecureRecordDirectory.open(root, create=False) as records,
        pytest.raises(FileExistsError) as raised,
    ):
        records.content_ids()

    assert raised.value.args == (temporary,)


def test_enumeration_rejects_a_manifest_temporary_fifo_without_blocking(
    tmp_path: Path,
) -> None:
    root = _secure_root(tmp_path)
    os.mkfifo(root / ".manifest-attacker", mode=0o600)

    _assert_enumeration_probe_rejects(root)


def test_enumeration_rejects_unrelated_entries_unless_explicitly_allowed(
    tmp_path: Path,
) -> None:
    root = _secure_root(tmp_path)
    locks = root / ".locks"
    locks.mkdir(mode=0o700)

    with SecureRecordDirectory.open(root, create=False) as records:
        with pytest.raises(FileExistsError) as raised:
            records.content_ids()
        assert records.content_ids(
            allowed_non_records=frozenset({".locks"})
        ) == ()

    assert raised.value.args == (locks,)


def test_read_rejects_path_traversal(tmp_path: Path) -> None:
    root = _secure_root(tmp_path)
    outside = tmp_path / RECORD_NAME
    _private_file(outside, b"outside")

    with (
        SecureRecordDirectory.open(root, create=False) as records,
        pytest.raises(ValueError, match="record name"),
    ):
        records.read(f"../{RECORD_NAME}", max_bytes=MAX_BYTES)


def test_publish_creates_a_private_record_and_leaves_no_temporary(
    tmp_path: Path,
) -> None:
    root = tmp_path / "records"

    with SecureRecordDirectory.open(root, create=True) as records:
        stored = records.publish(RECORD_NAME, b"canonical", max_bytes=MAX_BYTES)
        identifiers = records.content_ids()

    record = root / RECORD_NAME
    assert stored == b"canonical"
    assert record.read_bytes() == b"canonical"
    assert stat.S_IMODE(record.stat().st_mode) == 0o600
    assert record.stat().st_nlink == 1
    assert identifiers == (RECORD_ID,)
    assert tuple(root.iterdir()) == (record,)


def test_publish_uses_atomic_no_replace_hard_link(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from specpilot.manifests import _secure_records as secure_records_module

    root = _secure_root(tmp_path)
    observed_source: list[tuple[int, int, bool]] = []
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
        observed_source.append(
            (source_status.st_ino, stat.S_IMODE(source_status.st_mode), follow_symlinks)
        )
        original_link(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )

    monkeypatch.setattr(secure_records_module.os, "link", recording_link)
    monkeypatch.setattr(os, "supports_dir_fd", os.supports_dir_fd | {recording_link})
    monkeypatch.setattr(
        os,
        "supports_follow_symlinks",
        os.supports_follow_symlinks | {recording_link},
    )

    with SecureRecordDirectory.open(root, create=False) as records:
        records.publish(RECORD_NAME, b"canonical", max_bytes=MAX_BYTES)

    assert observed_source == [
        ((root / RECORD_NAME).stat().st_ino, 0o600, False)
    ]


def test_publish_fsyncs_the_file_and_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from specpilot.manifests import _secure_records as secure_records_module

    root = _secure_root(tmp_path)
    fsynced_types: list[int] = []
    original_fsync = os.fsync

    def recording_fsync(descriptor: int) -> None:
        fsynced_types.append(stat.S_IFMT(os.fstat(descriptor).st_mode))
        original_fsync(descriptor)

    monkeypatch.setattr(secure_records_module.os, "fsync", recording_fsync)

    with SecureRecordDirectory.open(root, create=False) as records:
        stored = records.publish(RECORD_NAME, b"durable", max_bytes=MAX_BYTES)

    assert stored == b"durable"
    assert fsynced_types[0] == stat.S_IFREG
    assert stat.S_IFDIR in fsynced_types[1:]


def test_byte_identical_replay_returns_existing_without_replacing_it(
    tmp_path: Path,
) -> None:
    root = _secure_root(tmp_path)
    record = root / RECORD_NAME
    _private_file(record, b"canonical")
    fixed_timestamp_ns = 1_700_000_000_000_000_000
    os.utime(record, ns=(fixed_timestamp_ns, fixed_timestamp_ns))
    original_inode = record.stat().st_ino

    with SecureRecordDirectory.open(root, create=False) as records:
        stored = records.publish(RECORD_NAME, b"canonical", max_bytes=MAX_BYTES)

    assert stored == b"canonical"
    assert record.stat().st_ino == original_inode
    assert record.stat().st_mtime_ns == fixed_timestamp_ns
    assert tuple(root.iterdir()) == (record,)


def test_conflicting_replay_fails_without_overwriting_existing_bytes(
    tmp_path: Path,
) -> None:
    root = _secure_root(tmp_path)
    record = root / RECORD_NAME
    _private_file(record, b"existing")
    original_inode = record.stat().st_ino

    with (
        SecureRecordDirectory.open(root, create=False) as records,
        pytest.raises(FileExistsError) as raised,
    ):
        records.publish(RECORD_NAME, b"different", max_bytes=MAX_BYTES)

    assert raised.value.args == (record,)
    assert record.read_bytes() == b"existing"
    assert record.stat().st_ino == original_inode
    assert tuple(root.iterdir()) == (record,)


def test_target_replacement_after_link_is_preserved_when_publish_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from specpilot.manifests import _secure_records as secure_records_module

    root = _secure_root(tmp_path)
    replacement_statuses: list[os.stat_result] = []
    original_link = os.link

    def replacing_link(
        source: str,
        destination: str,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> None:
        assert src_dir_fd is not None
        assert dst_dir_fd is not None
        original_link(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )
        replacement_statuses.append(
            _replace_private_entry(dst_dir_fd, destination, b"replacement-target")
        )

    monkeypatch.setattr(secure_records_module.os, "link", replacing_link)
    monkeypatch.setattr(os, "supports_dir_fd", os.supports_dir_fd | {replacing_link})
    monkeypatch.setattr(
        os,
        "supports_follow_symlinks",
        os.supports_follow_symlinks | {replacing_link},
    )

    with (
        SecureRecordDirectory.open(root, create=False) as records,
        pytest.raises(FileExistsError),
    ):
        records.publish(RECORD_NAME, b"canonical", max_bytes=MAX_BYTES)

    record = root / RECORD_NAME
    assert len(replacement_statuses) == 1
    assert record.read_bytes() == b"replacement-target"
    assert record.stat().st_ino == replacement_statuses[0].st_ino
    assert record.stat().st_nlink == 1
    assert stat.S_IMODE(record.stat().st_mode) == 0o600


def test_temp_replacement_after_link_is_preserved_when_publish_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from specpilot.manifests import _secure_records as secure_records_module

    root = _secure_root(tmp_path)
    replacement: list[tuple[str, os.stat_result]] = []
    original_link = os.link

    def replacing_link(
        source: str,
        destination: str,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> None:
        assert src_dir_fd is not None
        assert dst_dir_fd is not None
        original_link(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )
        replacement.append(
            (
                source,
                _replace_private_entry(src_dir_fd, source, b"replacement-temp"),
            )
        )

    monkeypatch.setattr(secure_records_module.os, "link", replacing_link)
    monkeypatch.setattr(os, "supports_dir_fd", os.supports_dir_fd | {replacing_link})
    monkeypatch.setattr(
        os,
        "supports_follow_symlinks",
        os.supports_follow_symlinks | {replacing_link},
    )

    with (
        SecureRecordDirectory.open(root, create=False) as records,
        pytest.raises(FileExistsError),
    ):
        records.publish(RECORD_NAME, b"canonical", max_bytes=MAX_BYTES)

    assert len(replacement) == 1
    temporary_name, replacement_status = replacement[0]
    temporary = root / temporary_name
    assert temporary.read_bytes() == b"replacement-temp"
    assert temporary.stat().st_ino == replacement_status.st_ino
    assert temporary.stat().st_nlink == 1
    assert stat.S_IMODE(temporary.stat().st_mode) == 0o600
    assert not (root / RECORD_NAME).exists()


def test_temp_replacement_on_link_error_is_preserved_by_exception_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from specpilot.manifests import _secure_records as secure_records_module

    root = _secure_root(tmp_path)
    replacement: list[tuple[str, os.stat_result]] = []

    def failing_link(
        source: str,
        destination: str,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> None:
        del destination, dst_dir_fd, follow_symlinks
        assert src_dir_fd is not None
        replacement.append(
            (
                source,
                _replace_private_entry(src_dir_fd, source, b"replacement-temp"),
            )
        )
        raise OSError("forced link failure")

    monkeypatch.setattr(secure_records_module.os, "link", failing_link)
    monkeypatch.setattr(os, "supports_dir_fd", os.supports_dir_fd | {failing_link})
    monkeypatch.setattr(
        os,
        "supports_follow_symlinks",
        os.supports_follow_symlinks | {failing_link},
    )

    with (
        SecureRecordDirectory.open(root, create=False) as records,
        pytest.raises(OSError, match="forced link failure"),
    ):
        records.publish(RECORD_NAME, b"canonical", max_bytes=MAX_BYTES)

    assert len(replacement) == 1
    temporary_name, replacement_status = replacement[0]
    temporary = root / temporary_name
    assert temporary.read_bytes() == b"replacement-temp"
    assert temporary.stat().st_ino == replacement_status.st_ino
    assert temporary.stat().st_nlink == 1
    assert stat.S_IMODE(temporary.stat().st_mode) == 0o600
    assert not (root / RECORD_NAME).exists()


def test_cleanup_stat_error_cannot_skip_closing_the_owned_temp_fd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from specpilot.manifests import _secure_records as secure_records_module

    root = _secure_root(tmp_path)
    captured: list[tuple[int, str]] = []
    link_failed = False
    original_create = secure_records_module.create_private_file
    original_stat = os.stat

    def recording_create(
        directory_descriptor: int,
        *,
        prefix: str,
    ) -> tuple[int, str]:
        created = original_create(directory_descriptor, prefix=prefix)
        captured.append(created)
        return created

    def failing_link(
        source: str,
        destination: str,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> None:
        nonlocal link_failed
        del source, destination, src_dir_fd, dst_dir_fd, follow_symlinks
        link_failed = True
        raise OSError("forced link failure")

    def failing_cleanup_stat(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        *,
        dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> os.stat_result:
        if link_failed and captured and path == captured[0][1]:
            raise PermissionError(root / captured[0][1])
        return original_stat(
            path,
            dir_fd=dir_fd,
            follow_symlinks=follow_symlinks,
        )

    with SecureRecordDirectory.open(root, create=False) as records:
        monkeypatch.setattr(
            secure_records_module,
            "create_private_file",
            recording_create,
        )
        monkeypatch.setattr(secure_records_module.os, "link", failing_link)
        monkeypatch.setattr(secure_records_module.os, "stat", failing_cleanup_stat)
        monkeypatch.setattr(
            os,
            "supports_dir_fd",
            os.supports_dir_fd | {failing_link, failing_cleanup_stat},
        )
        monkeypatch.setattr(
            os,
            "supports_follow_symlinks",
            os.supports_follow_symlinks | {failing_link, failing_cleanup_stat},
        )

        with pytest.raises(PermissionError):
            records.publish(RECORD_NAME, b"canonical", max_bytes=MAX_BYTES)

    assert len(captured) == 1
    _assert_descriptor_closed(captured[0][0])
    assert (root / captured[0][1]).exists()


def test_cleanup_directory_fsync_error_cannot_skip_closing_owned_temp_fd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from specpilot.manifests import _secure_records as secure_records_module

    root = _secure_root(tmp_path)
    captured: list[tuple[int, str]] = []
    link_failed = False
    original_create = secure_records_module.create_private_file
    original_fsync = os.fsync

    def recording_create(
        directory_descriptor: int,
        *,
        prefix: str,
    ) -> tuple[int, str]:
        created = original_create(directory_descriptor, prefix=prefix)
        captured.append(created)
        return created

    def failing_link(
        source: str,
        destination: str,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> None:
        nonlocal link_failed
        del source, destination, src_dir_fd, dst_dir_fd, follow_symlinks
        link_failed = True
        raise OSError("forced link failure")

    with SecureRecordDirectory.open(root, create=False) as records:
        root_descriptor = records.fd

        def failing_cleanup_fsync(descriptor: int) -> None:
            if link_failed and descriptor == root_descriptor:
                raise OSError("forced cleanup fsync failure")
            original_fsync(descriptor)

        monkeypatch.setattr(
            secure_records_module,
            "create_private_file",
            recording_create,
        )
        monkeypatch.setattr(secure_records_module.os, "link", failing_link)
        monkeypatch.setattr(secure_records_module.os, "fsync", failing_cleanup_fsync)
        monkeypatch.setattr(os, "supports_dir_fd", os.supports_dir_fd | {failing_link})
        monkeypatch.setattr(
            os,
            "supports_follow_symlinks",
            os.supports_follow_symlinks | {failing_link},
        )

        with pytest.raises(OSError, match="forced cleanup fsync failure"):
            records.publish(RECORD_NAME, b"canonical", max_bytes=MAX_BYTES)

    assert len(captured) == 1
    _assert_descriptor_closed(captured[0][0])
    assert not (root / captured[0][1]).exists()


def test_publish_revalidates_each_required_publication_boundary_in_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from specpilot.manifests import _secure_records as secure_records_module

    root = _secure_root(tmp_path)
    events: list[str] = []
    original_link = os.link
    original_read = SecureRecordDirectory.read
    original_revalidate = secure_records_module.revalidate_directory_path
    original_unlink = os.unlink

    def recording_revalidate(path: Path, descriptor: int) -> None:
        events.append("revalidate")
        original_revalidate(path, descriptor)

    def recording_link(
        source: str,
        destination: str,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> None:
        events.append("link")
        original_link(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )

    def recording_unlink(
        name: str,
        *,
        dir_fd: int | None = None,
    ) -> None:
        events.append("unlink-temp" if name.startswith(".manifest-") else "unlink")
        original_unlink(name, dir_fd=dir_fd)

    def recording_read(
        self: SecureRecordDirectory,
        name: str,
        *,
        max_bytes: int,
    ) -> bytes:
        events.append("read-entry")
        data = original_read(self, name, max_bytes=max_bytes)
        events.append("read-return")
        return data

    with SecureRecordDirectory.open(root, create=False) as records:
        monkeypatch.setattr(
            secure_records_module,
            "revalidate_directory_path",
            recording_revalidate,
        )
        monkeypatch.setattr(secure_records_module.os, "link", recording_link)
        monkeypatch.setattr(secure_records_module.os, "unlink", recording_unlink)
        monkeypatch.setattr(SecureRecordDirectory, "read", recording_read)
        monkeypatch.setattr(
            os,
            "supports_dir_fd",
            os.supports_dir_fd | {recording_link, recording_unlink},
        )
        monkeypatch.setattr(
            os,
            "supports_follow_symlinks",
            os.supports_follow_symlinks | {recording_link},
        )
        stored = records.publish(RECORD_NAME, b"canonical", max_bytes=MAX_BYTES)

    assert stored == b"canonical"
    assert events == [
        "revalidate",  # publish entry
        "revalidate",  # immediately before link
        "link",
        "revalidate",  # after link, before temp unlink
        "unlink-temp",
        "revalidate",  # after temp unlink
        "read-entry",
        "revalidate",  # read entry
        "revalidate",  # read final path/fd check
        "read-return",
        "revalidate",  # publish final path/fd check
    ]


@pytest.mark.parametrize(
    ("name", "data", "max_bytes"),
    [
        ("not-a-record.json", b"data", MAX_BYTES),
        ("../" + RECORD_NAME, b"data", MAX_BYTES),
        (RECORD_NAME, b"too-large", 4),
    ],
)
def test_publish_rejects_invalid_publications_before_creating_files(
    tmp_path: Path,
    name: str,
    data: bytes,
    max_bytes: int,
) -> None:
    root = _secure_root(tmp_path)

    with (
        SecureRecordDirectory.open(root, create=False) as records,
        pytest.raises(ValueError, match="publication"),
    ):
        records.publish(name, data, max_bytes=max_bytes)

    assert tuple(root.iterdir()) == ()


def test_root_swap_cannot_redirect_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from specpilot.manifests import _secure_records as secure_records_module

    root = _secure_root(tmp_path)
    moved_root = tmp_path / "validated-records"
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    original_link = os.link

    def swapping_link(
        source: str,
        destination: str,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> None:
        root.rename(moved_root)
        root.symlink_to(outside, target_is_directory=True)
        original_link(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )

    monkeypatch.setattr(secure_records_module.os, "link", swapping_link)
    monkeypatch.setattr(os, "supports_dir_fd", os.supports_dir_fd | {swapping_link})
    monkeypatch.setattr(
        os,
        "supports_follow_symlinks",
        os.supports_follow_symlinks | {swapping_link},
    )

    with (
        SecureRecordDirectory.open(root, create=False) as records,
        pytest.raises(FileExistsError),
    ):
        records.publish(RECORD_NAME, b"canonical", max_bytes=MAX_BYTES)

    assert tuple(outside.iterdir()) == ()
    assert tuple(moved_root.iterdir()) == ()


def test_open_closes_the_pinned_descriptor_on_validation_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from specpilot.manifests import _secure_records as secure_records_module

    root = _secure_root(tmp_path)
    root.chmod(0o750)
    opened: list[int] = []
    original_open_directory = secure_records_module.open_directory_path

    def recording_open_directory(path: Path, *, create: bool) -> int:
        descriptor = original_open_directory(path, create=create)
        opened.append(descriptor)
        return descriptor

    monkeypatch.setattr(
        secure_records_module,
        "open_directory_path",
        recording_open_directory,
    )

    with pytest.raises(PermissionError):
        SecureRecordDirectory.open(root, create=False)

    assert len(opened) == 1
    with pytest.raises(OSError):
        os.fstat(opened[0])


def test_read_closes_the_file_descriptor_on_validation_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from specpilot.manifests import _secure_records as secure_records_module

    root = _secure_root(tmp_path)
    record = root / RECORD_NAME
    _private_file(record)
    record.chmod(0o640)
    opened: list[int] = []
    original_open = os.open

    def recording_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
        if path == RECORD_NAME:
            opened.append(descriptor)
        return descriptor

    monkeypatch.setattr(secure_records_module.os, "open", recording_open)
    monkeypatch.setattr(os, "supports_dir_fd", os.supports_dir_fd | {recording_open})

    with (
        SecureRecordDirectory.open(root, create=False) as records,
        pytest.raises(FileExistsError),
    ):
        records.read(RECORD_NAME, max_bytes=MAX_BYTES)

    assert len(opened) == 1
    with pytest.raises(OSError):
        os.fstat(opened[0])
