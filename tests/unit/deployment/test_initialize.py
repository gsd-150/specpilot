from __future__ import annotations

import os
from pathlib import Path

import pytest

import specpilot.deployment.initialize as initialize_module
from specpilot.deployment.initialize import (
    InitializationRefusal,
    _pinned_real_directories,
    _read_bounded,
)


def test_bounded_read_refuses_when_open_file_name_is_swapped(
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
        *,
        dir_fd: int | None = None,
    ) -> int:
        descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
        if path == source.name and dir_fd is not None:
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
    monkeypatch.setattr(os, "supports_dir_fd", os.supports_dir_fd | {open_then_swap})

    with pytest.raises(InitializationRefusal) as raised:
        _read_bounded(source, max_bytes=len(trusted), code="invalid")

    assert raised.value.code == "invalid"


def test_bounded_read_refuses_a_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target.xml"
    target.write_bytes(b"target")
    source = tmp_path / "source.xml"
    source.symlink_to(target)

    with pytest.raises(InitializationRefusal) as raised:
        _read_bounded(source, max_bytes=1024, code="fixture_source_invalid")

    assert raised.value.code == "fixture_source_invalid"


def test_bounded_read_refuses_a_fifo_without_blocking(tmp_path: Path) -> None:
    source = tmp_path / "source.xml"
    os.mkfifo(source, mode=0o600)

    with pytest.raises(InitializationRefusal) as raised:
        _read_bounded(source, max_bytes=1024, code="fixture_source_invalid")

    assert raised.value.code == "fixture_source_invalid"


def _private_real_tree(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "real-input"
    root.mkdir(mode=0o700)
    for name in ("source-manifests", "sources", "model"):
        (root / name).mkdir(mode=0o700)
    output = tmp_path / "corpus-output"
    output.mkdir(mode=0o700)
    return root, output


def test_pinned_real_tree_refuses_a_named_child_inode_swap(tmp_path: Path) -> None:
    root, output = _private_real_tree(tmp_path)

    with (
        _pinned_real_directories(root, output) as pinned,
        pytest.raises(InitializationRefusal) as raised,
    ):
        child = root / "sources"
        child.rename(root / "sources-pinned")
        child.mkdir(mode=0o700)
        pinned.revalidate()

    assert raised.value.code == "real_corpus_unavailable"


def test_pinned_real_tree_refuses_the_root_path_inode_swap(tmp_path: Path) -> None:
    root, output = _private_real_tree(tmp_path)
    attacker = tmp_path / "attacker"
    attacker.mkdir(mode=0o700)

    with (
        _pinned_real_directories(root, output) as pinned,
        pytest.raises(InitializationRefusal) as raised,
    ):
        root.rename(tmp_path / "real-input-pinned")
        root.symlink_to(attacker, target_is_directory=True)
        pinned.revalidate()

    assert raised.value.code == "real_corpus_unavailable"


def test_pinned_model_view_keeps_the_original_inode_through_an_aba_swap(
    tmp_path: Path,
) -> None:
    root, output = _private_real_tree(tmp_path)
    trusted_model = root / "model" / "identity.json"
    trusted_model.write_bytes(b"trusted-model")
    attacker = tmp_path / "attacker-model"
    attacker.mkdir(mode=0o700)
    (attacker / "identity.json").write_bytes(b"attacker-model")

    with (
        _pinned_real_directories(root, output) as pinned,
        pytest.raises(InitializationRefusal) as raised,
        initialize_module._pinned_model_view(pinned) as model_view,
    ):
        original = root / "model-pinned"
        (root / "model").rename(original)
        attacker.rename(root / "model")
        assert (model_view / "identity.json").read_bytes() == b"trusted-model"
        (root / "model").rename(attacker)
        original.rename(root / "model")

    assert raised.value.code == "real_corpus_unavailable"
