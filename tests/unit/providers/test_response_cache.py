from __future__ import annotations

import hashlib
import json
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from specpilot.contracts.egress import EgressStage
from specpilot.providers.base import ProviderResponse, ResponseMetadata
from specpilot.providers.cache import (
    CacheKey,
    CacheNamespace,
    LocalResponseCache,
    ResponseCacheError,
)

NOW = datetime(2026, 8, 15, 4, tzinfo=UTC)


def _namespace(**changes: str) -> CacheNamespace:
    values = {
        "configuration_hash": "a" * 64,
        "prompt_id": "l1-answer-v1",
        "prompt_hash": "b" * 64,
        "source_manifest_id": "c" * 64,
        "corpus_manifest_id": "d" * 64,
    }
    values.update(changes)
    return CacheNamespace.model_validate(values)


def _key(**changes: object) -> CacheKey:
    values: dict[str, object] = {
        "namespace": _namespace(),
        "provider_id": "provider-a",
        "model_id": "model-a",
        "stage": EgressStage.EVIDENCE,
        "policy_hash": "e" * 64,
        "request_hash": "f" * 64,
    }
    values.update(changes)
    return CacheKey.create(**values)  # type: ignore[arg-type]


def _response(content: str = "fixture reply") -> ProviderResponse:
    return ProviderResponse(
        provider_id="provider-a",
        model_id="model-a",
        content=content,
        metadata=ResponseMetadata(
            prompt_tokens=3,
            completion_tokens=2,
            finish_reason="stop",
            duration_ms=4,
            request_bytes=25,
        ),
    )


@pytest.mark.parametrize(
    "changed",
    [
        {"provider_id": "provider-b"},
        {"model_id": "model-b"},
        {"stage": EgressStage.PLANNING},
        {"policy_hash": "0" * 64},
        {"request_hash": "1" * 64},
        {"namespace": _namespace(prompt_id="l1-answer-v2")},
        {"namespace": _namespace(prompt_hash="2" * 64)},
        {"namespace": _namespace(configuration_hash="3" * 64)},
        {"namespace": _namespace(source_manifest_id="4" * 64)},
        {"namespace": _namespace(corpus_manifest_id="5" * 64)},
    ],
)
def test_cache_key_changes_for_every_policy_namespace_dimension(
    changed: dict[str, object],
) -> None:
    assert _key(**changed).key_hash != _key().key_hash


def test_cache_round_trip_uses_private_atomic_records(tmp_path: Path) -> None:
    root = tmp_path / "cache"
    cache = LocalResponseCache(root, ttl_seconds=60, clock=lambda: NOW)

    stored = cache.put(_key(), _response(), run_id="run/raw", session_id="session/raw")

    assert cache.get(_key()) == stored
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    record = root / "records" / f"{_key().key_hash}.json"
    assert stat.S_IMODE(record.stat().st_mode) == 0o600
    assert not any("run/raw" in str(item) for item in root.rglob("*"))
    assert not any("session/raw" in str(item) for item in root.rglob("*"))
    run_index = hashlib.sha256(b"run/raw").hexdigest()
    session_index = hashlib.sha256(b"session/raw").hexdigest()
    assert (root / "runs" / run_index).is_dir()
    assert (root / "sessions" / session_index).is_dir()


def test_expired_record_is_unavailable_and_deleted(tmp_path: Path) -> None:
    current = NOW
    cache = LocalResponseCache(
        tmp_path / "cache", ttl_seconds=60, clock=lambda: current
    )
    cache.put(_key(), _response(), run_id="run-a", session_id="session-a")
    current += timedelta(seconds=61)

    assert cache.get(_key()) is None
    assert cache.delete_expired() == 0


def test_corrupt_record_fails_closed_without_exposing_content(tmp_path: Path) -> None:
    cache = LocalResponseCache(tmp_path / "cache", ttl_seconds=60, clock=lambda: NOW)
    cache.put(_key(), _response(), run_id="run-a", session_id="session-a")
    record = tmp_path / "cache" / "records" / f"{_key().key_hash}.json"
    record.write_text(json.dumps({"content": "SECRET"}))
    record.chmod(0o600)

    with pytest.raises(ResponseCacheError) as caught:
        cache.get(_key())

    assert caught.value.code == "cache_record_invalid"
    assert "SECRET" not in str(caught.value)
    assert str(tmp_path) not in str(caught.value)


def test_symlink_record_is_never_followed(tmp_path: Path) -> None:
    cache = LocalResponseCache(tmp_path / "cache", ttl_seconds=60, clock=lambda: NOW)
    cache.put(_key(), _response(), run_id="run-a", session_id="session-a")
    record = tmp_path / "cache" / "records" / f"{_key().key_hash}.json"
    record.unlink()
    target = tmp_path / "target"
    target.write_text("SECRET")
    record.symlink_to(target)

    with pytest.raises(ResponseCacheError) as caught:
        cache.get(_key())

    assert caught.value.code == "cache_record_invalid"


def test_hashed_run_and_session_indexes_delete_associated_records(
    tmp_path: Path,
) -> None:
    cache = LocalResponseCache(tmp_path / "cache", ttl_seconds=60, clock=lambda: NOW)
    first = _key(request_hash="1" * 64)
    second = _key(request_hash="2" * 64)
    cache.put(first, _response("first"), run_id="run-a", session_id="session-a")
    cache.put(second, _response("second"), run_id="run-b", session_id="session-a")

    assert cache.delete_run("run-a") == 1
    assert cache.get(first) is None
    assert cache.get(second) is not None
    assert cache.delete_session("session-a") == 1
    assert cache.get(second) is None


def test_corrupt_index_fails_closed_before_deleting_records(tmp_path: Path) -> None:
    cache = LocalResponseCache(tmp_path / "cache", ttl_seconds=60, clock=lambda: NOW)
    key = _key()
    cache.put(key, _response(), run_id="run-a", session_id="session-a")
    run_index = hashlib.sha256(b"run-a").hexdigest()
    marker = tmp_path / "cache" / "runs" / run_index / f"{key.key_hash}.json"
    marker.write_text(json.dumps({"key_hash": "0" * 64, "record_hash": "1" * 64}))
    marker.chmod(0o600)

    with pytest.raises(ResponseCacheError) as caught:
        cache.delete_run("run-a")

    assert caught.value.code == "cache_index_invalid"
    assert cache.get(key) is not None


def test_cache_rejects_nonpositive_ttl_and_symlink_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="positive"):
        LocalResponseCache(tmp_path / "cache", ttl_seconds=0)
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    alias = tmp_path / "alias"
    alias.symlink_to(target, target_is_directory=True)
    with pytest.raises(ResponseCacheError) as caught:
        LocalResponseCache(alias, ttl_seconds=60)
    assert caught.value.code == "cache_root_invalid"
