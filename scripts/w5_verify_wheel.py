from __future__ import annotations

import zipfile
from pathlib import Path

TRACE_MEMBER_ROOT = "specpilot/api/static/trace"
REQUIRED_PACKAGE_FILES = {
    "specpilot/egress/policies/default-v1.json": Path(
        "src/specpilot/egress/policies/default-v1.json"
    ),
    "specpilot/egress/policies/fixture-overlay-v1.json": Path(
        "src/specpilot/egress/policies/fixture-overlay-v1.json"
    ),
}


class WheelVerificationError(RuntimeError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise WheelVerificationError(message)


def _source_trace_members(trace_root: Path) -> dict[str, bytes]:
    _require(
        trace_root.is_dir(),
        f"trace source directory is missing: {trace_root}",
    )
    members: dict[str, bytes] = {}
    for path in sorted(trace_root.rglob("*")):
        _require(
            not path.is_symlink(),
            f"trace source contains a symlink: {path}",
        )
        if path.is_dir():
            continue
        _require(path.is_file(), f"trace source contains a non-file: {path}")
        relative = path.relative_to(trace_root).as_posix()
        members[f"{TRACE_MEMBER_ROOT}/{relative}"] = path.read_bytes()
    _require(
        f"{TRACE_MEMBER_ROOT}/index.html" in members,
        "trace index.html is missing",
    )
    _require(
        any(name.startswith(f"{TRACE_MEMBER_ROOT}/assets/") for name in members),
        "trace asset set is empty",
    )
    return members


def verify_trace_bundle(wheel_path: Path, trace_root: Path) -> None:
    source_members = _source_trace_members(trace_root)
    with zipfile.ZipFile(wheel_path) as archive:
        trace_infos = [
            info
            for info in archive.infolist()
            if not info.is_dir() and info.filename.startswith(f"{TRACE_MEMBER_ROOT}/")
        ]
        trace_names = [info.filename for info in trace_infos]
        _require(
            len(trace_names) == len(set(trace_names)),
            "wheel trace bundle contains duplicate members",
        )
        packaged_members = {
            info.filename: archive.read(info) for info in trace_infos
        }

    missing = sorted(source_members.keys() - packaged_members.keys())
    unexpected = sorted(packaged_members.keys() - source_members.keys())
    changed = sorted(
        name
        for name in source_members.keys() & packaged_members.keys()
        if source_members[name] != packaged_members[name]
    )
    _require(
        not (missing or unexpected or changed),
        "wheel trace bundle differs from source: "
        f"missing={missing}, unexpected={unexpected}, changed={changed}",
    )


def verify_release_wheel(repo_root: Path) -> Path:
    wheels = sorted((repo_root / "tmp/w5-dist").glob("specpilot-*.whl"))
    _require(
        len(wheels) == 1,
        f"expected exactly one W5 wheel, found: {wheels}",
    )
    wheel_path = wheels[0]
    verify_trace_bundle(
        wheel_path,
        repo_root / "src/specpilot/api/static/trace",
    )

    with zipfile.ZipFile(wheel_path) as archive:
        infos_by_name: dict[str, list[zipfile.ZipInfo]] = {}
        for info in archive.infolist():
            infos_by_name.setdefault(info.filename, []).append(info)
        for member, relative_source in REQUIRED_PACKAGE_FILES.items():
            infos = infos_by_name.get(member, [])
            _require(
                len(infos) == 1,
                f"wheel must contain exactly one {member}",
            )
            source_bytes = (repo_root / relative_source).read_bytes()
            _require(
                archive.read(infos[0]) == source_bytes,
                f"wheel member differs from source: {member}",
            )
    return wheel_path


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    wheel_path = verify_release_wheel(repo_root)
    print(f"verified packaged bytes: {wheel_path}")


if __name__ == "__main__":
    main()
