import zipfile
from pathlib import Path

import pytest

from scripts.w5_verify_wheel import WheelVerificationError, verify_trace_bundle


def _make_target(name: str) -> str:
    makefile = Path("Makefile").read_text(encoding="utf-8")
    target = makefile[makefile.index(f"{name}:") :]
    return target[: target.index("\n\n")]


def _write_wheel(path: Path, members: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)


def _trace_source(root: Path) -> dict[str, bytes]:
    files = {
        "index.html": b"<script src='assets/app.js'></script>",
        "assets/app.js": b"export const current = true;",
        "assets/app.css": b"body { color: black; }",
    }
    for relative, content in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return {
        f"specpilot/api/static/trace/{relative}": content
        for relative, content in files.items()
    }


def test_package_gate_discards_stale_build_state_and_uses_isolation() -> None:
    target = _make_target("package-check")

    cleanup = "rm -rf -- build tmp/w5-dist"
    build = "-m build --wheel"
    assert cleanup in target
    assert target.index(cleanup) < target.index(build)
    assert "test ! -L tmp" in target
    assert "--no-isolation" not in target
    assert "scripts/w5_verify_wheel.py" in target


def test_wheel_verifier_accepts_the_exact_trace_tree(tmp_path: Path) -> None:
    trace_root = tmp_path / "trace"
    members = _trace_source(trace_root)
    wheel = tmp_path / "specpilot.whl"
    _write_wheel(wheel, members)

    verify_trace_bundle(wheel, trace_root)


@pytest.mark.parametrize("defect", ["stale", "changed", "changed-index"])
def test_wheel_verifier_rejects_stale_or_changed_bytes(
    tmp_path: Path, defect: str
) -> None:
    trace_root = tmp_path / "trace"
    members = _trace_source(trace_root)
    if defect == "stale":
        members["specpilot/api/static/trace/assets/old.js"] = b"stale"
    elif defect == "changed":
        members["specpilot/api/static/trace/assets/app.js"] = b"corrupted"
    else:
        members["specpilot/api/static/trace/index.html"] = b"corrupted"
    wheel = tmp_path / "specpilot.whl"
    _write_wheel(wheel, members)

    with pytest.raises(WheelVerificationError, match="trace bundle"):
        verify_trace_bundle(wheel, trace_root)


def test_wheel_verifier_rejects_an_empty_asset_set(tmp_path: Path) -> None:
    trace_root = tmp_path / "trace"
    trace_root.mkdir()
    (trace_root / "index.html").write_bytes(b"<main></main>")
    wheel = tmp_path / "specpilot.whl"
    _write_wheel(
        wheel,
        {"specpilot/api/static/trace/index.html": b"<main></main>"},
    )

    with pytest.raises(WheelVerificationError, match="asset set is empty"):
        verify_trace_bundle(wheel, trace_root)
