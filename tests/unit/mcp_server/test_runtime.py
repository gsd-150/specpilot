from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from uvicorn.importer import import_from_string

from specpilot.contracts.corpus_manifest import Bm25Binding, ParseQaEvidence
from specpilot.contracts.manifests import RfcSourceManifestDraft
from specpilot.contracts.rfc import RfcLimits
from specpilot.corpus.clauses import EXCLUDED_SECTIONS, ClauseLimits
from specpilot.corpus.dense_inventory import derived_corpus_sha256
from specpilot.deployment.ready import ReadyMarker, ReadyMarkerStore
from specpilot.ingestion.rfc import load_verified_rfc
from specpilot.manifests.corpus_store import CorpusManifestStore
from specpilot.manifests.store import ManifestStore
from specpilot.mcp_server.app import create_runtime_app
from specpilot.mcp_server.client import StreamableMcpClient
from specpilot.mcp_server.runtime import (
    RuntimeConfig,
    _RuntimeBm25SearchBackend,
    load_runtime_config,
)
from specpilot.retrieval.bm25 import Bm25Index
from specpilot.retrieval.local import LocalCorpus
from tests.helpers import rfc_factory
from tests.helpers.corpus_manifest_factory import corpus_draft
from tests.unit.corpus.test_tool_metadata import TOOL_RFC_XML

_ROOT = Path(__file__).resolve().parents[3]
_RUNTIME_ENV = (
    "SPECPILOT_MCP_CORPUS_MANIFEST_DIR",
    "SPECPILOT_MCP_CORPUS_MANIFEST_ID",
    "SPECPILOT_MCP_SOURCE_MANIFEST_DIR",
    "SPECPILOT_MCP_SOURCES_JSON",
    "SPECPILOT_MCP_ALLOWED_HOSTS_JSON",
    "SPECPILOT_MCP_ALLOWED_ORIGINS_JSON",
    "SPECPILOT_MCP_READY_DIR",
    "SPECPILOT_MCP_READY_ID",
    "SPECPILOT_MCP_MODE",
)


def _clear_runtime_env(monkeypatch) -> None:
    for name in _RUNTIME_ENV:
        monkeypatch.delenv(name, raising=False)


def test_uvicorn_runtime_factory_imports_and_missing_config_fails_closed(
    monkeypatch,
) -> None:
    _clear_runtime_env(monkeypatch)
    monkeypatch.setenv(
        "SPECPILOT_MCP_SOURCE_MANIFEST_DIR",
        "/private/secret-runtime-path",
    )

    factory = import_from_string("specpilot.mcp_server.app:create_runtime_app")
    app = factory()
    with TestClient(app, base_url="http://127.0.0.1") as client:
        health = client.get("/health")
        tool_route = client.post("/mcp", json={})

    assert health.status_code == 503
    assert health.json() == {
        "status": "unavailable",
        "code": "mcp_runtime_config_invalid",
    }
    assert tool_route.status_code == 404
    assert "/private/secret-runtime-path" not in health.text


def test_mcp_container_invokes_the_zero_argument_runtime_factory() -> None:
    dockerfile = (_ROOT / "docker" / "mcp.Dockerfile").read_text(encoding="utf-8")

    assert "specpilot.mcp_server.app:create_runtime_app" in dockerfile
    assert "specpilot.mcp_server.app:create_app" not in dockerfile


def test_runtime_factory_is_unhealthy_when_ready_mode_mismatches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    corpus_store = CorpusManifestStore(tmp_path / "corpus-manifests")
    draft = corpus_draft()
    with corpus_store.acquire_freeze_lease(draft.collection_name) as lease:
        corpus = corpus_store.create(draft, lease=lease)
    marker = ReadyMarker.create(
        source_manifest_ids=corpus.source_manifest_ids,
        corpus_manifest_id=corpus.manifest_id,
        collection_name=corpus.collection_name,
        point_count=corpus.point_count,
        inventory_root_sha256=corpus.inventory_root_sha256,
        mode="fixture",
    )
    ready_dir = tmp_path / "ready"
    ReadyMarkerStore(ready_dir).publish(marker)
    config = RuntimeConfig(
        corpus_manifest_dir=tmp_path / "corpus-manifests",
        corpus_manifest_id=corpus.manifest_id,
        source_manifest_dir=tmp_path / "source-manifests",
        ready_dir=ready_dir,
        ready_id=marker.ready_id,
        mode="real",
        sources=(
            {
                "manifest_id": corpus.source_manifest_ids[0],
                "xml_path": tmp_path / "source.xml",
            },
        ),
    )
    monkeypatch.setattr("specpilot.mcp_server.app.load_runtime_config", lambda: config)

    app = create_runtime_app()
    with TestClient(app, base_url="http://127.0.0.1") as client:
        health = client.get("/health")

    assert health.status_code == 503
    assert health.json() == {
        "status": "unavailable",
        "code": "mcp_runtime_config_invalid",
    }


