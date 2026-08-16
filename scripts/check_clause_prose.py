"""Refuse specification prose in a tracked file.

Section 8.1 keeps clause text out of anything git tracks, and AGENTS.md records
that this also discharges an IETF TLP condition — so it is a licence rule, not
only hygiene. The repository is public, and until this existed the rule was
enforced by remembering it. It was checked once, by hand, immediately before the
first push, and found two violations: an overlap fixture that had held a clause
sentence since W1, and a test written the same day that pasted a clause in as a
literal so it could assert the literal's absence.

The discriminator is capitalisation. RFC 2119 keywords are uppercase in
specification text by convention, and this project writes its own prose — prompt
instructions, docstrings, commit bodies — in lowercase. So an uppercase MUST
inside a long string literal is a quotation; the same word in a docstring
sentence is the project talking about requirements, and the same word in a short
literal is usually a parameter such as `keywords=("MUST NOT",)`.

Only string literals are inspected, and only long ones. That is where both
violations were, it is where a pasted excerpt naturally lands, and it keeps the
check from arguing with the prose that explains the rule.

Synthetic fixtures that imitate a clause declare themselves with a
`# synthetic-spec-text` comment on or above the statement. The marker is the
point: a fabricated RFC 9999 and a pasted RFC 9112 sentence are
indistinguishable to any rule that reads the text, so the author states which
one it is, once, where the fixture lives. A quotation pasted in later carries no
marker and fails.

Usage:  python scripts/check_clause_prose.py [paths...]
        (defaults to every tracked .py file)
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

# Uppercase only. Lowercase "must" is this project describing a requirement;
# uppercase MUST is a specification stating one.
BCP14 = (
    "MUST NOT",
    "MUST",
    "SHALL NOT",
    "SHALL",
    "SHOULD NOT",
    "SHOULD",
    "REQUIRED",
    "RECOMMENDED",
)
# Twelve words is longer than any keyword argument and shorter than every clause
# in the frozen corpus, whose median runs past fifty.
MIN_WORDS = 12
MARKER = "synthetic-spec-text"


def _tracked_python_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "*.py"], capture_output=True, text=True, check=True
    )
    return [Path(line) for line in out.stdout.splitlines() if line.strip()]


def _mark_parents(path: Path) -> ast.AST | None:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError):
        return None
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            child.parent = parent  # type: ignore[attr-defined]
    return tree


def _declared_synthetic(lines: list[str], node: ast.Constant) -> bool:
    """True when the fixture says it invented the text.

    Read from the statement's own lines and the line above, so the declaration
    sits with the fixture instead of in a registry that drifts from it.
    """
    end = min(getattr(node, "end_lineno", node.lineno), len(lines))
    if any(MARKER in line for line in lines[node.lineno - 1 : end]):
        return True
    # Otherwise walk up through the comment block directly above the statement.
    # Counting lines instead would make the window a magic number that a longer
    # explanation quietly outgrows — which it did, at five.
    index = node.lineno - 2
    while index >= 0:
        stripped = lines[index].strip()
        if stripped.startswith("#"):
            if MARKER in stripped:
                return True
            index -= 1
            continue
        if stripped == "" or not stripped.endswith(("(", "=", "= (")):
            break
        index -= 1
    return False


def _scan(path: Path) -> list[tuple[int, str]]:
    tree = _mark_parents(path)
    if tree is None:
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(
            node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
        )
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if id(node) in docstrings:
            continue
        if len(node.value.split()) < MIN_WORDS:
            continue
        if _declared_synthetic(lines, node):
            continue
        for keyword in BCP14:
            if keyword in node.value:
                found.append((node.lineno, keyword))
                break
    return found


def main(argv: list[str]) -> int:
    paths = [Path(item) for item in argv[1:]] or _tracked_python_files()
    violations = 0
    for path in sorted(paths):
        for line, keyword in _scan(path):
            print(
                f"{path}:{line}: long string literal containing {keyword} — "
                "specification prose may not enter a tracked file (§8.1)"
            )
            violations += 1
    if violations:
        print(f"\n{violations} literal(s) look like quoted specification text.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
