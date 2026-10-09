"""Structural safety invariants for the new evaluation package.

The evaluator must never be able to reach an interpreter, because its whole value
is that a contradiction is reported only when the repository's own evidence says
so. If it could ``eval`` a target expression, a recorded expectation could be
satisfied by executing the very code it is meant to check.

These tests walk the package's own AST rather than grepping text, so prose in a
docstring is not mistaken for a dependency, and they fail loudly if a future change
adds the capability back.
"""

from __future__ import annotations

import ast
from pathlib import Path

import edgehunter

PACKAGE_ROOT = Path(edgehunter.__file__).parent
EVALUATION_ROOT = PACKAGE_ROOT / "evaluation"

EXECUTION_PRIMITIVES = frozenset(
    {
        "exec",
        "eval",
        "compile",
        "literal_eval",
        "__import__",
        "run_path",
        "load_module",
        "exec_module",
        "import_module",
        "system",
        "popen",
        "spawn",
        "fork",
    }
)

FORBIDDEN_MODULES = frozenset(
    {
        "subprocess",
        "importlib",
        "importlib.util",
        "runpy",
        "multiprocessing",
        "pickle",
        "marshal",
        "ctypes",
    }
)


def _modules() -> list[Path]:
    return sorted(EVALUATION_ROOT.rglob("*.py"))


def _called_name(call: ast.Call) -> str | None:
    function = call.func
    if isinstance(function, ast.Name):
        return function.id
    if isinstance(function, ast.Attribute):
        return function.attr
    return None


def test_the_package_is_actually_walked() -> None:
    """Guard against a vacuous pass if this file stops describing real modules."""
    modules = _modules()

    assert len(modules) >= 6
    assert {module.name for module in modules} >= {
        "__init__.py",
        "applicability.py",
        "compare.py",
        "evaluator.py",
        "exceptions.py",
        "findings.py",
        "models.py",
        "repository.py",
        "values.py",
    }


def test_no_module_in_the_evaluator_can_execute_code() -> None:
    offenders: list[str] = []
    for module in _modules():
        for call in ast.walk(ast.parse(module.read_text(encoding="utf-8"))):
            if isinstance(call, ast.Call) and _called_name(call) in EXECUTION_PRIMITIVES:
                offenders.append(f"{module.name}: line {call.lineno}")

    assert offenders == []


def test_no_module_in_the_evaluator_imports_a_process_or_deserializer() -> None:
    offenders: list[str] = []
    for module in _modules():
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names.extend(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.append(node.module.split(".")[0])
            for name in names:
                if name in FORBIDDEN_MODULES:
                    offenders.append(f"{module.name}: imports {name}")

    assert offenders == []


def test_the_evaluator_does_not_import_the_docker_backend() -> None:
    """Evaluation is pure host-side logic. Reaching for the backend here would mean
    the evaluator could start a container, which would break the property that it is
    testable without executing untrusted code."""
    offenders: list[str] = []
    for module in _modules():
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("execution"):
                if node.module and "docker" in node.module:
                    offenders.append(f"{module.name}: from {node.module}")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if "execution.docker" in alias.name:
                        offenders.append(f"{module.name}: import {alias.name}")

    assert offenders == []


def test_the_evaluator_does_not_import_the_worker() -> None:
    offenders: list[str] = []
    for module in _modules():
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.endswith("runtime.worker"):
                        offenders.append(f"{module.name}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module_name = node.module or ""
                if module_name.endswith("runtime.worker"):
                    offenders.append(f"{module.name}: from {module_name}")

    assert offenders == []


def test_the_static_pipeline_does_not_import_evaluation() -> None:
    """`analyze_repository` must stay static. If the scan path pulled in the
    evaluator it could grow the ability to execute, which is exactly the boundary
    the scan path exists to hold."""
    pipeline = (PACKAGE_ROOT / "pipeline.py").read_text(encoding="utf-8")
    tree = ast.parse(pipeline)
    imported = [
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("evaluation")
    ]

    assert imported == []


def test_comparison_never_uses_string_matching_for_values() -> None:
    """The comparison path must delegate to Python's operators.

    A substring or type-name comparison would be a re-implementation of equality
    that drifts from the language, which is how a passing assertion gets reported as
    a mismatch.
    """
    source = (EVALUATION_ROOT / "compare.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    comparison_calls = [
        call
        for call in ast.walk(tree)
        if isinstance(call, ast.Call) and _called_name(call) in {"startswith", "endswith", "find"}
    ]

    assert comparison_calls == []


def test_recognized_literals_go_through_the_protocol_whitelist() -> None:
    """The evaluator must not have its own literal parser.

    Reusing the Phase 6A recognizer is what guarantees a recorded expression can
    never reach anything that could evaluate it.
    """
    offenders: list[str] = []
    for module in _modules():
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module in {"ast", "json"}:
                    names = {alias.name for alias in node.names}
                    if "parse" in names or "loads" in names or "load" in names:
                        offenders.append(
                            f"{module.name}: from {node.module} import {sorted(names)}"
                        )

    assert offenders == []


def test_every_finding_kind_avoids_severity_language() -> None:
    """The vocabulary itself is a safety property.

    `CRITICAL_BUG` or `CONFIRMED_VULNERABILITY` members would invite output that
    claims more than any amount of evidence here supports.
    """
    from edgehunter.evaluation.models import FindingKind

    names = {member.name for member in FindingKind}
    forbidden = {"CRITICAL", "CONFIRMED_VULNERABILITY", "SEVERE", "CRITICAL_BUG", "VULNERABILITY"}

    assert names.isdisjoint(forbidden)


def test_reproduction_is_off_by_default() -> None:
    """Nothing retries on its own. A default above zero would mean EdgeHunter
    silently re-executes untrusted code."""
    from edgehunter.evaluation.repository import DEFAULT_MAX_RECHECKS, HARD_MAX_RECHECKS

    assert DEFAULT_MAX_RECHECKS == 0
    assert HARD_MAX_RECHECKS >= 1


def test_recheck_counts_are_clamped() -> None:
    from edgehunter.execution.repository import RunOptions

    assert RunOptions(target="f").bounded_rechecks() == 0
    assert RunOptions(target="f", rechecks=3).bounded_rechecks() == 3
    assert RunOptions(target="f", rechecks=-4).bounded_rechecks() == 0
    assert RunOptions(target="f", rechecks=1000).attempts_per_plan <= 6


def test_the_evaluator_is_importable_without_docker() -> None:
    """Importing the evaluator must not require or consult a container backend."""
    import subprocess
    import sys

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import edgehunter.evaluation as e; print(e.OracleStatus.MISMATCH.value)",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "mismatch"
