"""Tests for static mining of expectations from existing test files."""

from __future__ import annotations

from pathlib import Path

from edgehunter.analysis.models import ComparisonOperator
from edgehunter.analysis.repository import analyze_discovered_files
from edgehunter.oracle import (
    ExpectedOutcome,
    OracleStrength,
    PropertyKind,
    TargetResolution,
    TestExpectationCandidate,
    deduplicate_expectations,
    extract_test_expectations,
    is_test_file,
    resolve_expectations,
)
from edgehunter.oracle.repository import build_target_index
from edgehunter.scanner.repository import scan_repository


def write_repo(tmp_path: Path, files: dict[str, str]) -> None:
    for name, source in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")


def mine(tmp_path: Path, files: dict[str, str]) -> tuple[TestExpectationCandidate, ...]:
    """Mine expectations from every file in ``files`` and resolve them.

    Mirrors the pipeline: per-module mining, then repository-wide deduplication,
    then resolution.
    """
    write_repo(tmp_path, files)
    analyzed = analyze_discovered_files(scan_repository(tmp_path))
    raw: list[TestExpectationCandidate] = []
    for item in analyzed:
        raw.extend(extract_test_expectations(item.source))
    index = build_target_index(tuple(item.module for item in analyzed))
    return resolve_expectations(deduplicate_expectations(raw), index)


def only(items: tuple[TestExpectationCandidate, ...]) -> TestExpectationCandidate:
    assert len(items) == 1, f"expected one expectation, got {len(items)}"
    return items[0]


def test_bare_assert_call_comparison_is_mined(tmp_path: Path) -> None:
    items = mine(
        tmp_path,
        {
            "math_utils.py": "def add(a, b):\n    return a + b\n",
            "test_math_utils.py": "def test_add():\n    assert add(1, 2) == 3\n",
        },
    )

    expectation = only(items)
    assert expectation.kind is PropertyKind.EXAMPLE_EXPECTATION
    assert expectation.callee == "add"
    assert expectation.call_expression == "add(1, 2)"
    assert expectation.comparison_operator is ComparisonOperator.EQUAL
    assert expectation.expected_expression == "3"
    assert expectation.expected_outcome is ExpectedOutcome.RETURNS
    assert expectation.strength is OracleStrength.STRONG
    assert expectation.target == "add"
    assert expectation.resolution is TargetResolution.RESOLVED


def test_reversed_call_side_is_normalized(tmp_path: Path) -> None:
    items = mine(
        tmp_path,
        {
            "math_utils.py": "def add(a, b):\n    return a + b\n",
            "test_math_utils.py": "def test_add():\n    assert 3 == add(1, 2)\n",
        },
    )

    expectation = only(items)
    assert expectation.call_expression == "add(1, 2)"
    assert expectation.comparison_operator is ComparisonOperator.EQUAL
    assert expectation.expected_expression == "3"


def test_reversed_ordered_comparison_uses_the_inverse_operator(tmp_path: Path) -> None:
    items = mine(
        tmp_path,
        {
            "m.py": "def check(x):\n    return x\n",
            "test_m.py": "def test_check():\n    assert 10 < check(1)\n",
        },
    )

    expectation = only(items)
    assert expectation.comparison_operator is ComparisonOperator.GREATER_THAN
    assert expectation.expected_expression == "10"


def test_identity_comparison_is_recorded_without_inversion(tmp_path: Path) -> None:
    items = mine(
        tmp_path,
        {
            "m.py": "def parse(value):\n    return value\n",
            "test_parse.py": "def test_parse():\n    assert parse('') is None\n",
        },
    )

    expectation = only(items)
    assert expectation.comparison_operator is ComparisonOperator.IS
    assert expectation.expected_expression == "None"
    assert expectation.call_expression == "parse('')"


