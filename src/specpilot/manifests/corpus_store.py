from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Never, Self, SupportsIndex, cast

from pydantic import ValidationError

from specpilot.contracts.corpus_manifest import (
    CorpusManifest,
    CorpusManifestDraft,
    CorpusManifestIntent,
)
from specpilot.ingestion._secure_fs import (
    directory_open_flags,
    open_directory_path,
)
from specpilot.manifests._secure_records import SecureRecordDirectory
from specpilot.manifests.canonical import canonical_json

_MANIFEST_ID = re.compile(r"^[0-9a-f]{64}$")
_COLLECTION_NAME = re.compile(r"^[A-Za-z0-9._-]{1,255}$")
_MAX_MANIFEST_BYTES = 256 * 1024
_LOCKS_DIRECTORY = ".locks"
_LEASE_ISSUER = object()
_FileIdentity = tuple[int, int]


class UnsupportedCorpusManifestVersionError(ValueError):
    """Raised when a stored corpus manifest declares an unknown version."""


class CollectionLeaseError(RuntimeError):
    """Raised when a collection lease is invalid for an operation."""


class CollectionLeaseUnavailableError(CollectionLeaseError):
    """Raised when a non-blocking collection lease cannot be acquired."""


class CollectionFrozenError(CollectionLeaseError):
    """Raised when a durable manifest has revoked collection writes."""


class CorpusManifestIntentConflictError(ValueError):
    """Raised when one freeze intent would bind more than one manifest."""


class CorpusPredecessorError(ValueError):
    """Raised when a corpus-manifest predecessor reference is invalid."""


def _file_identity(status: os.stat_result) -> _FileIdentity:
    return status.st_dev, status.st_ino


def _raise_first(errors: list[BaseException]) -> None:
    if errors:
        raise errors[0]


@dataclass(slots=True, init=False)
class _CollectionLease:
    _collection_name: str = field(repr=False)
    _issue_token: object = field(repr=False)
    _owner_token: object = field(repr=False)
    _root_fd: int = field(repr=False)
    _lock_fd: int = field(repr=False)
    _locks_identity: _FileIdentity = field(repr=False)
    _exclusive: bool
    _closed: bool = False

    def __init__(self) -> None:
        raise TypeError("collection leases are issued by CorpusManifestStore")

    @classmethod
    def _issue(
        cls,
        *,
        issue_token: object,
        collection_name: str,
        owner_token: object,
        root_fd: int,
        lock_fd: int,
        locks_identity: _FileIdentity,
        exclusive: bool,
    ) -> Self:
        if issue_token is not _LEASE_ISSUER:
            raise CollectionLeaseError("collection lease issuer is invalid")
        lease = object.__new__(cls)
        lease._collection_name = collection_name
        lease._issue_token = issue_token
        lease._owner_token = owner_token
        lease._root_fd = root_fd
        lease._lock_fd = lock_fd
        lease._locks_identity = locks_identity
        lease._exclusive = exclusive
        lease._closed = False
        return lease

    def __enter__(self) -> Self:
        self.require_active_for(self._collection_name)
        return self

    def __copy__(self) -> Never:
        raise TypeError("collection leases cannot be copied or serialized")

    def __deepcopy__(self, memo: dict[int, object]) -> Never:
        del memo
        raise TypeError("collection leases cannot be copied or serialized")

    def __reduce__(self) -> Never:
        raise TypeError("collection leases cannot be copied or serialized")

    def __reduce_ex__(self, protocol: SupportsIndex, /) -> Never:
        del protocol
        raise TypeError("collection leases cannot be copied or serialized")

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback
        self.close()

    @property
    def collection_name(self) -> str:
        self.require_active_for(self._collection_name)
        return self._collection_name

    @property
    def root_fd(self) -> int:
        self.require_active_for(self._collection_name)
        return self._root_fd

    def require_active_for(self, collection_name: str) -> None:
        try:
            valid = self._issue_token is _LEASE_ISSUER and not self._closed
            bound_collection = self._collection_name
        except AttributeError as error:
            raise CollectionLeaseError("collection lease is closed") from error
        if not valid:
            raise CollectionLeaseError("collection lease is closed")
        if bound_collection != collection_name:
            raise CollectionLeaseError("collection lease names another collection")

    def require_owned(
        self,
        *,
        owner_token: object,
        collection_name: str,
        exclusive: bool | None = None,
    ) -> None:
        self.require_active_for(collection_name)
        if self._owner_token is not owner_token:
            raise CollectionLeaseError("collection lease is not active for this store")
        if exclusive is not None and self._exclusive is not exclusive:
            raise CollectionLeaseError("collection lease has the wrong mode")

    def close(self) -> None:
        try:
            if self._issue_token is not _LEASE_ISSUER:
                raise CollectionLeaseError("collection lease is closed")
            if self._closed:
                return
            lock_fd = self._lock_fd
            root_fd = self._root_fd
        except AttributeError as error:
            raise CollectionLeaseError("collection lease is closed") from error

        self._closed = True
        self._lock_fd = -1
        self._root_fd = -1
        errors: list[BaseException] = []
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        except BaseException as error:
            errors.append(error)
        try:
            os.close(lock_fd)
        except BaseException as error:
            errors.append(error)
        try:
            os.close(root_fd)
        except BaseException as error:
            errors.append(error)
        _raise_first(errors)


