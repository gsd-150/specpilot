"""Private content-addressed provider response cache.

Cache records may contain source quotations returned by a provider.  They are
therefore treated like restricted manifests: every path is opened without
following symlinks, directories are private, files are create-only and mode
``0600``, and exceptions crossing this boundary carry stable codes only.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Callable
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
    source_manifest_id: Sha256
    corpus_manifest_id: Sha256


class CacheKey(_FrozenModel):
    """Complete policy and request identity for exactly one provider response."""

    schema_version: Literal["provider-cache-key/v1"] = "provider-cache-key/v1"
    namespace: CacheNamespace
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
            "namespace": namespace.model_dump(mode="json"),
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
            for name in ("records", "runs", "sessions"):
                with SecureRecordDirectory.open(root / name, create=True):
                    pass
        except Exception:
            raise ResponseCacheError("cache_root_invalid") from None

    def get(self, key: CacheKey) -> CachedProviderResponse | None:
        name = f"{key.key_hash}.json"
        try:
            with SecureRecordDirectory.open(
                self._root / "records", create=False
            ) as records:
                try:
                    data = records.read(name, max_bytes=_MAX_RECORD_BYTES)
                except FileNotFoundError:
                    return None
            cached = CachedProviderResponse.model_validate_json(data)
            if cached.key != key or data != _canonical(cached):
                raise ValueError("cache record identity mismatch")
            now = _aware_utc(self._clock())
            if cached.expires_at <= now:
                self._delete_record(key.key_hash)
                return None
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
        run_id: str,
        session_id: str,
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
            with SecureRecordDirectory.open(
                self._root / "records", create=False
            ) as records:
                stored = records.publish(
                    f"{key.key_hash}.json", data, max_bytes=_MAX_RECORD_BYTES
                )
            if stored != data:
                raise ValueError("cache key already names another response")
            self._publish_index("runs", run_id, cached)
            self._publish_index("sessions", session_id, cached)
            return cached
        except ResponseCacheError:
            raise
        except Exception:
            raise ResponseCacheError("cache_write_failed") from None

    def delete_run(self, run_id: str) -> int:
        return self._delete_indexed("runs", run_id)

    def delete_session(self, session_id: str) -> int:
        return self._delete_indexed("sessions", session_id)

    def delete_expired(self) -> int:
        try:
            now = _aware_utc(self._clock())
            removed = 0
            with SecureRecordDirectory.open(
                self._root / "records", create=False
            ) as records:
                identifiers = records.content_ids()
            for identifier in identifiers:
                key_name = f"{identifier}.json"
                with SecureRecordDirectory.open(
                    self._root / "records", create=False
                ) as records:
                    data = records.read(key_name, max_bytes=_MAX_RECORD_BYTES)
                cached = CachedProviderResponse.model_validate_json(data)
                if cached.key.key_hash != identifier or data != _canonical(cached):
                    raise ValueError("cache record identity mismatch")
                if cached.expires_at <= now and self._delete_record(identifier):
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
    ) -> None:
        index_hash = _identity_hash(identity)
        directory = self._root / family / index_hash
        marker = _canonical(
            {
                "key_hash": cached.key.key_hash,
                "record_hash": cached.record_hash,
            }
        )
        with SecureRecordDirectory.open(directory, create=True) as records:
            stored = records.publish(
                f"{cached.key.key_hash}.json", marker, max_bytes=_MAX_INDEX_BYTES
            )
        if stored != marker:
            raise ValueError("cache index mismatch")

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
            validated: list[str] = []
            for identifier in identifiers:
                with SecureRecordDirectory.open(directory, create=False) as records:
                    marker_data = records.read(
                        f"{identifier}.json", max_bytes=_MAX_INDEX_BYTES
                    )
                marker = _CacheIndexMarker.model_validate_json(marker_data)
                if marker.key_hash != identifier or marker_data != _canonical(marker):
                    raise ValueError("cache index identity mismatch")
                try:
                    with SecureRecordDirectory.open(
                        self._root / "records", create=False
                    ) as records:
                        record_data = records.read(
                            f"{identifier}.json", max_bytes=_MAX_RECORD_BYTES
                        )
                except FileNotFoundError:
                    pass
                else:
                    cached = CachedProviderResponse.model_validate_json(record_data)
                    if (
                        cached.key.key_hash != identifier
                        or cached.record_hash != marker.record_hash
                        or record_data != _canonical(cached)
                    ):
                        raise ValueError("cache index record mismatch")
                validated.append(identifier)
            for identifier in validated:
                if self._delete_record(identifier):
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
            with SecureRecordDirectory.open(
                self._root / "records", create=False
            ) as records:
                return _secure_unlink(
                    records,
                    f"{key_hash}.json",
                    max_bytes=_MAX_RECORD_BYTES,
                )
        except FileNotFoundError:
            return False
        except Exception:
            raise ResponseCacheError("cache_record_invalid") from None


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
    "CacheNamespace",
    "CachedProviderResponse",
    "LocalResponseCache",
    "ResponseCacheError",
]
