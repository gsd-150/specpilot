"""Private content-addressed provider response cache.

Cache records may contain source quotations returned by a provider.  They are
therefore treated like restricted manifests: every path is opened without
following symlinks, directories are private, files are create-only and mode
``0600``, and exceptions crossing this boundary carry stable codes only.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
import time
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager, suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from specpilot.contracts.egress import EgressStage
from specpilot.contracts.manifests import Identifier, Sha256
from specpilot.manifests._secure_records import SecureRecordDirectory
from specpilot.providers.base import ProviderResponse

_MAX_RECORD_BYTES = 4 * 1024 * 1024
_MAX_INDEX_BYTES = 1024
_MAX_LOCK_BYTES = 16
_LOCK_TIMEOUT_SECONDS = 5.0


def _canonical(value: BaseModel | dict[str, object]) -> bytes:
    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    return json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class CacheNamespace(_FrozenModel):
    """Deployment-owned identities not carried by an egress request."""

    configuration_hash: Sha256
    prompt_id: Identifier
    prompt_hash: Sha256
    compliance_prompt_hash: Sha256
    verifier_prompt_hash: Sha256
    source_manifest_id: Sha256
    corpus_manifest_id: Sha256

    def bind(self, stage: EgressStage) -> _BoundCacheNamespace:
        stage_prompt_hash = self.prompt_hash
        if stage is EgressStage.COMPLIANCE:
            stage_prompt_hash = self.compliance_prompt_hash
        elif stage in {EgressStage.VERIFIER, EgressStage.JUDGE}:
            stage_prompt_hash = self.verifier_prompt_hash
        return _BoundCacheNamespace(
            configuration_hash=self.configuration_hash,
            prompt_id=self.prompt_id,
            prompt_hash=self.prompt_hash,
            stage_prompt_hash=stage_prompt_hash,
            source_manifest_id=self.source_manifest_id,
            corpus_manifest_id=self.corpus_manifest_id,
        )


class _BoundCacheNamespace(_FrozenModel):
    configuration_hash: Sha256
    prompt_id: Identifier
    prompt_hash: Sha256
    stage_prompt_hash: Sha256
    source_manifest_id: Sha256
    corpus_manifest_id: Sha256


class CacheLinkage(_FrozenModel):
    """Validated retention owners copied from the durable run record."""

    run_id: Identifier
    session_id: Identifier


class CacheKey(_FrozenModel):
    """Complete policy and request identity for exactly one provider response."""

    schema_version: Literal["provider-cache-key/v1"] = "provider-cache-key/v1"
    namespace: _BoundCacheNamespace
    provider_id: Identifier
    model_id: Identifier
    stage: EgressStage
    policy_hash: Sha256
    request_hash: Sha256
    key_hash: Sha256

    @classmethod
    def create(
        cls,
        *,
        namespace: CacheNamespace,
        provider_id: str,
        model_id: str,
        stage: EgressStage,
        policy_hash: str,
        request_hash: str,
    ) -> Self:
        payload: dict[str, object] = {
            "schema_version": "provider-cache-key/v1",
            "namespace": namespace.bind(stage).model_dump(mode="json"),
            "provider_id": provider_id,
            "model_id": model_id,
            "stage": stage.value,
            "policy_hash": policy_hash,
            "request_hash": request_hash,
        }
        return cls.model_validate(
            {**payload, "key_hash": hashlib.sha256(_canonical(payload)).hexdigest()}
        )

    @model_validator(mode="after")
    def _key_hash_matches_content(self) -> Self:
        expected = hashlib.sha256(
            _canonical(self.model_dump(mode="json", exclude={"key_hash"}))
        ).hexdigest()
        if self.key_hash != expected:
            raise ValueError("cache key hash does not match canonical content")
        return self


class CachedProviderResponse(_FrozenModel):
    """One expiring provider response; its hash never includes itself."""

    schema_version: Literal["cached-provider-response/v1"] = (
        "cached-provider-response/v1"
    )
    key: CacheKey
    created_at: datetime
    expires_at: datetime
    response: ProviderResponse
    record_hash: Sha256

    @classmethod
    def create(
        cls,
        *,
        key: CacheKey,
        response: ProviderResponse,
        created_at: datetime,
        ttl_seconds: int,
    ) -> Self:
        created = _aware_utc(created_at)
        payload: dict[str, object] = {
            "schema_version": "cached-provider-response/v1",
            "key": key.model_dump(mode="json"),
            "created_at": _timestamp(created),
            "expires_at": _timestamp(created + timedelta(seconds=ttl_seconds)),
            "response": response.model_dump(mode="json"),
        }
        return cls.model_validate(
            {
                **payload,
                "record_hash": hashlib.sha256(_canonical(payload)).hexdigest(),
            }
        )

    @model_validator(mode="after")
    def _validate_record(self) -> Self:
        if self.expires_at <= self.created_at:
            raise ValueError("cache expiry must follow creation")
        if (
            self.response.provider_id != self.key.provider_id
            or self.response.model_id != self.key.model_id
        ):
            raise ValueError("cached response route does not match cache key")
        expected = hashlib.sha256(
            _canonical(self.model_dump(mode="json", exclude={"record_hash"}))
        ).hexdigest()
        if self.record_hash != expected:
            raise ValueError("cache record hash does not match canonical content")
        return self

    @field_validator("created_at", "expires_at")
    @classmethod
    def _normalize_timestamp(cls, value: datetime) -> datetime:
        return _aware_utc(value)


class _CacheIndexMarker(_FrozenModel):
    key_hash: Sha256
    record_hash: Sha256


class ResponseCacheError(Exception):
    """A stable fail-closed cache error with no path or response content."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class LocalResponseCache:
    """Synchronous local cache used immediately around one provider adapter."""

    def __init__(
        self,
        root: Path,
        *,
        ttl_seconds: int = 7 * 24 * 60 * 60,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if isinstance(ttl_seconds, bool) or ttl_seconds <= 0:
            raise ValueError("cache TTL must be positive")
        self._root = root
        self._ttl_seconds = ttl_seconds
        self._clock = clock or (lambda: datetime.now(UTC))
        try:
            with SecureRecordDirectory.open(root, create=True):
                pass
            for name in ("records", "runs", "sessions", "locks"):
                with SecureRecordDirectory.open(root / name, create=True):
                    pass
        except Exception:
            raise ResponseCacheError("cache_root_invalid") from None

    def get(
        self, key: CacheKey, *, linkage: CacheLinkage | None = None
    ) -> CachedProviderResponse | None:
        try:
            with self._key_lock(key.key_hash):
                cached = self._read_record_locked(key.key_hash)
                if cached is None:
                    return None
                if cached.key != key:
                    raise ValueError("cache record identity mismatch")
                now = _aware_utc(self._clock())
                if cached.expires_at <= now:
                    self._delete_record_locked(key.key_hash)
                    return None
                if linkage is not None:
                    self._associate_locked(cached, linkage)
                return cached
        except ResponseCacheError:
            raise
        except Exception:
            raise ResponseCacheError("cache_record_invalid") from None

    def put(
        self,
        key: CacheKey,
        response: ProviderResponse,
        *,
        linkage: CacheLinkage,
    ) -> CachedProviderResponse:
        try:
            cached = CachedProviderResponse.create(
                key=key,
                response=response,
                created_at=_aware_utc(self._clock()),
                ttl_seconds=self._ttl_seconds,
            )
            data = _canonical(cached)
            if len(data) > _MAX_RECORD_BYTES:
                raise ValueError("cache record too large")
            with self._key_lock(key.key_hash):
                existing = self._read_record_locked(key.key_hash)
                if existing is not None:
                    if existing != cached:
                        raise ValueError("cache key already names another response")
                    self._associate_locked(existing, linkage)
                    return existing
                created: list[tuple[Literal["runs", "sessions"], str]] = []
                try:
                    if self._publish_index(
                        "runs", linkage.run_id, cached, replace_stale=True
                    ):
                        created.append(("runs", linkage.run_id))
                    if self._publish_index(
                        "sessions", linkage.session_id, cached, replace_stale=True
                    ):
                        created.append(("sessions", linkage.session_id))
                    with SecureRecordDirectory.open(
                        self._root / "records", create=False
                    ) as records:
                        stored = records.publish(
                            f"{key.key_hash}.json", data, max_bytes=_MAX_RECORD_BYTES
                        )
                    if stored != data:
                        raise ValueError("cache key already names another response")
                    return cached
                except BaseException:
                    with suppress(BaseException):
                        self._delete_record_locked(key.key_hash)
                    for family, identity in reversed(created):
                        with suppress(BaseException):
                            self._remove_index_locked(family, identity, key.key_hash)
                    raise
        except ResponseCacheError:
            raise
        except Exception:
            raise ResponseCacheError("cache_write_failed") from None

    def delete_run(self, run_id: str) -> int:
        return self._delete_indexed("runs", run_id)

    def delete_session(self, session_id: str) -> int:
        return self._delete_indexed("sessions", session_id)

    def associate(self, key: CacheKey, linkage: CacheLinkage) -> None:
        try:
            with self._key_lock(key.key_hash):
                cached = self._read_record_locked(key.key_hash)
                if cached is None or cached.key != key:
                    raise ValueError("cache record unavailable")
                self._associate_locked(cached, linkage)
        except ResponseCacheError:
            raise
        except Exception:
            raise ResponseCacheError("cache_index_invalid") from None

    def delete_expired(self) -> int:
        try:
            now = _aware_utc(self._clock())
            removed = 0
            with SecureRecordDirectory.open(
                self._root / "records", create=False
            ) as records:
                identifiers = records.content_ids()
            for identifier in identifiers:
                with self._key_lock(identifier):
                    cached = self._read_record_locked(identifier)
                    if (
                        cached is not None
                        and cached.expires_at <= now
                        and self._delete_record_locked(identifier)
                    ):
                        removed += 1
            return removed
        except ResponseCacheError:
            raise
        except Exception:
            raise ResponseCacheError("cache_record_invalid") from None

    def _publish_index(
        self,
        family: Literal["runs", "sessions"],
        identity: str,
        cached: CachedProviderResponse,
        *,
        replace_stale: bool = False,
    ) -> bool:
        index_hash = _identity_hash(identity)
        directory = self._root / family / index_hash
        marker = _canonical(
            {
                "key_hash": cached.key.key_hash,
                "record_hash": cached.record_hash,
            }
        )
        with SecureRecordDirectory.open(directory, create=True) as records:
            try:
                existing = records.read(
                    f"{cached.key.key_hash}.json", max_bytes=_MAX_INDEX_BYTES
                )
            except FileNotFoundError:
                existing = None
            if existing is not None and existing != marker and replace_stale:
                _secure_unlink(
                    records,
                    f"{cached.key.key_hash}.json",
                    max_bytes=_MAX_INDEX_BYTES,
                )
                existing = None
            stored = records.publish(
                f"{cached.key.key_hash}.json", marker, max_bytes=_MAX_INDEX_BYTES
            )
        if stored != marker:
            raise ValueError("cache index mismatch")
        return existing is None

    def _delete_indexed(
        self, family: Literal["runs", "sessions"], identity: str
    ) -> int:
        directory = self._root / family / _identity_hash(identity)
        try:
            with SecureRecordDirectory.open(directory, create=False) as records:
                identifiers = records.content_ids()
        except FileNotFoundError:
            return 0
        except Exception:
            raise ResponseCacheError("cache_index_invalid") from None
        removed = 0
        try:
            with ExitStack() as locks:
                for identifier in sorted(identifiers):
                    locks.enter_context(self._key_lock(identifier))
                for identifier in identifiers:
                    self._validate_index_locked(directory, identifier)
                for identifier in identifiers:
                    if self._delete_record_locked(identifier):
                        removed += 1
                    with SecureRecordDirectory.open(directory, create=False) as records:
                        _secure_unlink(
                            records,
                            f"{identifier}.json",
                            max_bytes=_MAX_INDEX_BYTES,
                        )
            return removed
        except ResponseCacheError:
            raise
        except Exception:
            raise ResponseCacheError("cache_index_invalid") from None

    def _delete_record(self, key_hash: str) -> bool:
        try:
            with self._key_lock(key_hash):
                return self._delete_record_locked(key_hash)
        except FileNotFoundError:
            return False
        except Exception:
            raise ResponseCacheError("cache_record_invalid") from None

    def _delete_record_locked(self, key_hash: str) -> bool:
        with SecureRecordDirectory.open(
            self._root / "records", create=False
        ) as records:
            return _secure_unlink(
                records,
                f"{key_hash}.json",
                max_bytes=_MAX_RECORD_BYTES,
            )

    def _read_record_locked(self, key_hash: str) -> CachedProviderResponse | None:
        with SecureRecordDirectory.open(
            self._root / "records", create=False
        ) as records:
            try:
                data = records.read(f"{key_hash}.json", max_bytes=_MAX_RECORD_BYTES)
            except FileNotFoundError:
                return None
        cached = CachedProviderResponse.model_validate_json(data)
        if cached.key.key_hash != key_hash or data != _canonical(cached):
            raise ValueError("cache record identity mismatch")
        return cached

    def _associate_locked(
        self, cached: CachedProviderResponse, linkage: CacheLinkage
    ) -> None:
        created: list[tuple[Literal["runs", "sessions"], str]] = []
        try:
            if self._publish_index("runs", linkage.run_id, cached):
                created.append(("runs", linkage.run_id))
            if self._publish_index("sessions", linkage.session_id, cached):
                created.append(("sessions", linkage.session_id))
        except BaseException:
            for family, identity in reversed(created):
                with suppress(BaseException):
                    self._remove_index_locked(family, identity, cached.key.key_hash)
            raise

    def _remove_index_locked(
        self,
        family: Literal["runs", "sessions"],
        identity: str,
        key_hash: str,
    ) -> None:
        directory = self._root / family / _identity_hash(identity)
        try:
            with SecureRecordDirectory.open(directory, create=False) as records:
                _secure_unlink(records, f"{key_hash}.json", max_bytes=_MAX_INDEX_BYTES)
        except FileNotFoundError:
            return

    def _validate_index_locked(self, directory: Path, identifier: str) -> None:
        with SecureRecordDirectory.open(directory, create=False) as records:
            marker_data = records.read(f"{identifier}.json", max_bytes=_MAX_INDEX_BYTES)
        marker = _CacheIndexMarker.model_validate_json(marker_data)
        if marker.key_hash != identifier or marker_data != _canonical(marker):
            raise ValueError("cache index identity mismatch")
        cached = self._read_record_locked(identifier)
        if cached is not None and cached.record_hash != marker.record_hash:
            raise ValueError("cache index record mismatch")

    @contextmanager
    def _key_lock(self, key_hash: str) -> Iterator[None]:
        descriptor: int | None = None
        try:
            try:
                with SecureRecordDirectory.open(
                    self._root / "locks", create=False
                ) as locks:
                    name = f"{key_hash}.json"
                    if locks.publish(name, b"{}", max_bytes=_MAX_LOCK_BYTES) != b"{}":
                        raise ValueError("cache lock identity mismatch")
                    descriptor = os.open(
                        name,
                        os.O_RDWR | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0),
                        dir_fd=locks.fd,
                    )
                    opened = os.fstat(descriptor)
                    named = os.stat(name, dir_fd=locks.fd, follow_symlinks=False)
                    if (
                        not stat.S_ISREG(opened.st_mode)
                        or stat.S_IMODE(opened.st_mode) != 0o600
                        or opened.st_nlink != 1
                        or opened.st_size > _MAX_LOCK_BYTES
                        or (opened.st_dev, opened.st_ino)
                        != (named.st_dev, named.st_ino)
                    ):
                        raise ValueError("cache lock identity mismatch")
                    deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
                    while True:
                        try:
                            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                            break
                        except BlockingIOError:
                            if time.monotonic() >= deadline:
                                raise ResponseCacheError("cache_lock_timeout") from None
                            time.sleep(0.01)
            except ResponseCacheError:
                raise
            except Exception:
                raise ResponseCacheError("cache_lock_invalid") from None
            yield
        finally:
            if descriptor is not None:
                with suppress(OSError):
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                with suppress(OSError):
                    os.close(descriptor)


