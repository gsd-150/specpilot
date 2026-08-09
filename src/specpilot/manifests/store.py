from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import TypeVar

from pydantic import ValidationError

from specpilot.contracts.manifests import (
    ComplianceAssessment,
    ProviderRouteBinding,
    RfcSourceManifest,
    RfcSourceManifestDraft,
    SourceManifest,
    SourceManifestDraft,
)
from specpilot.manifests._secure_records import SecureRecordDirectory
from specpilot.manifests.canonical import canonical_json

_ManifestT = TypeVar("_ManifestT", SourceManifest, RfcSourceManifest)

_MANIFEST_ID = re.compile(r"^[0-9a-f]{64}$")
_MAX_MANIFEST_BYTES = 256 * 1024


class UnsupportedManifestVersionError(ValueError):
    """Raised when a readable manifest declares an unsupported schema version."""


def _reject_nonstandard_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


class ManifestStore:
    """Create-only storage for canonical, content-addressed source manifests."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def create_source(self, draft: SourceManifestDraft) -> SourceManifest:
        if draft.predecessor_manifest_id is not None:
            raise ValueError("create_source requires an initial manifest draft")
        return self._create(SourceManifest.from_draft(draft))

    def create_source_v2(self, draft: RfcSourceManifestDraft) -> RfcSourceManifest:
        if draft.predecessor_manifest_id is not None:
            raise ValueError("create_source_v2 requires an initial manifest draft")
        return self._create(RfcSourceManifest.from_draft(draft))

    def create_successor(
        self,
        predecessor: SourceManifest,
        *,
        assessment: ComplianceAssessment,
        route_binding: ProviderRouteBinding,
        created_at: datetime,
    ) -> SourceManifest:
        stored_predecessor = self.read_source(predecessor.manifest_id)
        if stored_predecessor != predecessor:
            raise ValueError("predecessor does not match the stored manifest")
        if not isinstance(stored_predecessor, SourceManifest):
            raise ValueError("create_successor requires a v1 predecessor")

        draft = SourceManifestDraft(
            schema_version=predecessor.schema_version,
            document_id=predecessor.document_id,
            document_version=predecessor.document_version,
            download_url=predecessor.download_url,
            archive_sha256=predecessor.archive_sha256,
            docx_sha256=predecessor.docx_sha256,
            downloaded_at=predecessor.downloaded_at,
            created_at=created_at,
            predecessor_manifest_id=predecessor.manifest_id,
            cloud_egress_authorized=True,
            compliance_assessment=assessment,
            provider_route_binding=route_binding,
        )
        return self._create(SourceManifest.from_draft(draft))

    def read_source(self, manifest_id: str) -> SourceManifest | RfcSourceManifest:
        self._validate_manifest_id(manifest_id)
        with SecureRecordDirectory.open(self._directory, create=False) as records:
            data = records.read(
                f"{manifest_id}.json",
                max_bytes=_MAX_MANIFEST_BYTES,
            )
        return self._decode_canonical(data, manifest_id)

    def _create(self, manifest: _ManifestT) -> _ManifestT:
        data = canonical_json(manifest, include_manifest_id=True)
        if len(data) > _MAX_MANIFEST_BYTES:
            raise ValueError("manifest exceeds maximum storage size")

        destination_name = f"{manifest.manifest_id}.json"
        with SecureRecordDirectory.open(self._directory, create=True) as records:
            stored = records.publish(
                destination_name,
                data,
                max_bytes=_MAX_MANIFEST_BYTES,
            )
        try:
            decoded = self._decode_canonical(stored, manifest.manifest_id)
            if decoded != manifest or not isinstance(decoded, type(manifest)):
                raise FileExistsError(self._directory / destination_name)
            return decoded
        except (ValidationError, ValueError) as error:
            raise FileExistsError(self._directory / destination_name) from error

    @staticmethod
    def _validate_manifest_id(manifest_id: str) -> None:
        if _MANIFEST_ID.fullmatch(manifest_id) is None:
            raise ValueError("manifest_id must be a lowercase SHA-256 digest")

    @staticmethod
    def _decode_canonical(
        data: bytes, expected_id: str
    ) -> SourceManifest | RfcSourceManifest:
        model: type[SourceManifest] | type[RfcSourceManifest] = SourceManifest
        try:
            raw_manifest = json.loads(
                data,
                parse_constant=_reject_nonstandard_json_constant,
            )
        except (UnicodeDecodeError, ValueError):
            pass
        else:
            if isinstance(raw_manifest, dict) and isinstance(
                raw_manifest.get("schema_version"), str
            ):
                declared = raw_manifest["schema_version"]
                if declared == "source-manifest/v2":
                    model = RfcSourceManifest
                elif declared != "source-manifest/v1":
                    raise UnsupportedManifestVersionError(
                        "stored manifest has an unsupported schema version"
                    )
        try:
            manifest = model.model_validate_json(data)
        except ValidationError as error:
            raise ValueError("stored manifest is invalid") from error
        if manifest.manifest_id != expected_id:
            raise ValueError("stored manifest ID does not match its filename")
        if canonical_json(manifest, include_manifest_id=True) != data:
            raise ValueError("stored manifest is not canonical JSON")
        return manifest
