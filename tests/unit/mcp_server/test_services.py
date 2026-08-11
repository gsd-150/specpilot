from __future__ import annotations

import inspect
from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import Path

import pytest
from pydantic import ValidationError

from specpilot.contracts.rfc import RfcLimits
from specpilot.corpus.clauses import ClauseLimits
from specpilot.corpus.tool_metadata import build_rfc_tool_metadata
from specpilot.ingestion.rfc import load_verified_rfc
from specpilot.mcp_server.contracts import (
    ExpandReferencesRequest,
    GetClauseRequest,
    GetTocRequest,
    LookupTermRequest,
    McpToolError,
    SearchClausesRequest,
)
from specpilot.mcp_server.services import (
    McpToolServices,
    SearchBackendHit,
)
from specpilot.retrieval.bm25 import Bm25Index
from specpilot.retrieval.local import LocalCorpus
from specpilot.retrieval.protocol import locator_for_unit
from tests.helpers import rfc_factory
from tests.unit.corpus.test_tool_metadata import TOOL_RFC_XML

CORPUS_ID = "a" * 64
DOCUMENT_ID = "ietf-rfc-9999"


@dataclass(frozen=True)
class Bm25SearchBackend:
    corpus: LocalCorpus

    def search(
        self,
        query: str,
        *,
        corpus_manifest_id: str,
        document_ids: tuple[str, ...],
        normative_levels: tuple[str, ...],
        limit: int,
    ) -> tuple[SearchBackendHit, ...]:
        index = Bm25Index.build(self.corpus.indexable())
        return tuple(
            SearchBackendHit(
                locator=locator_for_unit(
                    corpus_manifest_id, self.corpus.get_clause(hit.unit_id)
                ),
                score=hit.score,
            )
            for hit in index.search(query, self.corpus.unit_count())
        )


@pytest.fixture
def tool_services(tmp_path: Path) -> McpToolServices:
    path = rfc_factory.write(tmp_path, "tools.xml", TOOL_RFC_XML)
    verified = load_verified_rfc(path, RfcLimits())
    documents = ((verified, ClauseLimits()),)
    corpus = LocalCorpus.load(documents, RfcLimits())
    metadata = build_rfc_tool_metadata(
        corpus_manifest_id=CORPUS_ID,
        documents=documents,
        units=corpus.units(),
        rfc_limits=RfcLimits(),
    )
    return McpToolServices(
        corpus=corpus,
        search_backend=Bm25SearchBackend(corpus),
        tool_metadata=metadata,
    )


def _public_methods(value: object) -> tuple[str, ...]:
    return tuple(
        name
        for name, member in inspect.getmembers(value, predicate=callable)
        if not name.startswith("_")
    )


def test_services_are_read_only_and_return_typed_body_bounded_results(
    tool_services: McpToolServices,
) -> None:
    before = tool_services.inventory_hash()
    result = tool_services.search_clauses(
        SearchClausesRequest(
            query="retry",
            corpus_manifest_id=CORPUS_ID,
            document_ids=(DOCUMENT_ID,),
            normative_levels=("MUST", "SHOULD"),
            limit=3,
        )
    )
    clause = tool_services.get_clause(
        GetClauseRequest(
            corpus_manifest_id=CORPUS_ID,
            document_id=DOCUMENT_ID,
            clause_id=result.hits[0].clause_id,
        )
    )
    toc = tool_services.get_toc(
        GetTocRequest(
            corpus_manifest_id=CORPUS_ID,
            document_id=DOCUMENT_ID,
            limit=2,
        )
    )

    assert len(result.hits) <= 3
    assert clause.content_hash == sha256(clause.text.encode()).hexdigest()
    assert len(toc.nodes) == 2
    assert all(not hasattr(hit, "text") for hit in result.hits)
    assert all(not hasattr(node, "text") for node in toc.nodes)
    assert tool_services.inventory_hash() == before
    assert not any(
        name.startswith(("put_", "add_", "delete_", "update_"))
        for name in _public_methods(tool_services)
    )


