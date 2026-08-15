from __future__ import annotations

import os
from pathlib import Path

import pytest

from specpilot.ingestion._secure_fs import open_directory_path
from specpilot.manifests.corpus_store import CorpusManifestStore


def _initialize_store(path: Path) -> None:
    path.mkdir(mode=0o700)
    lease = CorpusManifestStore(path).acquire_freeze_lease("collection")
    lease.close()


def test_descriptor_bound_store_refuses_an_output_aba_replacement(
    tmp_path: Path,
) -> None:
    output = tmp_path / "output"
    attacker = tmp_path / "attacker"
    _initialize_store(output)
    _initialize_store(attacker)
    descriptor = open_directory_path(output, create=False)
    try:
        store = CorpusManifestStore.from_fd(output, descriptor)
        original = tmp_path / "output-pinned"
        output.rename(original)
        attacker.rename(output)
        try:
            with pytest.raises(FileExistsError):
                store.read_all()
            with pytest.raises(FileExistsError):
                store.acquire_freeze_lease("collection")
        finally:
            output.rename(attacker)
            original.rename(output)
    finally:
        os.close(descriptor)
