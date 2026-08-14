from __future__ import annotations

import os
from pathlib import Path

import pytest

from specpilot.deployment.initialize import InitializationRefusal, _read_bounded


def test_bounded_read_pins_open_file_across_path_swap(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "source.xml"
    pinned = tmp_path / "pinned.xml"
    attacker = tmp_path / "attacker.xml"
    trusted = b"trusted-bytes"
    source.write_bytes(trusted)
    attacker.write_bytes(b"attacker-byte")
    original_open = os.open
    original_read_bytes = Path.read_bytes

    def open_then_swap(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
    ) -> int:
        descriptor = original_open(path, flags, mode)
        if Path(path) == source:
            source.rename(pinned)
            source.symlink_to(attacker)
        return descriptor

    def swap_then_read(path: Path) -> bytes:
        if path == source:
            source.rename(pinned)
            source.symlink_to(attacker)
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", swap_then_read)
    monkeypatch.setattr(os, "open", open_then_swap)

    assert _read_bounded(source, max_bytes=len(trusted), code="invalid") == trusted


def test_bounded_read_refuses_a_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target.xml"
    target.write_bytes(b"target")
    source = tmp_path / "source.xml"
    source.symlink_to(target)

    with pytest.raises(InitializationRefusal) as raised:
        _read_bounded(source, max_bytes=1024, code="fixture_source_invalid")

    assert raised.value.code == "fixture_source_invalid"
