"""Safety regression tests: analyzed code must never be imported or executed."""

from __future__ import annotations

import ast
from pathlib import Path

import edgehunter
from edgehunter.scanner import analyze_repository

_FORBIDDEN_CALLS = frozenset({"eval", "exec", "__import__", "import_module", "run_path"})


def test_module_level_side_effects_are_never_executed(tmp_path: Path) -> None:
    marker = tmp_path / "executed.marker"
    source = (
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('executed')\n"
        "raise RuntimeError('THIS MUST NEVER RUN')\n"
    )
    target = tmp_path / "dangerous.py"
    target.write_text(source, encoding="utf-8")

    analysis = analyze_repository(target)

    assert analysis.modules[0].is_parsed is True
    assert not marker.exists()


def test_package_source_contains_no_execution_primitives() -> None:
    package_root = Path(edgehunter.__file__).resolve().parent
    offenders: list[str] = []

    for source_path in sorted(package_root.rglob("*.py")):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _call_name(node.func) in _FORBIDDEN_CALLS:
                offenders.append(f"{source_path}:{node.lineno}")

    assert offenders == []


def _call_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None