def test_runtime_transport_configuration_rejects_normalized_host_input(
    monkeypatch,
) -> None:
    _clear_runtime_env(monkeypatch)
    monkeypatch.setenv("SPECPILOT_MCP_CORPUS_MANIFEST_DIR", "/runtime/corpus")
    monkeypatch.setenv("SPECPILOT_MCP_CORPUS_MANIFEST_ID", "a" * 64)
    monkeypatch.setenv("SPECPILOT_MCP_SOURCE_MANIFEST_DIR", "/runtime/sources")
    monkeypatch.setenv(
        "SPECPILOT_MCP_SOURCES_JSON",
        json.dumps(
            [{"manifest_id": "b" * 64, "xml_path": "/runtime/rfc.xml"}]
        ),
    )
    monkeypatch.setenv(
        "SPECPILOT_MCP_ALLOWED_HOSTS_JSON", '[" mcp:8080 "]'
    )

    try:
        load_runtime_config()
    except ValueError as error:
        assert "exact" in str(error)
    else:
        raise AssertionError("normalized host input was accepted")


def test_production_runtime_requires_ready_identity_at_nonstandard_mounts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_runtime_env(monkeypatch)
    monkeypatch.setenv("SPECPILOT_MCP_CORPUS_MANIFEST_DIR", "/runtime/corpus")
    monkeypatch.setenv("SPECPILOT_MCP_CORPUS_MANIFEST_ID", "a" * 64)
    monkeypatch.setenv("SPECPILOT_MCP_SOURCE_MANIFEST_DIR", "/runtime/sources")
    monkeypatch.setenv(
        "SPECPILOT_MCP_SOURCES_JSON",
        json.dumps(
            [{"manifest_id": "b" * 64, "xml_path": "/runtime/rfc.xml"}]
        ),
    )

    with pytest.raises(ValidationError, match="ready marker"):
        load_runtime_config()


def test_markerless_runtime_requires_an_explicit_non_environment_test_flag(
    tmp_path: Path,
) -> None:
    config = RuntimeConfig(
        corpus_manifest_dir=tmp_path / "corpus",
        corpus_manifest_id="a" * 64,
        source_manifest_dir=tmp_path / "sources",
        sources=(
            {
                "manifest_id": "b" * 64,
                "xml_path": tmp_path / "rfc.xml",
            },
        ),
        allow_missing_ready_for_tests=True,
    )

    assert config.ready_dir is None
    assert config.ready_id is None
    assert config.mode is None
    assert config.allow_missing_ready_for_tests is True


@pytest.mark.parametrize(
    "xml_path",
    [
        "/etc/passwd",
        "/run/specpilot/sources/../manifests/source.json",
        "relative.xml",
    ],
)
def test_deployed_runtime_source_must_stay_inside_fixed_mount(
    monkeypatch: pytest.MonkeyPatch, xml_path: str
) -> None:
    _clear_runtime_env(monkeypatch)
    monkeypatch.setenv(
        "SPECPILOT_MCP_CORPUS_MANIFEST_DIR", "/run/specpilot/corpus"
    )
    monkeypatch.setenv("SPECPILOT_MCP_CORPUS_MANIFEST_ID", "a" * 64)
    monkeypatch.setenv(
        "SPECPILOT_MCP_SOURCE_MANIFEST_DIR", "/run/specpilot/manifests"
    )
    monkeypatch.setenv(
        "SPECPILOT_MCP_SOURCES_JSON",
        json.dumps([{"manifest_id": "b" * 64, "xml_path": xml_path}]),
    )

    with pytest.raises(ValidationError):
        load_runtime_config()


def test_runtime_search_backend_never_returns_an_unrequested_document(
    tmp_path: Path,
) -> None:
    first_path = rfc_factory.write(tmp_path, "first.xml", TOOL_RFC_XML)
    second_path = rfc_factory.write(
        tmp_path,
        "second.xml",
        TOOL_RFC_XML.replace('number="9999"', 'number="9998"'),
    )
    documents = tuple(
        (
            load_verified_rfc(path, RfcLimits()),
            ClauseLimits(excluded_sections=EXCLUDED_SECTIONS),
        )
        for path in (first_path, second_path)
    )
    corpus = LocalCorpus.load(documents, RfcLimits())
    backend = _RuntimeBm25SearchBackend(corpus, Bm25Index.build(corpus.indexable()))

    hits = backend.search(
        "retry",
        corpus_manifest_id="a" * 64,
        document_ids=("ietf-rfc-9999",),
        normative_levels=(),
        limit=5,
    )

    assert hits
    assert {hit.locator.document_id for hit in hits} == {"ietf-rfc-9999"}


