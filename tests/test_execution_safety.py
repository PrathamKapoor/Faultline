"""The host/worker execution boundary, asserted structurally.

Phase 6A permits exactly one module to import or invoke target code: the trusted
worker that runs inside the container. This suite walks the package's own AST on
the host and fails if any other module gains that power, so the invariant cannot
rot silently as the code changes.
"""

from __future__ import annotations

import ast
from pathlib import Path

import edgehunter

PACKAGE_ROOT = Path(edgehunter.__file__).parent

# The worker is the deliberate, reviewed exception. It runs inside the container
# and is never imported by host-side code paths.
ALLOWED_EXECUTORS = frozenset({"runtime/worker.py"})

# Call names that can load or run code from a file, a string, or the import system.
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
    }
)

# Modules a host-side component may never touch, because each can reach an
# interpreter or a process launcher.
FORBIDDEN_HOST_MODULES = frozenset(
    {
        "importlib",
        "importlib.util",
        "runpy",
        "pty",
        "popen2",
        "commands",
        "ctypes",
        "multiprocessing",
    }
)


def _modules() -> list[Path]:
    return sorted(PACKAGE_ROOT.rglob("*.py"))


def _qualified(module: Path) -> str:
    return module.relative_to(PACKAGE_ROOT).as_posix()


def _calls(tree: ast.AST) -> list[ast.Call]:
    return [node for node in ast.walk(tree) if isinstance(node, ast.Call)]


def _called_name(call: ast.Call) -> str | None:
    function = call.func
    if isinstance(function, ast.Name):
        return function.id
    if isinstance(function, ast.Attribute):
        return function.attr
    return None


def test_the_boundary_is_actually_exercised() -> None:
    """Guard against a vacuous pass if this file stops describing real modules."""
    modules = _modules()

    assert ALLOWED_EXECUTORS <= {_qualified(module) for module in modules}
    assert len(modules) > 10


def test_only_the_worker_may_execute_code() -> None:
    offenders: list[str] = []
    for module in _modules():
        if _qualified(module) in ALLOWED_EXECUTORS:
            continue
        for call in _calls(ast.parse(module.read_text(encoding="utf-8"))):
            if _called_name(call) in EXECUTION_PRIMITIVES:
                offenders.append(f"{_qualified(module)}: line {call.lineno}")

    assert offenders == []


def test_only_the_worker_may_import_the_import_system() -> None:
    offenders: list[str] = []
    for module in _modules():
        if _qualified(module) in ALLOWED_EXECUTORS:
            continue
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in FORBIDDEN_HOST_MODULES:
                        offenders.append(f"{_qualified(module)}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module.split(".")[0] in FORBIDDEN_HOST_MODULES:
                    offenders.append(f"{_qualified(module)}: from {node.module}")

    assert offenders == []


def test_host_code_never_imports_the_worker() -> None:
    """Importing the worker on the host would make it reachable from scan paths.

    Checked through the AST rather than the raw text so that prose describing the
    worker in a module docstring is not mistaken for a real dependency.
    """
    offenders: list[str] = []
    for module in _modules():
        relative = _qualified(module)
        if relative in ALLOWED_EXECUTORS:
            continue
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.endswith("runtime.worker"):
                        offenders.append(f"{relative}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                names = {alias.name for alias in node.names}
                module_name = node.module or ""
                if module_name.endswith("runtime.worker") or (
                    "worker" in names and "runtime" in module_name
                ):
                    offenders.append(f"{relative}: from {module_name} import {sorted(names)}")

    assert offenders == []


def test_only_subprocess_runs_the_docker_cli() -> None:
    """The only host process EdgeHunter may start is the Docker CLI itself."""
    offenders: list[str] = []
    execution = PACKAGE_ROOT / "execution"
    for module in sorted(execution.rglob("*.py")):
        for call in _calls(ast.parse(module.read_text(encoding="utf-8"))):
            function = call.func
            if _called_name(call) not in {"Popen", "call", "check_call", "check_output", "run"}:
                continue
            if isinstance(function, ast.Attribute) and isinstance(function.value, ast.Name):
                if function.value.id != "subprocess":
                    offenders.append(f"{_qualified(module)}: line {call.lineno}")

    assert offenders == []


def test_static_analysis_entry_point_does_not_import_execution() -> None:
    """``analyze_repository`` must stay a pure static-analysis entry point."""
    pipeline = (PACKAGE_ROOT / "pipeline.py").read_text(encoding="utf-8")
    tree = ast.parse(pipeline)
    imported = [
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("execution")
    ]

    assert imported == []


def test_no_pickle_or_object_deserialization_anywhere() -> None:
    """Values travel as typed JSON; no serializer may reconstruct target objects."""
    offenders: list[str] = []
    banned = {"loads", "load", "Unpickler", "marshal_loads"}
    for module in _modules():
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.ImportFrom) and node.module:
                names.append(node.module.split(".")[0])
            elif isinstance(node, ast.Import):
                names.extend(alias.name.split(".")[0] for alias in node.names)
            for name in names:
                if name in {"pickle", "cPickle", "marshal", "shelve", "dill"}:
                    offenders.append(f"{_qualified(module)}: imports {name}")
        for call in _calls(tree):
            if _called_name(call) in banned:
                function = call.func
                if isinstance(function, ast.Attribute) and isinstance(function.value, ast.Name):
                    if function.value.id in {"pickle", "marshal"}:
                        offenders.append(f"{_qualified(module)}: line {call.lineno}")

    assert offenders == []