def _identity_hash(value: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("cache index identity must be exact")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("cache clock must be timezone-aware")
    return value.astimezone(UTC)


def _timestamp(value: datetime) -> str:
    return _aware_utc(value).isoformat().replace("+00:00", "Z")


def _secure_unlink(
    records: SecureRecordDirectory,
    name: str,
    *,
    max_bytes: int,
) -> bool:
    try:
        records.read(name, max_bytes=max_bytes)
    except FileNotFoundError:
        return False
    descriptor = os.open(
        name,
        os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0),
        dir_fd=records.fd,
    )
    try:
        opened = os.fstat(descriptor)
        named = os.stat(name, dir_fd=records.fd, follow_symlinks=False)
        if (
            not stat.S_ISREG(opened.st_mode)
            or stat.S_IMODE(opened.st_mode) != 0o600
            or opened.st_nlink != 1
            or opened.st_size > max_bytes
            or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
        ):
            raise FileExistsError(name)
        os.unlink(name, dir_fd=records.fd)
        os.fsync(records.fd)
        return True
    finally:
        os.close(descriptor)


__all__ = [
    "CacheKey",
    "CacheLinkage",
    "CacheNamespace",
    "CachedProviderResponse",
    "LocalResponseCache",
    "ResponseCacheError",
]
