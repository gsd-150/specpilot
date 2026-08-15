from __future__ import annotations

import hashlib
import json
import stat
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from specpilot.contracts.egress import EgressStage
from specpilot.providers.base import ProviderResponse, ResponseMetadata
from specpilot.providers.cache import (
    CacheKey,
    CacheLinkage,
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
        "compliance_prompt_hash": "6" * 64,
        "verifier_prompt_hash": "7" * 64,
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


@pytest.mark.parametrize(
    ("stage", "namespace_change"),
    [
        (EgressStage.COMPLIANCE, {"compliance_prompt_hash": "8" * 64}),
        (EgressStage.VERIFIER, {"verifier_prompt_hash": "9" * 64}),
    ],
)
def test_cache_key_binds_the_prompt_hash_for_its_stage(
    stage: EgressStage, namespace_change: dict[str, str]
) -> None:
    assert (
        _key(stage=stage, namespace=_namespace(**namespace_change)).key_hash
        != _key(stage=stage).key_hash
    )


def test_unrelated_stage_prompt_hash_does_not_invalidate_planning() -> None:
    changed = _namespace(compliance_prompt_hash="8" * 64, verifier_prompt_hash="9" * 64)
    assert (
        _key(stage=EgressStage.PLANNING, namespace=changed).key_hash
        == _key(stage=EgressStage.PLANNING).key_hash
    )


def test_cache_round_trip_uses_private_atomic_records(tmp_path: Path) -> None:
    root = tmp_path / "cache"
    cache = LocalResponseCache(root, ttl_seconds=60, clock=lambda: NOW)

    stored = cache.put(
        _key(),
        _response(),
        linkage=CacheLinkage(run_id="run/raw", session_id="session/raw"),
    )

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
    cache.put(
        _key(),
        _response(),
        linkage=CacheLinkage(run_id="run-a", session_id="session-a"),
    )
    current += timedelta(seconds=61)

    assert cache.get(_key()) is None
    assert cache.delete_expired() == 0


def test_corrupt_record_fails_closed_without_exposing_content(tmp_path: Path) -> None:
    cache = LocalResponseCache(tmp_path / "cache", ttl_seconds=60, clock=lambda: NOW)
    cache.put(
        _key(),
        _response(),
        linkage=CacheLinkage(run_id="run-a", session_id="session-a"),
    )
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
    cache.put(
        _key(),
        _response(),
        linkage=CacheLinkage(run_id="run-a", session_id="session-a"),
    )
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
    cache.put(
        first,
        _response("first"),
        linkage=CacheLinkage(run_id="run-a", session_id="session-a"),
    )
    cache.put(
        second,
        _response("second"),
        linkage=CacheLinkage(run_id="run-b", session_id="session-a"),
    )

    assert cache.delete_run("run-a") == 1
    assert cache.get(first) is None
    assert cache.get(second) is not None
    assert cache.delete_session("session-a") == 1
    assert cache.get(second) is None


def test_hit_associates_reusing_run_and_session_before_return(tmp_path: Path) -> None:
    cache = LocalResponseCache(tmp_path / "cache", ttl_seconds=60, clock=lambda: NOW)
    key = _key()
    cache.put(
        key,
        _response(),
        linkage=CacheLinkage(run_id="run-a", session_id="session-a"),
    )

    assert (
        cache.get(key, linkage=CacheLinkage(run_id="run-b", session_id="session-b"))
        is not None
    )
    assert cache.delete_run("run-b") == 1
    assert cache.get(key) is None


def test_deleted_shared_record_can_be_republished_over_stale_other_indexes(
    tmp_path: Path,
) -> None:
    current = NOW
    cache = LocalResponseCache(
        tmp_path / "cache", ttl_seconds=60, clock=lambda: current
    )
    key = _key()
    cache.put(
        key,
        _response(),
        linkage=CacheLinkage(run_id="run-a", session_id="session-shared"),
    )
    assert cache.delete_run("run-a") == 1
    current += timedelta(seconds=1)

    republished = cache.put(
        key,
        _response(),
        linkage=CacheLinkage(run_id="run-b", session_id="session-shared"),
    )

    assert cache.get(key) == republished


def test_index_failure_never_publishes_a_reusable_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = LocalResponseCache(tmp_path / "cache", ttl_seconds=60, clock=lambda: NOW)
    original = cache._publish_index
    calls = 0

    def fail_second(*args: object, **kwargs: object) -> bool:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected index fault")
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(cache, "_publish_index", fail_second)
    with pytest.raises(ResponseCacheError, match="cache_write_failed"):
        cache.put(
            _key(),
            _response(),
            linkage=CacheLinkage(run_id="run-a", session_id="session-a"),
        )

    assert cache.get(_key()) is None
    assert not (tmp_path / "cache" / "records" / f"{_key().key_hash}.json").exists()


def test_publication_rolls_back_and_releases_lock_on_cancellation_shaped_fault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class CancelSignal(BaseException):
        pass

    cache = LocalResponseCache(tmp_path / "cache", ttl_seconds=60, clock=lambda: NOW)
    original = cache._publish_index

    def interrupt_second(*args: object, **kwargs: object) -> bool:
        if args[0] == "sessions":
            raise CancelSignal()
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(cache, "_publish_index", interrupt_second)
    with pytest.raises(CancelSignal):
        cache.put(
            _key(),
            _response(),
            linkage=CacheLinkage(run_id="run-a", session_id="session-a"),
        )
    assert cache.get(_key()) is None


def test_hit_association_fault_fails_closed_and_rolls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = LocalResponseCache(tmp_path / "cache", ttl_seconds=60, clock=lambda: NOW)
    key = _key()
    cache.put(
        key,
        _response(),
        linkage=CacheLinkage(run_id="run-a", session_id="session-a"),
    )
    original = cache._publish_index

    def fail_session(
        family: object, identity: object, cached: object, **kwargs: object
    ) -> bool:
        if family == "sessions" and identity == "session-b":
            raise OSError("injected association fault")
        return original(family, identity, cached, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(cache, "_publish_index", fail_session)
    with pytest.raises(ResponseCacheError):
        cache.get(key, linkage=CacheLinkage(run_id="run-b", session_id="session-b"))

    assert cache.delete_run("run-b") == 0
    assert cache.get(key) is not None


def test_delete_cannot_unlink_a_new_record_after_validating_the_old_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import specpilot.providers.cache as cache_module

    cache = LocalResponseCache(tmp_path / "cache", ttl_seconds=60, clock=lambda: NOW)
    key = _key()
    first = CacheLinkage(run_id="run-a", session_id="session-a")
    second = CacheLinkage(run_id="run-b", session_id="session-b")
    cache.put(key, _response("old"), linkage=first)
    reached_unlink = threading.Event()
    allow_unlink = threading.Event()
    original_unlink = cache_module._secure_unlink

    def paused_unlink(*args: object, **kwargs: object) -> bool:
        if kwargs.get("max_bytes") == cache_module._MAX_RECORD_BYTES:
            reached_unlink.set()
            assert allow_unlink.wait(timeout=2)
        return original_unlink(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(cache_module, "_secure_unlink", paused_unlink)
    deleted: list[int] = []
    published: list[object] = []
    delete_thread = threading.Thread(
        target=lambda: deleted.append(cache.delete_run("run-a"))
    )
    delete_thread.start()
    assert reached_unlink.wait(timeout=2)
    put_thread = threading.Thread(
        target=lambda: published.append(
            cache.put(key, _response("new"), linkage=second)
        )
    )
    put_thread.start()
    put_thread.join(timeout=0.05)
    assert put_thread.is_alive()
    allow_unlink.set()
    delete_thread.join(timeout=2)
    put_thread.join(timeout=2)

    assert deleted == [1]
    assert len(published) == 1
    assert cache.get(key).response.content == "new"  # type: ignore[union-attr]


def test_key_lock_refuses_symlink_and_insecure_mode(tmp_path: Path) -> None:
    cache = LocalResponseCache(tmp_path / "cache", ttl_seconds=60, clock=lambda: NOW)
    key = _key()
    lock = tmp_path / "cache" / "locks" / f"{key.key_hash}.json"
    target = tmp_path / "target"
    target.write_text("{}")
    lock.symlink_to(target)
    with pytest.raises(ResponseCacheError, match="cache_lock_invalid"):
        cache.get(key)
    lock.unlink()
    lock.write_text("{}")
    lock.chmod(0o644)
    with pytest.raises(ResponseCacheError, match="cache_lock_invalid"):
        cache.get(key)


def test_key_lock_contention_has_a_hard_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import specpilot.providers.cache as cache_module

    cache = LocalResponseCache(tmp_path / "cache", ttl_seconds=60, clock=lambda: NOW)
    key = _key()
    monkeypatch.setattr(cache_module, "_LOCK_TIMEOUT_SECONDS", 0.01)
    errors: list[ResponseCacheError] = []
    with cache._key_lock(key.key_hash):
        contender = threading.Thread(
            target=lambda: _capture_cache_error(lambda: cache.get(key), errors)
        )
        contender.start()
        contender.join(timeout=1)
    assert [error.code for error in errors] == ["cache_lock_timeout"]


def test_corrupt_index_fails_closed_before_deleting_records(tmp_path: Path) -> None:
    cache = LocalResponseCache(tmp_path / "cache", ttl_seconds=60, clock=lambda: NOW)
    key = _key()
    cache.put(
        key, _response(), linkage=CacheLinkage(run_id="run-a", session_id="session-a")
    )
    run_index = hashlib.sha256(b"run-a").hexdigest()
    marker = tmp_path / "cache" / "runs" / run_index / f"{key.key_hash}.json"
    marker.write_text(json.dumps({"key_hash": "0" * 64, "record_hash": "1" * 64}))
    marker.chmod(0o600)

    with pytest.raises(ResponseCacheError) as caught:
        cache.delete_run("run-a")

    assert caught.value.code == "cache_index_invalid"
    assert cache.get(key) is not None


def _capture_cache_error(operation: object, errors: list[ResponseCacheError]) -> None:
    assert callable(operation)
    try:
        operation()
    except ResponseCacheError as error:
        errors.append(error)


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
