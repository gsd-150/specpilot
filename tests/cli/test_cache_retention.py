from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from specpilot.cli import main
from specpilot.contracts.egress import EgressStage
from specpilot.providers.base import ProviderResponse, ResponseMetadata
from specpilot.providers.cache import (
    CacheKey,
    CacheLinkage,
    CacheNamespace,
    LocalResponseCache,
)


def _put(root: Path, *, request_hash: str, run_id: str, session_id: str) -> None:
    cache = LocalResponseCache(
        root,
        ttl_seconds=60,
        clock=lambda: datetime(2000, 1, 1, tzinfo=UTC),
    )
    namespace = CacheNamespace(
        configuration_hash="a" * 64,
        prompt_id="prompt-v1",
        prompt_hash="b" * 64,
        compliance_prompt_hash="1" * 64,
        verifier_prompt_hash="2" * 64,
        source_manifest_id="c" * 64,
        corpus_manifest_id="d" * 64,
    )
    key = CacheKey.create(
        namespace=namespace,
        provider_id="provider-a",
        model_id="model-a",
        stage=EgressStage.EVIDENCE,
        policy_hash="e" * 64,
        request_hash=request_hash,
    )
    cache.put(
        key,
        ProviderResponse(
            provider_id="provider-a",
            model_id="model-a",
            content="private provider response",
            metadata=ResponseMetadata(
                prompt_tokens=1,
                completion_tokens=1,
                finish_reason="stop",
                duration_ms=1,
                request_bytes=2,
            ),
        ),
        linkage=CacheLinkage(run_id=run_id, session_id=session_id),
    )


def test_cache_delete_run_outputs_count_only(tmp_path: Path, capsys) -> None:
    root = tmp_path / "cache"
    _put(root, request_hash="1" * 64, run_id="private-run", session_id="session")

    code = main(
        [
            "cache",
            "delete-run",
            "--directory",
            str(root),
            "--ttl-seconds",
            "60",
            "--run-id",
            "private-run",
        ]
    )

    captured = capsys.readouterr()
    assert code == 0
    assert json.loads(captured.out) == {"deleted": 1}
    assert "private-run" not in captured.out + captured.err


def test_cache_delete_session_and_expired_output_counts_only(
    tmp_path: Path, capsys
) -> None:
    root = tmp_path / "cache"
    _put(root, request_hash="1" * 64, run_id="run-a", session_id="private-session")
    code = main(
        [
            "cache",
            "delete-session",
            "--directory",
            str(root),
            "--ttl-seconds",
            "60",
            "--session-id",
            "private-session",
        ]
    )
    assert code == 0
    assert json.loads(capsys.readouterr().out) == {"deleted": 1}

    _put(root, request_hash="2" * 64, run_id="run-b", session_id="session-b")
    code = main(
        [
            "cache",
            "delete-expired",
            "--directory",
            str(root),
            "--ttl-seconds",
            "60",
        ]
    )
    captured = capsys.readouterr()
    assert code == 0
    assert json.loads(captured.out) == {"deleted": 1}
    assert "provider response" not in captured.out + captured.err
