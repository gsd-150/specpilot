from __future__ import annotations

import json
import shutil
import stat
from pathlib import Path

from specpilot.contracts.archive import ArchiveRejectionCode


def quarantine_archive(
    archive_path: Path,
    quarantine_dir: Path,
    *,
    archive_sha256: str,
    archive_bytes: int,
    rejection_code: ArchiveRejectionCode,
) -> Path:
    """Store a rejected archive and a content-free rejection record."""
    record_dir = quarantine_dir / archive_sha256
    record_dir.mkdir(parents=True, exist_ok=True)
    stored_archive = record_dir / "archive.zip"
    if not stored_archive.exists():
        shutil.copyfile(archive_path, stored_archive)
    if stat.S_IMODE(stored_archive.stat().st_mode) != 0o600:
        stored_archive.chmod(0o600)

    record_path = record_dir / "record.json"
    if not record_path.exists():
        record_path.write_text(
            json.dumps(
                {
                    "archive_bytes": archive_bytes,
                    "archive_sha256": archive_sha256,
                    "rejection_code": rejection_code,
                },
                sort_keys=True,
            )
            + "\n"
        )
    if stat.S_IMODE(record_path.stat().st_mode) != 0o600:
        record_path.chmod(0o600)
    return record_path