def test_identical_searches_have_byte_identical_stable_dumps(
    tool_services: McpToolServices,
) -> None:
    request = SearchClausesRequest(
        query="target",
        corpus_manifest_id=CORPUS_ID,
        document_ids=(DOCUMENT_ID,),
        limit=20,
    )

    first = tool_services.search_clauses(request).model_dump_json()
    second = tool_services.search_clauses(request).model_dump_json()

    assert first == second
    assert "Section target one" not in first


def test_search_keeps_table_units_unless_a_normative_filter_excludes_them(
    tool_services: McpToolServices,
) -> None:
    unfiltered = tool_services.search_clauses(
        SearchClausesRequest(
            query="statusmarker",
            corpus_manifest_id=CORPUS_ID,
            document_ids=(DOCUMENT_ID,),
            limit=3,
        )
    )
    filtered = tool_services.search_clauses(
        SearchClausesRequest(
            query="statusmarker",
            corpus_manifest_id=CORPUS_ID,
            document_ids=(DOCUMENT_ID,),
            normative_levels=("MUST",),
            limit=3,
        )
    )

    assert len(unfiltered.hits) == 1
    assert filtered.hits == ()


def test_reference_and_term_services_return_only_bounded_clause_ids(
    tool_services: McpToolServices,
) -> None:
    source = next(
        unit
        for unit in tool_services.corpus.units()
        if unit.text.startswith("A sender")
    )

    expanded = tool_services.expand_references(
        ExpandReferencesRequest(
            corpus_manifest_id=CORPUS_ID,
            document_id=DOCUMENT_ID,
            clause_ids=(source.unit_id,),
        )
    )
    definitions = tool_services.lookup_term(
        LookupTermRequest(
            corpus_manifest_id=CORPUS_ID,
            document_id=DOCUMENT_ID,
            term=" RETRY   TOKEN ",
        )
    )

    assert len(expanded.clause_ids) == 3
    assert len(definitions.definition_clause_ids) == 2
    assert "First definition" not in definitions.model_dump_json()
    assert "RETRY" not in definitions.model_dump_json()


@pytest.mark.parametrize(
    "tool_request",
    [
        SearchClausesRequest(
            query="retry",
            corpus_manifest_id="b" * 64,
            document_ids=(DOCUMENT_ID,),
            limit=3,
        ),
        GetClauseRequest(
            corpus_manifest_id=CORPUS_ID,
            document_id="ietf-rfc-1111",
            clause_id="c" * 64,
        ),
    ],
)
def test_manifest_and_document_scope_errors_are_sanitized(
    tool_services: McpToolServices, tool_request: object
) -> None:
    with pytest.raises(McpToolError) as caught:
        if isinstance(tool_request, SearchClausesRequest):
            tool_services.search_clauses(tool_request)
        else:
            tool_services.get_clause(tool_request)  # type: ignore[arg-type]

    assert caught.value.code in {"invalid_argument", "not_found"}
    assert caught.value.field in {"corpus_manifest_id", "document_id"}
    rendered = str(caught.value)
    assert "retry" not in rendered
    assert "ietf-rfc-1111" not in rendered
    assert len(caught.value.correction) <= 160


def test_invalid_reference_and_integrity_errors_are_sanitized(
    tool_services: McpToolServices,
) -> None:
    bad = next(
        unit for unit in tool_services.corpus.units() if unit.text == "RFC 9110."
    )
    with pytest.raises(McpToolError) as reference_error:
        tool_services.expand_references(
            ExpandReferencesRequest(
                corpus_manifest_id=CORPUS_ID,
                document_id=DOCUMENT_ID,
                clause_ids=(bad.unit_id,),
            )
        )
    assert reference_error.value.code == "invalid_reference"
    assert bad.unit_id not in str(reference_error.value)

    changed_metadata = replace(tool_services.tool_metadata, metadata_hash="0" * 64)
    changed_services = McpToolServices(
        corpus=tool_services.corpus,
        search_backend=tool_services.search_backend,
        tool_metadata=changed_metadata,
    )
    with pytest.raises(McpToolError) as integrity_error:
        changed_services.get_toc(
            GetTocRequest(
                corpus_manifest_id=CORPUS_ID,
                document_id=DOCUMENT_ID,
                limit=1,
            )
        )
    assert integrity_error.value.code == "backend_unavailable"
    assert "hash" not in str(integrity_error.value).lower()


