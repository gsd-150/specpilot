from __future__ import annotations

import json
import shutil
from pathlib import Path

from specpilot.cli import main

_ROOT = Path(__file__).resolve().parents[2]


def _fixture_copy(tmp_path: Path) -> Path:
    destination = tmp_path / "fixture"
    shutil.copytree(_ROOT / "fixtures" / "demo", destination)
    return destination


def test_init_fixture_validates_committed_hashes_before_qdrant(
    tmp_path: Path, capsys
) -> None:
    fixture = _fixture_copy(tmp_path)
    source = fixture / "source.xml"
    source.write_bytes(source.read_bytes() + b"\n")

    code = main(
        [
            "corpus",
            "init-fixture",
            "--fixture-dir",
            str(fixture),
            "--source-manifest-dir",
            str(tmp_path / "source-manifests"),
            "--corpus-manifest-dir",
            str(tmp_path / "corpus-manifests"),
            "--ready-dir",
            str(tmp_path / "ready"),
            "--qdrant-url",
            "http://127.0.0.1:1",
        ]
    )

    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert captured.err == "fixture_source_hash_mismatch\n"
    assert not (tmp_path / "source-manifests").exists()


def test_init_real_requires_an_absolute_corpus_directory(
    tmp_path: Path, capsys
) -> None:
    code = main(
        [
            "corpus",
            "init-real",
            "--corpus-dir",
            "relative/corpus",
            "--ready-dir",
            str(tmp_path / "ready"),
            "--qdrant-url",
            "http://127.0.0.1:1",
        ]
    )

    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert captured.err == "real_corpus_dir_not_absolute\n"


def test_fixture_manifest_is_canonical_and_binds_1024d_points() -> None:
    fixture = _ROOT / "fixtures" / "demo"
    encoded = (fixture / "fixture-manifest.json").read_bytes()
    payload = json.loads(encoded)

    assert (
        encoded
        == (
            json.dumps(
                payload,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode()
    )
    assert payload["dense_points"]["vector_size"] == 1024
    assert payload["dense_points"]["point_count"] == 6
    lines = (fixture / payload["dense_points"]["filename"]).read_text().splitlines()
    assert len(lines) == 6
    assert all(len(json.loads(line)["vector"]) == 1024 for line in lines)
