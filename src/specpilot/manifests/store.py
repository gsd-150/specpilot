from __future__ import annotations

import errno
import os
import re
import stat
from contextlib import suppress
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from specpilot.contracts.manifests import (
    ComplianceAssessment,
    ProviderRouteBinding,
    SourceManifest,
    SourceManifestDraft,
)
from specpilot.ingestion._secure_fs import (
    create_private_file,
    open_directory_path,
    require_secure_filesystem,
    revalidate_directory_path,
)
from specpilot.manifests.canonical import canonical_json

_MANIFEST_ID = re.compile(r"^[0-9a-f]{64}$")
_MAX_MANIFEST_BYTES = 256 * 1024


class ManifestStore:
    """Create-only storage for canonical, content-addressed source manifests."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def create_source(self, draft: SourceManifestDraft) -> SourceManifest:
        if draft.predecessor_manifest_id is not None:
            raise ValueError("create_source requires an initial manifest draft")
        return self._create(SourceManifest.from_draft(draft))

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

    def read_source(self, manifest_id: str) -> SourceManifest:
        self._validate_manifest_id(manifest_id)
        require_secure_filesystem()
        directory_descriptor = open_directory_path(self._directory, create=False)
        try:
            revalidate_directory_path(self._directory, directory_descriptor)
            data = self._read_regular_file(
                directory_descriptor,
                f"{manifest_id}.json",
            )
            revalidate_directory_path(self._directory, directory_descriptor)
            return self._decode_canonical(data, manifest_id)
        finally:
            os.close(directory_descriptor)

    def _create(self, manifest: SourceManifest) -> SourceManifest:
        data = canonical_json(manifest, include_manifest_id=True)
        if len(data) > _MAX_MANIFEST_BYTES:
            raise ValueError("manifest exceeds maximum storage size")

        require_secure_filesystem()
        directory_descriptor = open_directory_path(self._directory, create=True)
        try:
            return self._create_in_directory(directory_descriptor, manifest, data)
        finally:
            os.close(directory_descriptor)

    def _create_in_directory(
        self,
        directory_descriptor: int,
        manifest: SourceManifest,
        data: bytes,
    ) -> SourceManifest:
        destination_name = f"{manifest.manifest_id}.json"
        temporary_name: str | None = None
        published = False
        replay = False
        try:
            os.fchmod(directory_descriptor, 0o700)
            revalidate_directory_path(self._directory, directory_descriptor)
            file_descriptor, temporary_name = create_private_file(
                directory_descriptor,
                prefix=".manifest-",
            )
            with os.fdopen(file_descriptor, "wb") as temporary_file:
                temporary_file.write(data)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())

            revalidate_directory_path(self._directory, directory_descriptor)
            try:
                os.link(
                    temporary_name,
                    destination_name,
                    src_dir_fd=directory_descriptor,
                    dst_dir_fd=directory_descriptor,
                    follow_symlinks=False,
                )
                published = True
            except FileExistsError:
                replay = True

            if published:
                os.fsync(directory_descriptor)
                revalidate_directory_path(self._directory, directory_descriptor)
        except BaseException:
            if published:
                with suppress(FileNotFoundError):
                    os.unlink(destination_name, dir_fd=directory_descriptor)
                    os.fsync(directory_descriptor)
            raise
        finally:
            if temporary_name is not None:
                with suppress(FileNotFoundError):
                    os.unlink(temporary_name, dir_fd=directory_descriptor)
                    os.fsync(directory_descriptor)

        try:
            revalidate_directory_path(self._directory, directory_descriptor)
            stored = self._read_regular_file(directory_descriptor, destination_name)
            if stored != data:
                raise FileExistsError(self._directory / destination_name)
            decoded = self._decode_canonical(stored, manifest.manifest_id)
            if decoded != manifest:
                raise FileExistsError(self._directory / destination_name)
            if not published and not replay:
                raise FileExistsError(self._directory / destination_name)
            return decoded
        except (ValidationError, ValueError) as error:
            raise FileExistsError(self._directory / destination_name) from error

    @staticmethod
    def _validate_manifest_id(manifest_id: str) -> None:
        if _MANIFEST_ID.fullmatch(manifest_id) is None:
            raise ValueError("manifest_id must be a lowercase SHA-256 digest")

    def _read_regular_file(self, directory_descriptor: int, name: str) -> bytes:
        try:
            file_descriptor = os.open(
                name,
                os.O_RDONLY | os.O_NOFOLLOW,
                dir_fd=directory_descriptor,
            )
        except OSError as error:
            if error.errno in {errno.ELOOP, errno.EISDIR}:
                raise FileExistsError(self._directory / name) from error
            raise

        try:
            file_status = os.fstat(file_descriptor)
            if (
                not stat.S_ISREG(file_status.st_mode)
                or file_status.st_nlink != 1
                or stat.S_IMODE(file_status.st_mode) != 0o600
                or file_status.st_size > _MAX_MANIFEST_BYTES
            ):
                raise FileExistsError(self._directory / name)
            with os.fdopen(file_descriptor, "rb", closefd=False) as source:
                data = source.read(_MAX_MANIFEST_BYTES + 1)
            if len(data) > _MAX_MANIFEST_BYTES:
                raise FileExistsError(self._directory / name)
            entry_status = os.stat(
                name,
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
            if (
                not stat.S_ISREG(entry_status.st_mode)
                or entry_status.st_dev != file_status.st_dev
                or entry_status.st_ino != file_status.st_ino
            ):
                raise FileExistsError(self._directory / name)
            return data
        finally:
            os.close(file_descriptor)

    @staticmethod
    def _decode_canonical(data: bytes, expected_id: str) -> SourceManifest:
        try:
            manifest = SourceManifest.model_validate_json(data)
        except ValidationError as error:
            raise ValueError("stored manifest is invalid") from error
        if manifest.manifest_id != expected_id:
            raise ValueError("stored manifest ID does not match its filename")
        if canonical_json(manifest, include_manifest_id=True) != data:
            raise ValueError("stored manifest is not canonical JSON")
        return manifest
