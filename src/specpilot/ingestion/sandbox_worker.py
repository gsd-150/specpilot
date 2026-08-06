from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import asdict
from pathlib import Path

from specpilot.ingestion._secure_fs import (
    create_private_file,
    open_directory_path,
    revalidate_directory_path,
)
from specpilot.ingestion.ooxml import OoxmlLimits, UnsafeOoxmlError, inspect_docx


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="specpilot-ooxml-worker")
    subparsers = parser.add_subparsers(dest="command", required=True)
    inspect_parser = subparsers.add_parser("inspect")
    inspect_parser.add_argument("--input", type=Path, required=True)
    inspect_parser.add_argument("--output", type=Path, required=True)
    return parser


def _write_json_exclusive(output: Path, payload: dict[str, object]) -> None:
    directory_descriptor = open_directory_path(output.parent, create=False)
    temporary_name: str | None = None
    try:
        output_descriptor, temporary_name = create_private_file(
            directory_descriptor,
            prefix=".inspection-",
        )
        try:
            with os.fdopen(output_descriptor, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            revalidate_directory_path(output.parent, directory_descriptor)
            os.link(
                temporary_name,
                output.name,
                src_dir_fd=directory_descriptor,
                dst_dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
            os.unlink(temporary_name, dir_fd=directory_descriptor)
            temporary_name = None
        except BaseException:
            with suppress(OSError):
                os.close(output_descriptor)
            raise
    finally:
        if temporary_name is not None:
            with suppress(FileNotFoundError):
                os.unlink(temporary_name, dir_fd=directory_descriptor)
        os.close(directory_descriptor)


def _inspection_payload(input_path: Path) -> dict[str, object]:
    inspection = inspect_docx(input_path, OoxmlLimits())
    payload = asdict(inspection)
    payload["external_relationships"] = []
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        payload = _inspection_payload(arguments.input)
        _write_json_exclusive(arguments.output, payload)
    except UnsafeOoxmlError as error:
        print(error.code.value, file=sys.stderr)
        return 2
    except (OSError, RuntimeError):
        print("io_error", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