@pytest.mark.anyio
async def test_runtime_factory_builds_services_and_serves_real_mcp_protocol(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _clear_runtime_env(monkeypatch)
    xml_path = rfc_factory.write(tmp_path, "runtime.xml", TOOL_RFC_XML)
    xml_bytes = xml_path.read_bytes()
    source_dir = tmp_path / "source-manifests"
    source = ManifestStore(source_dir).create_source_v2(
        RfcSourceManifestDraft(
            document_id="ietf-rfc-9999",
            document_version="2026-08",
            text_url="https://example.test/rfc9999.txt",
            xml_url="https://example.test/rfc9999.xml",
            text_sha256="f" * 64,
            xml_sha256=hashlib.sha256(xml_bytes).hexdigest(),
            downloaded_at=datetime(2026, 8, 12, tzinfo=UTC),
            created_at=datetime(2026, 8, 12, 1, tzinfo=UTC),
        )
    )
    verified = load_verified_rfc(xml_path, RfcLimits())
    documents = ((verified, ClauseLimits(excluded_sections=EXCLUDED_SECTIONS)),)
    corpus = LocalCorpus.load(documents, RfcLimits())
    bm25 = Bm25Index.build(corpus.indexable())
    corpus_dir = tmp_path / "corpus-manifests"
    corpus_store = CorpusManifestStore(corpus_dir)
    draft = corpus_draft(
        source_manifest_ids=(source.manifest_id,),
        bm25=Bm25Binding(
            tokenizer_version=bm25.tokenizer_version,
            k1=bm25.parameters.k1,
            b=bm25.parameters.b,
            index_fingerprint=bm25.fingerprint,
        ),
        point_count=corpus.unit_count(),
        derived_corpus_sha256=derived_corpus_sha256(corpus.units()),
        parse_qa=(
            ParseQaEvidence(
                source_manifest_id=source.manifest_id,
                evidence_sha256="1" * 64,
            ),
        ),
    )
    with corpus_store.acquire_freeze_lease(draft.collection_name) as lease:
        corpus_manifest = corpus_store.create(draft, lease=lease)
    ready = ReadyMarker.create(
        source_manifest_ids=corpus_manifest.source_manifest_ids,
        corpus_manifest_id=corpus_manifest.manifest_id,
        collection_name=corpus_manifest.collection_name,
        point_count=corpus_manifest.point_count,
        inventory_root_sha256=corpus_manifest.inventory_root_sha256,
        mode="fixture",
    )
    ready_dir = tmp_path / "ready"
    ReadyMarkerStore(ready_dir).publish(ready)

    monkeypatch.setenv("SPECPILOT_MCP_CORPUS_MANIFEST_DIR", str(corpus_dir))
    monkeypatch.setenv(
        "SPECPILOT_MCP_CORPUS_MANIFEST_ID", corpus_manifest.manifest_id
    )
    monkeypatch.setenv("SPECPILOT_MCP_SOURCE_MANIFEST_DIR", str(source_dir))
    monkeypatch.setenv("SPECPILOT_MCP_READY_DIR", str(ready_dir))
    monkeypatch.setenv("SPECPILOT_MCP_READY_ID", ready.ready_id)
    monkeypatch.setenv("SPECPILOT_MCP_MODE", "fixture")
    monkeypatch.setenv(
        "SPECPILOT_MCP_SOURCES_JSON",
        json.dumps(
            [{"manifest_id": source.manifest_id, "xml_path": str(xml_path)}]
        ),
    )

    app = create_runtime_app()
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1:8080",
        ) as http_client,
        StreamableMcpClient(
            "http://127.0.0.1:8080/mcp", http_client=http_client
        ) as mcp_client,
    ):
        health = await http_client.get("/health")
        listed = await mcp_client.list_tools()
        result = await mcp_client.call_tool(
            "get_toc",
            {
                "corpus_manifest_id": corpus_manifest.manifest_id,
                "document_id": "ietf-rfc-9999",
                "limit": 2,
            },
        )

    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert {tool.name for tool in listed.tools} == {
        "search_clauses",
        "get_clause",
        "get_toc",
        "expand_references",
        "lookup_term",
    }
    assert result.isError is False
    assert result.structuredContent is not None
    assert 1 <= len(result.structuredContent["nodes"]) <= 2