@dataclass(slots=True, init=False)
class CollectionWriteLease(_CollectionLease):
    """A live shared lease authorizing one collection writer lifetime."""

    def require_active_for(self, collection_name: str) -> None:
        super().require_active_for(collection_name)
        if self._exclusive:
            raise CollectionLeaseError("collection lease has the wrong mode")


@dataclass(slots=True, init=False)
class CollectionFreezeLease(_CollectionLease):
    """A live exclusive lease authorizing collection freeze publication."""

    def require_active_for(self, collection_name: str) -> None:
        super().require_active_for(collection_name)
        if not self._exclusive:
            raise CollectionLeaseError("collection lease has the wrong mode")


def _reject_nonstandard_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


class CorpusManifestStore:
    """Immutable corpus manifests and the durable frozen-collection registry."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory
        self._owner_token = object()

    def read(self, manifest_id: str) -> CorpusManifest:
        self._validate_manifest_id(manifest_id)
        manifests = self.read_all()
        for manifest in manifests:
            if manifest.manifest_id == manifest_id:
                return manifest
        raise FileNotFoundError(self._directory / f"{manifest_id}.json")

    def read_all(self) -> tuple[CorpusManifest, ...]:
        with SecureRecordDirectory.open(self._directory, create=False) as records:
            return self._decode_all(records)

    def find_by_intent(
        self,
        intent: CorpusManifestIntent,
        *,
        lease: CollectionFreezeLease,
    ) -> CorpusManifest | None:
        self._require_freeze_lease(lease, intent.collection_name)
        manifests = self._read_all_under(lease)
        self._validate_predecessor_reference(intent, manifests)
        matches = tuple(item for item in manifests if item.intent == intent)
        if len(matches) > 1:
            raise CorpusManifestIntentConflictError(
                "intent already has multiple corpus manifests"
            )
        return matches[0] if matches else None

    def require_publishable_intent(
        self,
        intent: CorpusManifestIntent,
        *,
        lease: CollectionFreezeLease,
    ) -> None:
        self._require_freeze_lease(lease, intent.collection_name)
        manifests = self._read_all_under(lease)
        self._require_publishable_against(intent, manifests)

    def create(
        self,
        draft: CorpusManifestDraft,
        *,
        lease: CollectionFreezeLease,
    ) -> CorpusManifest:
        self._require_freeze_lease(lease, draft.collection_name)
        existing = self.find_by_intent(draft.intent, lease=lease)
        manifest = CorpusManifest.from_draft(draft)
        if existing is not None:
            if existing != manifest:
                raise CorpusManifestIntentConflictError(
                    "intent already has a corpus manifest"
                )
            return existing
        self.require_publishable_intent(draft.intent, lease=lease)
        return self._publish_under(manifest, lease=lease)

    def acquire_write_lease(
        self,
        collection_name: str,
        *,
        blocking: bool = True,
    ) -> CollectionWriteLease:
        lease = cast(
            CollectionWriteLease,
            self._acquire(collection_name, exclusive=False, blocking=blocking),
        )
        try:
            if any(
                item.collection_name == collection_name
                for item in self._read_all_under(lease)
            ):
                raise CollectionFrozenError(collection_name)
            return lease
        except BaseException:
            lease.close()
            raise

    def acquire_freeze_lease(
        self,
        collection_name: str,
        *,
        blocking: bool = True,
    ) -> CollectionFreezeLease:
        return cast(
            CollectionFreezeLease,
            self._acquire(collection_name, exclusive=True, blocking=blocking),
        )

    def _acquire(
        self,
        collection_name: str,
        *,
        exclusive: bool,
        blocking: bool,
    ) -> CollectionWriteLease | CollectionFreezeLease:
        self._validate_collection_name(collection_name)
        root_fd: int | None = None
        locks_fd: int | None = None
        lock_fd: int | None = None
        lock_held = False
        try:
            root_fd = open_directory_path(self._directory, create=True)
            os.fchmod(root_fd, 0o700)
            self._validate_root(root_fd)
            locks_fd, locks_identity = self._open_locks_directory(
                root_fd,
                create=True,
            )
            lock_name = f"{hashlib.sha256(collection_name.encode()).hexdigest()}.lock"
            lock_fd = self._open_lock_file(locks_fd, lock_name)
            operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
            if not blocking:
                operation |= fcntl.LOCK_NB
            try:
                fcntl.flock(lock_fd, operation)
            except OSError as error:
                if error.errno in {errno.EWOULDBLOCK, errno.EAGAIN}:
                    raise CollectionLeaseUnavailableError(collection_name) from error
                raise
            lock_held = True
            self._validate_lock_file(locks_fd, lock_name, lock_fd)
            self._validate_named_locks_directory(
                root_fd,
                locks_fd,
                expected=locks_identity,
            )
            self._validate_root(root_fd)
            os.close(locks_fd)
            locks_fd = None
            lease_type = CollectionFreezeLease if exclusive else CollectionWriteLease
            lease = lease_type._issue(
                issue_token=_LEASE_ISSUER,
                collection_name=collection_name,
                owner_token=self._owner_token,
                root_fd=root_fd,
                lock_fd=lock_fd,
                locks_identity=locks_identity,
                exclusive=exclusive,
            )
            root_fd = None
            lock_fd = None
            return lease
        except BaseException:
            errors: list[BaseException] = []
            if lock_fd is not None:
                if lock_held:
                    try:
                        fcntl.flock(lock_fd, fcntl.LOCK_UN)
                    except BaseException as error:
                        errors.append(error)
                try:
                    os.close(lock_fd)
                except BaseException as error:
                    errors.append(error)
            if locks_fd is not None:
                try:
                    os.close(locks_fd)
                except BaseException as error:
                    errors.append(error)
            if root_fd is not None:
                try:
                    os.close(root_fd)
                except BaseException as error:
                    errors.append(error)
            raise

    def _read_all_under(
        self,
        lease: CollectionWriteLease | CollectionFreezeLease,
    ) -> tuple[CorpusManifest, ...]:
        self._require_owned_lease(lease)
        with SecureRecordDirectory.from_fd(
            self._directory,
            lease.root_fd,
            close_fd=False,
        ) as records:
            return self._decode_all(
                records,
                expected_locks_identity=lease._locks_identity,
            )

    def _publish_under(
        self,
        manifest: CorpusManifest,
        *,
        lease: CollectionFreezeLease,
    ) -> CorpusManifest:
        self._require_freeze_lease(lease, manifest.collection_name)
        data = canonical_json(manifest, include_manifest_id=True)
        if len(data) > _MAX_MANIFEST_BYTES:
            raise ValueError("corpus manifest exceeds maximum storage size")
        destination_name = f"{manifest.manifest_id}.json"
        with SecureRecordDirectory.from_fd(
            self._directory,
            lease.root_fd,
            close_fd=False,
        ) as records:
            manifests = self._decode_all(
                records,
                expected_locks_identity=lease._locks_identity,
            )
            matching_intent = tuple(
                item for item in manifests if item.intent == manifest.intent
            )
            if matching_intent:
                if len(matching_intent) != 1 or matching_intent[0] != manifest:
                    raise CorpusManifestIntentConflictError(
                        "intent already has a corpus manifest"
                    )
                return matching_intent[0]
            self._require_publishable_against(manifest.intent, manifests)
            records.publish(
                destination_name,
                data,
                max_bytes=_MAX_MANIFEST_BYTES,
            )
            stored_manifests = self._decode_all(
                records,
                expected_locks_identity=lease._locks_identity,
            )
            os.fsync(records.fd)

        stored = tuple(
            item
            for item in stored_manifests
            if item.manifest_id == manifest.manifest_id
        )
        if len(stored) != 1 or stored[0] != manifest:
            raise FileExistsError(self._directory / destination_name)
        return stored[0]

    def _decode_all(
        self,
        records: SecureRecordDirectory,
        *,
        expected_locks_identity: _FileIdentity | None = None,
    ) -> tuple[CorpusManifest, ...]:
        locks_fd, locks_identity = self._open_locks_directory(
            records.fd,
            create=False,
        )
        try:
            if (
                expected_locks_identity is not None
                and locks_identity != expected_locks_identity
            ):
                raise FileExistsError(self._directory / _LOCKS_DIRECTORY)
            manifest_ids = records.content_ids(
                allowed_non_records=frozenset({_LOCKS_DIRECTORY})
            )
            manifests = tuple(
                self._decode_canonical(
                    records.read(
                        f"{manifest_id}.json",
                        max_bytes=_MAX_MANIFEST_BYTES,
                    ),
                    manifest_id,
                )
                for manifest_id in manifest_ids
            )
            self._validate_predecessor_graph(manifests)
            self._validate_named_locks_directory(
                records.fd,
                locks_fd,
                expected=locks_identity,
            )
            return manifests
        finally:
            os.close(locks_fd)

    def _open_locks_directory(
        self,
        root_fd: int,
        *,
        create: bool,
    ) -> tuple[int, _FileIdentity]:
        created = False
        if create:
            try:
                os.mkdir(_LOCKS_DIRECTORY, mode=0o700, dir_fd=root_fd)
                created = True
            except FileExistsError:
                pass
        try:
            locks_fd = os.open(
                _LOCKS_DIRECTORY,
                directory_open_flags(),
                dir_fd=root_fd,
            )
        except OSError as error:
            if error.errno in {errno.ELOOP, errno.ENOTDIR, errno.EISDIR}:
                raise FileExistsError(self._directory / _LOCKS_DIRECTORY) from error
            raise
        try:
            if created:
                os.fchmod(locks_fd, 0o700)
                os.fsync(root_fd)
            status = os.fstat(locks_fd)
            if not stat.S_ISDIR(status.st_mode):
                raise FileExistsError(self._directory / _LOCKS_DIRECTORY)
            if stat.S_IMODE(status.st_mode) != 0o700:
                raise PermissionError(self._directory / _LOCKS_DIRECTORY)
            identity = _file_identity(status)
            self._validate_named_locks_directory(
                root_fd,
                locks_fd,
                expected=identity,
            )
            return locks_fd, identity
        except BaseException:
            os.close(locks_fd)
            raise

    def _validate_named_locks_directory(
        self,
        root_fd: int,
        locks_fd: int,
        *,
        expected: _FileIdentity,
    ) -> None:
        opened = os.fstat(locks_fd)
        if (
            not stat.S_ISDIR(opened.st_mode)
            or stat.S_IMODE(opened.st_mode) != 0o700
            or _file_identity(opened) != expected
        ):
            raise FileExistsError(self._directory / _LOCKS_DIRECTORY)
        try:
            named = os.stat(
                _LOCKS_DIRECTORY,
                dir_fd=root_fd,
                follow_symlinks=False,
            )
        except OSError as error:
            raise FileExistsError(self._directory / _LOCKS_DIRECTORY) from error
        if (
            not stat.S_ISDIR(named.st_mode)
            or stat.S_IMODE(named.st_mode) != 0o700
            or _file_identity(named) != expected
        ):
            raise FileExistsError(self._directory / _LOCKS_DIRECTORY)

    def _open_lock_file(self, locks_fd: int, name: str) -> int:
        nonblocking_flag = getattr(os, "O_NONBLOCK", 0)
        if not nonblocking_flag:
            raise RuntimeError("required secure filesystem primitives unavailable")
        flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | nonblocking_flag
        created = False
        try:
            lock_fd = os.open(name, flags | os.O_EXCL, 0o600, dir_fd=locks_fd)
            created = True
        except FileExistsError:
            try:
                lock_fd = os.open(name, flags, 0o600, dir_fd=locks_fd)
            except OSError as error:
                if error.errno in {errno.ELOOP, errno.EISDIR, errno.ENXIO}:
                    path = self._directory / _LOCKS_DIRECTORY / name
                    raise FileExistsError(path) from error
                raise
        except OSError as error:
            if error.errno in {errno.ELOOP, errno.EISDIR, errno.ENXIO}:
                path = self._directory / _LOCKS_DIRECTORY / name
                raise FileExistsError(path) from error
            raise
        try:
            if created:
                os.fchmod(lock_fd, 0o600)
                os.fsync(lock_fd)
                os.fsync(locks_fd)
            self._validate_lock_file(locks_fd, name, lock_fd)
            return lock_fd
        except BaseException:
            os.close(lock_fd)
            raise

    def _validate_lock_file(self, locks_fd: int, name: str, lock_fd: int) -> None:
        opened = os.fstat(lock_fd)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or stat.S_IMODE(opened.st_mode) != 0o600
        ):
            raise FileExistsError(self._directory / _LOCKS_DIRECTORY / name)
        try:
            named = os.stat(name, dir_fd=locks_fd, follow_symlinks=False)
        except OSError as error:
            raise FileExistsError(self._directory / _LOCKS_DIRECTORY / name) from error
        if (
            not stat.S_ISREG(named.st_mode)
            or named.st_nlink != 1
            or stat.S_IMODE(named.st_mode) != 0o600
            or _file_identity(named) != _file_identity(opened)
        ):
            raise FileExistsError(self._directory / _LOCKS_DIRECTORY / name)

    def _validate_root(self, root_fd: int) -> None:
        with SecureRecordDirectory.from_fd(
            self._directory,
            root_fd,
            close_fd=False,
        ):
            pass

    def _require_owned_lease(
        self,
        lease: CollectionWriteLease | CollectionFreezeLease,
    ) -> None:
        if not isinstance(lease, (CollectionWriteLease, CollectionFreezeLease)):
            raise CollectionLeaseError("collection lease is invalid")
        try:
            collection_name = lease.collection_name
        except AttributeError as error:
            raise CollectionLeaseError("collection lease is closed") from error
        lease.require_owned(
            owner_token=self._owner_token,
            collection_name=collection_name,
        )

    def _require_freeze_lease(
        self,
        lease: CollectionFreezeLease,
        collection_name: str,
    ) -> None:
        if not isinstance(lease, CollectionFreezeLease):
            raise CollectionLeaseError("an exclusive freeze lease is required")
        lease.require_owned(
            owner_token=self._owner_token,
            collection_name=collection_name,
            exclusive=True,
        )

    @staticmethod
    def _require_publishable_against(
        intent: CorpusManifestIntent,
        manifests: tuple[CorpusManifest, ...],
    ) -> None:
        CorpusManifestStore._validate_predecessor_reference(intent, manifests)
        predecessor_id = intent.predecessor_manifest_id
        bound = tuple(
            item for item in manifests if item.collection_name == intent.collection_name
        )
        if bound and predecessor_id not in {item.manifest_id for item in bound}:
            raise CorpusPredecessorError(
                "a frozen collection requires an explicit predecessor"
            )

    @staticmethod
    def _validate_predecessor_reference(
        intent: CorpusManifestIntent,
        manifests: tuple[CorpusManifest, ...],
    ) -> None:
        predecessor_id = intent.predecessor_manifest_id
        if predecessor_id is None:
            return
        by_id = {item.manifest_id: item for item in manifests}
        predecessor = by_id.get(predecessor_id)
        if predecessor is None:
            raise CorpusPredecessorError("corpus predecessor does not exist")
        if predecessor.collection_name != intent.collection_name:
            raise CorpusPredecessorError("corpus predecessor names another collection")

    @classmethod
    def _validate_predecessor_graph(
        cls,
        manifests: tuple[CorpusManifest, ...],
    ) -> None:
        for manifest in manifests:
            cls._validate_predecessor_reference(manifest.intent, manifests)

    @staticmethod
    def _decode_canonical(data: bytes, expected_id: str) -> CorpusManifest:
        try:
            raw_manifest = json.loads(
                data,
                parse_constant=_reject_nonstandard_json_constant,
            )
        except (UnicodeDecodeError, ValueError):
            pass
        else:
            if isinstance(raw_manifest, dict) and isinstance(
                raw_manifest.get("schema_version"),
                str,
            ):
                declared = raw_manifest["schema_version"]
                if declared != "corpus-manifest/v1":
                    raise UnsupportedCorpusManifestVersionError(
                        "stored corpus manifest has an unsupported schema version"
                    )
        try:
            manifest = CorpusManifest.model_validate_json(data)
        except ValidationError as error:
            raise ValueError("stored corpus manifest is invalid") from error
        if manifest.manifest_id != expected_id:
            raise ValueError(
                "stored corpus manifest ID does not match its filename"
            )
        if canonical_json(manifest, include_manifest_id=True) != data:
            raise ValueError("stored corpus manifest is not canonical JSON")
        return manifest

    @staticmethod
    def _validate_manifest_id(manifest_id: str) -> None:
        if (
            not isinstance(manifest_id, str)
            or _MANIFEST_ID.fullmatch(manifest_id) is None
        ):
            raise ValueError("manifest_id must be a lowercase SHA-256 digest")

    @staticmethod
    def _validate_collection_name(collection_name: str) -> None:
        if (
            not isinstance(collection_name, str)
            or _COLLECTION_NAME.fullmatch(collection_name) is None
        ):
            raise ValueError("collection name is invalid")