def test_source_hash_mismatch_fails_closed_without_revealing_hashes(
    tool_services: McpToolServices,
) -> None:
    changed_corpus = replace(
        tool_services.corpus,
        _source_hashes=((DOCUMENT_ID, "b" * 64),),
    )
    changed_services = McpToolServices(
        corpus=changed_corpus,
        search_backend=tool_services.search_backend,
        tool_metadata=tool_services.tool_metadata,
    )

    with pytest.raises(McpToolError) as caught:
        changed_services.get_toc(
            GetTocRequest(
                corpus_manifest_id=CORPUS_ID,
                document_id=DOCUMENT_ID,
                limit=1,
            )
        )

    assert caught.value.code == "backend_unavailable"
    assert "b" * 64 not in str(caught.value)


def test_backend_errors_do_not_retain_raw_exception_text(
    tool_services: McpToolServices,
) -> None:
    class FailingBackend:
        def search(
            self, *args: object, **kwargs: object
        ) -> tuple[SearchBackendHit, ...]:
            raise RuntimeError("raw task clause stack text")

    failing_services = McpToolServices(
        corpus=tool_services.corpus,
        search_backend=FailingBackend(),  # type: ignore[arg-type]
        tool_metadata=tool_services.tool_metadata,
    )
    request = SearchClausesRequest(
        query="retry",
        corpus_manifest_id=CORPUS_ID,
        document_ids=(DOCUMENT_ID,),
        limit=3,
    )

    with pytest.raises(McpToolError) as caught:
        failing_services.search_clauses(request)

    assert caught.value.code == "backend_unavailable"
    assert caught.value.__cause__ is None
    assert "raw task clause stack text" not in str(caught.value)


def test_backend_timeout_has_the_closed_retryable_code(
    tool_services: McpToolServices,
) -> None:
    class TimeoutBackend:
        def search(
            self, *args: object, **kwargs: object
        ) -> tuple[SearchBackendHit, ...]:
            raise TimeoutError("raw timeout details")

    timeout_services = McpToolServices(
        corpus=tool_services.corpus,
        search_backend=TimeoutBackend(),  # type: ignore[arg-type]
        tool_metadata=tool_services.tool_metadata,
    )

    with pytest.raises(McpToolError) as caught:
        timeout_services.search_clauses(
            SearchClausesRequest(
                query="retry",
                corpus_manifest_id=CORPUS_ID,
                document_ids=(DOCUMENT_ID,),
                limit=3,
            )
        )

    assert caught.value.code == "tool_timeout"
    assert caught.value.__cause__ is None


@pytest.mark.parametrize(
    ("model", "field", "value"),
    [
        (SearchClausesRequest, "limit", 0),
        (SearchClausesRequest, "limit", 21),
        (GetTocRequest, "limit", 13),
        (LookupTermRequest, "term", "x" * 129),
    ],
)
def test_request_models_enforce_public_bounds(model, field: str, value: object) -> None:
    values: dict[str, object] = {
        "query": "retry",
        "corpus_manifest_id": CORPUS_ID,
        "document_ids": (DOCUMENT_ID,),
        "document_id": DOCUMENT_ID,
        "term": "retry",
        "limit": 3,
    }
    values[field] = value
    accepted = set(model.model_fields)

    with pytest.raises(ValidationError):
        model.model_validate(
            {key: item for key, item in values.items() if key in accepted}
        )