def test_pytest_raises_is_mined(tmp_path: Path) -> None:
    items = mine(
        tmp_path,
        {
            "m.py": "def parse(value):\n    return value\n",
            "test_parse.py": (
                "import pytest\n"
                "\n"
                "def test_parse():\n"
                "    with pytest.raises(ValueError):\n"
                "        parse('')\n"
            ),
        },
    )

    expectation = only(items)
    assert expectation.kind is PropertyKind.EXAMPLE_RAISES
    assert expectation.expected_outcome is ExpectedOutcome.RAISES
    assert expectation.exception == "ValueError"
    assert expectation.call_expression == "parse('')"
    assert expectation.target == "parse"
    assert expectation.resolution is TargetResolution.RESOLVED


def test_pytest_raises_with_compound_body_is_ignored(tmp_path: Path) -> None:
    items = mine(
        tmp_path,
        {
            "m.py": "def parse(value):\n    return value\n",
            "test_parse.py": (
                "def test_parse():\n"
                "    with pytest.raises(ValueError):\n"
                "        value = parse('')\n"
                "        use(value)\n"
            ),
        },
    )

    assert items == ()


def test_ambiguous_callee_is_explicitly_ambiguous(tmp_path: Path) -> None:
    items = mine(
        tmp_path,
        {
            "a.py": "def parse(value):\n    ...\n",
            "b.py": "def parse(value):\n    ...\n",
            "test_parse.py": "def test_parse():\n    assert parse('x') is None\n",
        },
    )

    expectation = only(items)
    assert expectation.resolution is TargetResolution.AMBIGUOUS
    assert expectation.target is None
    assert "2 targets share this name" in expectation.resolution_reason


def test_dotted_receiver_stays_unresolved(tmp_path: Path) -> None:
    items = mine(
        tmp_path,
        {
            "m.py": "def parse(value):\n    return value\n",
            "test_service.py": "def test_parse():\n    assert service.parse('x') is None\n",
        },
    )

    expectation = only(items)
    assert expectation.callee == "service.parse"
    assert expectation.resolution is TargetResolution.UNRESOLVED
    assert expectation.target is None
    assert expectation.resolution_reason == "receiver resolution not implemented"


def test_unknown_callee_is_unresolved(tmp_path: Path) -> None:
    items = mine(
        tmp_path,
        {
            "m.py": "def other():\n    ...\n",
            "test_x.py": "def test_x():\n    assert missing(1) == 2\n",
        },
    )

    expectation = only(items)
    assert expectation.resolution is TargetResolution.UNRESOLVED
    assert expectation.resolution_reason == "no target with this name"


def test_non_test_file_produces_no_test_expectations(tmp_path: Path) -> None:
    items = mine(
        tmp_path,
        {
            "examples/demo.py": "def add(a, b):\n    return a + b\n\n\nassert add(1, 2) == 3\n",
            "m.py": "def add(a, b):\n    return a + b\n",
        },
    )

    assert items == ()


def test_unrecognized_assert_shape_is_ignored(tmp_path: Path) -> None:
    items = mine(
        tmp_path,
        {
            "m.py": "def add(a, b):\n    return a + b\n",
            "test_m.py": "def test_add():\n    assert add(1, 2)\n",
        },
    )

    assert items == ()


def test_chained_assert_is_ignored(tmp_path: Path) -> None:
    items = mine(
        tmp_path,
        {
            "m.py": "def between(x):\n    return x\n",
            "test_m.py": "def test_between():\n    assert 0 < between(1) <= 10\n",
        },
    )

    assert items == ()


def test_repeated_expectation_is_deduplicated_across_files(tmp_path: Path) -> None:
    items = mine(
        tmp_path,
        {
            "m.py": "def add(a, b):\n    return a + b\n",
            "test_one.py": "def test_add():\n    assert add(1, 2) == 3\n",
            "test_two.py": "def test_add_again():\n    assert add(1, 2) == 3\n",
        },
    )

    expectation = only(items)
    assert [entry.relative_path.as_posix() for entry in expectation.evidence] == [
        "test_one.py",
        "test_two.py",
    ]


def test_test_file_recognition_rule() -> None:
    assert is_test_file(Path("test_module.py")) is True
    assert is_test_file(Path("tests/module_test.py")) is True
    assert is_test_file(Path("examples/demo.py")) is False
    assert is_test_file(Path("src/testing_helper.py")) is False
