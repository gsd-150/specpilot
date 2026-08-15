from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import os
import re
import stat
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Never, Self, SupportsIndex, cast, final

from pydantic import ValidationError

from specpilot.contracts.corpus_manifest import (
    CorpusManifest,
    CorpusManifestDraft,
    CorpusManifestIntent,
)
from specpilot.ingestion._secure_fs import (
    directory_open_flags,
    open_directory_path,
    revalidate_directory_path,
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


@dataclass(frozen=True, slots=True)
class _LeaseNamespace:
    directory: Path
    collection_name: str
    lock_name: str
    locks_identity: _FileIdentity
    lock_identity: _FileIdentity


@dataclass(slots=True)
class _LeaseState:
    condition: threading.Condition = field(
        default_factory=threading.Condition,
        repr=False,
    )
    active_operations: int = 0
    holders: dict[int, int] = field(default_factory=dict, repr=False)
    closing: bool = False
    closed: bool = False


@dataclass(slots=True, init=False)
class _CollectionLease:
    _namespace: _LeaseNamespace = field(repr=False)
    _issue_token: object = field(repr=False)
    _owner_token: object = field(repr=False)
    _root_fd: int = field(repr=False)
    _lock_fd: int = field(repr=False)
    _exclusive: bool
    _state: _LeaseState = field(repr=False)

    def __init__(self) -> None:
        raise TypeError("collection leases are issued by CorpusManifestStore")

    @classmethod
    def _issue(
        cls,
        *,
        issue_token: object,
        directory: Path,
        collection_name: str,
        owner_token: object,
        root_fd: int,
        lock_fd: int,
        lock_name: str,
        locks_identity: _FileIdentity,
        lock_identity: _FileIdentity,
        exclusive: bool,
    ) -> Self:
        if issue_token is not _LEASE_ISSUER:
            raise CollectionLeaseError("collection lease issuer is invalid")
        lease = object.__new__(cls)
        lease._namespace = _LeaseNamespace(
            directory=directory,
            collection_name=collection_name,
            lock_name=lock_name,
            locks_identity=locks_identity,
            lock_identity=lock_identity,
        )
        lease._issue_token = issue_token
        lease._owner_token = owner_token
        lease._root_fd = root_fd
        lease._lock_fd = lock_fd
        lease._exclusive = exclusive
        lease._state = _LeaseState()
        return lease

    def __enter__(self) -> Self:
        self.require_active_for(self._namespace.collection_name)
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
        self.require_active_for(self._namespace.collection_name)
        return self._namespace.collection_name

    @property
    def root_fd(self) -> int:
        self.require_active_for(self._namespace.collection_name)
        return self._root_fd

    def require_active_for(self, collection_name: str) -> None:
        """Check this instant only; mutations must use ``active_operation``."""
        _CollectionLease._require_active_state(self, collection_name)

    @contextmanager
    def active_operation(self, collection_name: str) -> Iterator[None]:
        """Keep lease authority for a complete mutation or admin operation.

        Task 5 dense mutation and snapshot administration must run inside this
        guard rather than treating ``require_active_for`` as a lasting grant.
        """
        lease_type = type(self)
        if lease_type is CollectionWriteLease:
            exclusive = False
        elif lease_type is CollectionFreezeLease:
            exclusive = True
        else:
            raise CollectionLeaseError("collection lease is invalid")
        with _CollectionLease._operation_scope(
            self,
            collection_name,
            exclusive=exclusive,
        ):
            yield

    def _require_active_state(
        self,
        collection_name: str,
        *,
        owner_token: object | None = None,
        validate_owner: bool = False,
        exclusive: bool | None = None,
    ) -> None:
        try:
            state = self._state
            bound_collection = self._namespace.collection_name
        except AttributeError as error:
            raise CollectionLeaseError("collection lease is closed") from error
        thread_id = threading.get_ident()
        with state.condition:
            _CollectionLease._validate_active_locked(
                self,
                state,
                thread_id=thread_id,
                bound_collection=bound_collection,
                collection_name=collection_name,
                owner_token=owner_token,
                validate_owner=validate_owner,
                exclusive=exclusive,
            )

    def _validate_active_locked(
        self,
        state: _LeaseState,
        *,
        thread_id: int,
        bound_collection: str,
        collection_name: str,
        owner_token: object | None,
        validate_owner: bool,
        exclusive: bool | None,
    ) -> None:
        if self._issue_token is not _LEASE_ISSUER or state.closed:
            raise CollectionLeaseError("collection lease is closed")
        if state.closing and state.holders.get(thread_id, 0) == 0:
            raise CollectionLeaseError("collection lease is closing")
        if bound_collection != collection_name:
            raise CollectionLeaseError("collection lease names another collection")
        if validate_owner and self._owner_token is not owner_token:
            raise CollectionLeaseError("collection lease is not active for this store")
        if exclusive is not None and self._exclusive is not exclusive:
            raise CollectionLeaseError("collection lease has the wrong mode")
        _CollectionLease._validate_namespace(self)

    @contextmanager
    def _operation_scope(
        self,
        collection_name: str,
        *,
        owner_token: object | None = None,
        validate_owner: bool = False,
        exclusive: bool | None = None,
    ) -> Iterator[None]:
        try:
            state = self._state
            bound_collection = self._namespace.collection_name
        except AttributeError as error:
            raise CollectionLeaseError("collection lease is closed") from error
        thread_id = threading.get_ident()
        with state.condition:
            _CollectionLease._validate_active_locked(
                self,
                state,
                thread_id=thread_id,
                bound_collection=bound_collection,
                collection_name=collection_name,
                owner_token=owner_token,
                validate_owner=validate_owner,
                exclusive=exclusive,
            )
            state.active_operations += 1
            state.holders[thread_id] = state.holders.get(thread_id, 0) + 1
        try:
            yield
        finally:
            with state.condition:
                depth = state.holders[thread_id] - 1
                if depth:
                    state.holders[thread_id] = depth
                else:
                    del state.holders[thread_id]
                state.active_operations -= 1
                if state.active_operations == 0:
                    state.condition.notify_all()

    def _validate_namespace(self) -> None:
        namespace = self._namespace
        locks_fd: int | None = None
        failure: Exception | None = None
        try:
            root_status = os.fstat(self._root_fd)
            if (
                not stat.S_ISDIR(root_status.st_mode)
                or stat.S_IMODE(root_status.st_mode) != 0o700
            ):
                raise FileExistsError(namespace.directory)
            revalidate_directory_path(namespace.directory, self._root_fd)
            locks_fd = os.open(
                _LOCKS_DIRECTORY,
                directory_open_flags(),
                dir_fd=self._root_fd,
            )
            locks_status = os.fstat(locks_fd)
            named_locks = os.stat(
                _LOCKS_DIRECTORY,
                dir_fd=self._root_fd,
                follow_symlinks=False,
            )
            if (
                not stat.S_ISDIR(locks_status.st_mode)
                or stat.S_IMODE(locks_status.st_mode) != 0o700
                or _file_identity(locks_status) != namespace.locks_identity
                or not stat.S_ISDIR(named_locks.st_mode)
                or stat.S_IMODE(named_locks.st_mode) != 0o700
                or _file_identity(named_locks) != namespace.locks_identity
            ):
                raise FileExistsError(namespace.directory / _LOCKS_DIRECTORY)

            lock_status = os.fstat(self._lock_fd)
            named_lock = os.stat(
                namespace.lock_name,
                dir_fd=locks_fd,
                follow_symlinks=False,
            )
            if (
                not stat.S_ISREG(lock_status.st_mode)
                or lock_status.st_nlink != 1
                or stat.S_IMODE(lock_status.st_mode) != 0o600
                or _file_identity(lock_status) != namespace.lock_identity
                or not stat.S_ISREG(named_lock.st_mode)
                or named_lock.st_nlink != 1
                or stat.S_IMODE(named_lock.st_mode) != 0o600
                or _file_identity(named_lock) != namespace.lock_identity
            ):
                path = namespace.directory / _LOCKS_DIRECTORY / namespace.lock_name
                raise FileExistsError(path)
        except Exception as error:
            failure = error
        finally:
            if locks_fd is not None:
                try:
                    os.close(locks_fd)
                except Exception as error:
                    if failure is None:
                        failure = error
        if failure is not None:
            raise CollectionLeaseError(
                "collection lease namespace changed"
            ) from failure

    def require_owned(
        self,
        *,
        owner_token: object,
        collection_name: str,
        exclusive: bool | None = None,
    ) -> None:
        _CollectionLease._require_active_state(
            self,
            collection_name,
            owner_token=owner_token,
            validate_owner=True,
            exclusive=exclusive,
        )

    def close(self) -> None:
        errors: list[BaseException] = []
        try:
            if self._issue_token is not _LEASE_ISSUER:
                raise CollectionLeaseError("collection lease is closed")
            state = self._state
        except AttributeError as error:
            raise CollectionLeaseError("collection lease is closed") from error
        thread_id = threading.get_ident()
        with state.condition:
            if state.holders.get(thread_id, 0):
                raise CollectionLeaseError(
                    "collection lease cannot close inside an active operation"
                )
            if state.closed:
                return
            if state.closing:
                while not state.closed:
                    state.condition.wait()
                return
            state.closing = True
            state.condition.notify_all()
            # A published close leader must not orphan ``closing``. Defer
            # control-flow exceptions until active work drains and cleanup ends.
            while state.active_operations:
                try:
                    state.condition.wait()
                except BaseException as error:
                    errors.append(error)
            lock_fd = self._lock_fd
            root_fd = self._root_fd
            self._lock_fd = -1
            self._root_fd = -1
        try:
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
        finally:
            with state.condition:
                state.closed = True
                state.condition.notify_all()
        _raise_first(errors)


@final
@dataclass(slots=True, init=False)
class CollectionWriteLease(_CollectionLease):
    """A live shared lease authorizing one collection writer lifetime."""

    def require_active_for(self, collection_name: str) -> None:
        _CollectionLease._require_active_state(
            self,
            collection_name,
            exclusive=False,
        )

    def __init_subclass__(cls, **kwargs: object) -> Never:
        del cls, kwargs
        raise TypeError("CollectionWriteLease cannot be subclassed")


@final
@dataclass(slots=True, init=False)
class CollectionFreezeLease(_CollectionLease):
    """A live exclusive lease authorizing collection freeze publication."""

    def require_active_for(self, collection_name: str) -> None:
        _CollectionLease._require_active_state(
            self,
            collection_name,
            exclusive=True,
        )

    def __init_subclass__(cls, **kwargs: object) -> Never:
        del cls, kwargs
        raise TypeError("CollectionFreezeLease cannot be subclassed")


def _reject_nonstandard_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


class CorpusManifestStore:
    """Immutable corpus manifests and the durable frozen-collection registry."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory
        self._root_fd: int | None = None
        self._owner_token = object()

    @classmethod
    def from_fd(cls, directory: Path, root_fd: int) -> Self:
        """Bind all record and lease operations to one borrowed root descriptor."""
        with SecureRecordDirectory.from_fd(
            directory,
            root_fd,
            close_fd=False,
        ):
            pass
        store = cls(directory)
        store._root_fd = root_fd
        return store

    def read(self, manifest_id: str) -> CorpusManifest:
        self._validate_manifest_id(manifest_id)
        manifests = self.read_all()
        for manifest in manifests:
            if manifest.manifest_id == manifest_id:
                return manifest
        raise FileNotFoundError(self._directory / f"{manifest_id}.json")

    def read_all(self) -> tuple[CorpusManifest, ...]:
        if self._root_fd is None:
            records = SecureRecordDirectory.open(self._directory, create=False)
        else:
            records = SecureRecordDirectory.from_fd(
                self._directory,
                self._root_fd,
                close_fd=False,
            )
        with records:
            return self._decode_all(records)

    def find_by_intent(
        self,
        intent: CorpusManifestIntent,
        *,
        lease: CollectionFreezeLease,
    ) -> CorpusManifest | None:
        with self._owned_operation(
            lease,
            collection_name=intent.collection_name,
            exclusive=True,
        ):
            manifests = self._read_all_under(lease)
            self._validate_predecessor_reference(intent, manifests)
            matches = tuple(item for item in manifests if item.intent == intent)
            if len(matches) > 1:
                raise CorpusManifestIntentConflictError(
                    "intent already has multiple corpus manifests"
                )
            return matches[0] if matches else None

    def has_collection_binding(
        self,
        collection_name: str,
        *,
        lease: CollectionFreezeLease,
    ) -> bool:
        """Check the durable frozen registry under this store's owned lease."""
        self._validate_collection_name(collection_name)
        with self._owned_operation(
            lease,
            collection_name=collection_name,
            exclusive=True,
        ):
            return any(
                manifest.collection_name == collection_name
                for manifest in self._read_all_under(lease)
            )

    def require_publishable_intent(
        self,
        intent: CorpusManifestIntent,
        *,
        lease: CollectionFreezeLease,
    ) -> None:
        with self._owned_operation(
            lease,
            collection_name=intent.collection_name,
            exclusive=True,
        ):
            manifests = self._read_all_under(lease)
            self._require_publishable_against(intent, manifests)

    def create(
        self,
        draft: CorpusManifestDraft,
        *,
        lease: CollectionFreezeLease,
    ) -> CorpusManifest:
        with self._owned_operation(
            lease,
            collection_name=draft.collection_name,
            exclusive=True,
        ):
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

    @contextmanager
    def write_operation(
        self,
        lease: CollectionWriteLease,
        collection_name: str,
    ) -> Iterator[None]:
        """Keep an owned shared lease live for one complete write operation."""
        if type(lease) is not CollectionWriteLease:
            raise CollectionLeaseError("an owned write lease is required")
        with CorpusManifestStore._owned_operation(
            self,
            lease,
            collection_name=collection_name,
            exclusive=False,
        ):
            yield

    @contextmanager
    def freeze_operation(
        self,
        lease: CollectionFreezeLease,
        collection_name: str,
    ) -> Iterator[None]:
        """Keep an owned exclusive lease live for one complete admin operation."""
        if type(lease) is not CollectionFreezeLease:
            raise CollectionLeaseError("an owned freeze lease is required")
        with CorpusManifestStore._owned_operation(
            self,
            lease,
            collection_name=collection_name,
            exclusive=True,
        ):
            yield

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
            if self._root_fd is None:
                root_fd = open_directory_path(self._directory, create=True)
                os.fchmod(root_fd, 0o700)
            else:
                root_fd = os.dup(self._root_fd)
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
            lock_identity = _file_identity(os.fstat(lock_fd))
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
                directory=self._directory,
                collection_name=collection_name,
                owner_token=self._owner_token,
                root_fd=root_fd,
                lock_fd=lock_fd,
                lock_name=lock_name,
                locks_identity=locks_identity,
                lock_identity=lock_identity,
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
        if type(lease) not in {CollectionWriteLease, CollectionFreezeLease}:
            raise CollectionLeaseError("collection lease is invalid")
        try:
            collection_name = lease._namespace.collection_name
        except AttributeError as error:
            raise CollectionLeaseError("collection lease is closed") from error
        with (
            self._owned_operation(
                lease,
                collection_name=collection_name,
            ),
            SecureRecordDirectory.from_fd(
                self._directory,
                lease._root_fd,
                close_fd=False,
            ) as records,
        ):
            return self._decode_all(
                records,
                expected_locks_identity=lease._namespace.locks_identity,
            )

    def _publish_under(
        self,
        manifest: CorpusManifest,
        *,
        lease: CollectionFreezeLease,
    ) -> CorpusManifest:
        data = canonical_json(manifest, include_manifest_id=True)
        if len(data) > _MAX_MANIFEST_BYTES:
            raise ValueError("corpus manifest exceeds maximum storage size")
        destination_name = f"{manifest.manifest_id}.json"
        with self._owned_operation(
            lease,
            collection_name=manifest.collection_name,
            exclusive=True,
        ):
            with SecureRecordDirectory.from_fd(
                self._directory,
                lease._root_fd,
                close_fd=False,
            ) as records:
                manifests = self._decode_all(
                    records,
                    expected_locks_identity=lease._namespace.locks_identity,
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
                    expected_locks_identity=lease._namespace.locks_identity,
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
        if type(lease) not in {CollectionWriteLease, CollectionFreezeLease}:
            raise CollectionLeaseError("collection lease is invalid")
        try:
            collection_name = lease._namespace.collection_name
        except AttributeError as error:
            raise CollectionLeaseError("collection lease is closed") from error
        _CollectionLease.require_owned(
            lease,
            owner_token=self._owner_token,
            collection_name=collection_name,
        )

    @contextmanager
    def _owned_operation(
        self,
        lease: CollectionWriteLease | CollectionFreezeLease,
        *,
        collection_name: str,
        exclusive: bool | None = None,
    ) -> Iterator[None]:
        if type(lease) not in {CollectionWriteLease, CollectionFreezeLease}:
            raise CollectionLeaseError("collection lease is invalid")
        with _CollectionLease._operation_scope(
            lease,
            collection_name,
            owner_token=self._owner_token,
            validate_owner=True,
            exclusive=exclusive,
        ):
            yield

    def _require_freeze_lease(
        self,
        lease: CollectionFreezeLease,
        collection_name: str,
    ) -> None:
        if type(lease) is not CollectionFreezeLease:
            raise CollectionLeaseError("an exclusive freeze lease is required")
        _CollectionLease.require_owned(
            lease,
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
