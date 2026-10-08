"""Safety regression tests: analyzed code must never be imported or executed."""

from __future__ import annotations

import ast
from pathlib import Path

import edgehunter
from edgehunter.analysis import analyze_repository_semantics
from edgehunter.pipeline import analyze_repository as analyze_repository_full
from edgehunter.scanner import analyze_repository

_FORBIDDEN_CALLS = frozenset(
    {"eval", "exec", "__import__", "import_module", "run_path", "literal_eval"}
)


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


def test_semantic_analysis_is_static_for_hostile_source(tmp_path: Path) -> None:
    marker = tmp_path / "MUST_NOT_EXIST.txt"
    source = (
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('bad')\n"
        "\n"
        "def dangerous(x):\n"
        "    if x > 10:\n"
        "        return x * 2\n"
        "    return 0\n"
    )
    target = tmp_path / "hostile.py"
    target.write_text(source, encoding="utf-8")

    analysis = analyze_repository_semantics(target)

    assert not marker.exists()
    assert analysis.predicate_count == 1
    assert analysis.comparison_count == 1
    assert analysis.arithmetic_expression_count == 1
    assert analysis.boundary_candidate_count > 0


def test_package_source_contains_no_execution_primitives() -> None:
    package_root = Path(edgehunter.__file__).resolve().parent
    offenders: list[str] = []

    for source_path in sorted(package_root.rglob("*.py")):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _call_name(node.func) in _FORBIDDEN_CALLS:
                offenders.append(f"{source_path}:{node.lineno}")

    assert offenders == []


def test_oracle_mining_never_executes_target_or_test_code(tmp_path: Path) -> None:
    marker = tmp_path / "MUST_NOT_EXIST.txt"
    (tmp_path / "service.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('bad')\n"
        "\n"
        "def dangerous():\n"
        "    raise RuntimeError('must never run')\n",
        encoding="utf-8",
    )
    (tmp_path / "test_service.py").write_text(
        "def test_dangerous():\n    assert dangerous() == 1\n",
        encoding="utf-8",
    )

    analysis = analyze_repository_full(tmp_path)

    assert not marker.exists()
    assert analysis.oracles.resolved_test_expectation_count == 1
    assert analysis.risk.scored_target_count >= 1


def test_validation_exception_in_test_file_is_not_raised(tmp_path: Path) -> None:
    marker = tmp_path / "SIDE_EFFECT.txt"
    (tmp_path / "service.py").write_text(
        "from pathlib import Path\n"
        "\n"
        "def parse(value):\n"
        f"    Path({str(marker)!r}).write_text(value)\n"
        "    raise ValueError(value)\n",
        encoding="utf-8",
    )
    (tmp_path / "test_service.py").write_text(
        "def test_parse():\n    with pytest.raises(ValueError):\n        parse('x')\n",
        encoding="utf-8",
    )

    analysis = analyze_repository_full(tmp_path)

    assert not marker.exists()
    expectation = analysis.oracles.test_expectations[0]
    assert expectation.exception == "ValueError"
    assert expectation.call_expression == "parse('x')"


def test_action_selection_never_executes_target_code(tmp_path: Path) -> None:
    marker = tmp_path / "MUST_NOT_EXIST.txt"
    (tmp_path / "service.py").write_text(
        "from pathlib import Path\n"
        "\n"
        "def withdraw(amount: float, balance: float):\n"
        f"    Path({str(marker)!r}).write_text(str(amount))\n"
        "    if amount <= balance:\n"
        "        raise ValueError(amount)\n"
        "    return balance - amount\n",
        encoding="utf-8",
    )

    analysis = analyze_repository_full(tmp_path)

    assert not marker.exists()
    assert analysis.generation.action_count > 0


def test_materialized_values_are_source_text_not_executed_objects(tmp_path: Path) -> None:
    marker = tmp_path / "MUST_NOT_EXIST.txt"
    (tmp_path / "service.py").write_text(
        "from pathlib import Path\n"
        "\n"
        "class Widget:\n"
        "    pass\n"
        "\n"
        "def explode(items: list[int], other: list[Widget]):\n"
        f"    Path({str(marker)!r}).write_text('bad')\n"
        "    return items, other\n",
        encoding="utf-8",
    )

    analysis = analyze_repository_full(tmp_path)

    values = [
        case.action.value_text
        for case in analysis.generation.cases
        if case.action.value_text is not None
    ]
    # A known element type materializes; the unknown element type does not.
    assert values == ["[]", "[0]", "[0, 0]", "[]"]
    assert all(isinstance(value, str) for value in values)
    assert "[None]" not in values
    assert not marker.exists()


def test_unrecognized_annotations_never_trigger_construction(tmp_path: Path) -> None:
    marker = tmp_path / "MUST_NOT_EXIST.txt"
    (tmp_path / "service.py").write_text(
        "from pathlib import Path\n"
        "\n"
        "class Widget:\n"
        f"    def __init__(self):\n        Path({str(marker)!r}).write_text('bad')\n"
        "\n"
        "def build(widget: Widget, payload: dict[str, int], flag: bool = False):\n"
        "    return widget, payload, flag\n",
        encoding="utf-8",
    )

    analysis = analyze_repository_full(tmp_path)

    assert not marker.exists()
    # ``dict[str, int]`` is now recognized, so the payload contributes an empty
    # and a singleton dict. No ``Widget`` is ever constructed, and the unknown
    # ``widget`` parameter simply stays missing.
    assert analysis.generation.action_count == 2
    assert analysis.generation.case_count == 2
    for case in analysis.generation.cases:
        assert case.parameter == "payload"
        assert case.missing_parameters == ("widget",)


def _call_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None
