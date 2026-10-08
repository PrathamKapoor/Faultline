"""Tests for deterministic boundary-candidate mining."""

from __future__ import annotations

from pathlib import Path

from edgehunter.analysis import (
    BoundaryKind,
    BoundaryOrigin,
    ModuleSemantics,
    TargetSemantics,
    analyze_repository_semantics,
    extract_module_semantics,
)
from edgehunter.scanner import ModuleAnalysis, build_module_analysis, parse_source_file


def analyze(tmp_path: Path, source: str, name: str = "module.py") -> ModuleSemantics:
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    parsed = parse_source_file(path, Path(name))
    module: ModuleAnalysis = build_module_analysis(parsed)
    return extract_module_semantics(parsed, module)


def candidates_for(semantics: ModuleSemantics, qualified_name: str) -> list[tuple[str, ...]]:
    for entry in semantics.boundaries:
        if entry.qualified_name == qualified_name:
            return [
                (candidate.parameter, candidate.kind.value, candidate.reference or "")
                for candidate in entry.candidates
            ]
    raise AssertionError(f"no boundaries for: {qualified_name}")


def target_of(semantics: ModuleSemantics, qualified_name: str) -> TargetSemantics:
    for target in semantics.targets:
        if target.qualified_name == qualified_name:
            return target
    raise AssertionError(f"target not found: {qualified_name}")


def test_int_parameter_gets_generic_numeric_candidates(tmp_path: Path) -> None:
    semantics = analyze(tmp_path, "def f(x: int):\n    ...\n")

    kinds = [kind for _, kind, _ in candidates_for(semantics, "f")]
    assert kinds == ["zero", "one", "negative_one"]


def test_float_parameter_gets_generic_float_candidates(tmp_path: Path) -> None:
    semantics = analyze(tmp_path, "def f(x: float):\n    ...\n")

    kinds = [kind for _, kind, _ in candidates_for(semantics, "f")]
    assert kinds == [
        "zero",
        "one",
        "negative_one",
        "negative_zero",
        "nan",
        "positive_infinity",
        "negative_infinity",
    ]


def test_unannotated_parameter_gets_no_generic_candidates(tmp_path: Path) -> None:
    semantics = analyze(tmp_path, "def f(x):\n    ...\n")

    assert semantics.boundaries == ()


def test_complex_annotation_gets_no_generic_candidates(tmp_path: Path) -> None:
    source = "from typing import Optional\n\n\ndef f(x: Optional[int]):\n    ...\n"

    semantics = analyze(tmp_path, source)

    assert semantics.boundaries == ()


def test_var_positional_annotation_gets_no_generic_candidates(tmp_path: Path) -> None:
    semantics = analyze(tmp_path, "def f(*args: int):\n    ...\n")

    assert semantics.boundaries == ()


def test_inequality_produces_below_at_above(tmp_path: Path) -> None:
    semantics = analyze(tmp_path, "def f(x: int):\n    if x < 10:\n        ...\n")

    comparison_candidates = [entry for entry in candidates_for(semantics, "f") if entry[2] == "10"]
    assert comparison_candidates == [
        ("x", "just_below", "10"),
        ("x", "at_boundary", "10"),
        ("x", "just_above", "10"),
    ]


def test_equality_produces_only_at_boundary(tmp_path: Path) -> None:
    semantics = analyze(tmp_path, "def f(x: int):\n    if x == 10:\n        ...\n")

    comparison_candidates = [entry for entry in candidates_for(semantics, "f") if entry[2] == "10"]
    assert comparison_candidates == [("x", "at_boundary", "10")]


def test_not_equal_produces_only_at_boundary(tmp_path: Path) -> None:
    semantics = analyze(tmp_path, "def f(x: int):\n    if x != 10:\n        ...\n")

    comparison_candidates = [entry for entry in candidates_for(semantics, "f") if entry[2] == "10"]
    assert comparison_candidates == [("x", "at_boundary", "10")]


def test_reversed_comparison_is_normalized_to_parameter(tmp_path: Path) -> None:
    semantics = analyze(tmp_path, "def f(x: int):\n    if 0 <= x:\n        ...\n")

    comparison_candidates = [entry for entry in candidates_for(semantics, "f") if entry[2] == "0"]
    assert comparison_candidates == [
        ("x", "just_below", "0"),
        ("x", "at_boundary", "0"),
        ("x", "just_above", "0"),
    ]


def test_chained_comparison_yields_boundaries_around_both_limits(tmp_path: Path) -> None:
    semantics = analyze(tmp_path, "def f(x: int):\n    if 0 < x <= 10:\n        ...\n")

    references = {entry[2] for entry in candidates_for(semantics, "f") if entry[2]}
    assert references == {"0", "10"}
    for reference in ("0", "10"):
        kinds = {entry[1] for entry in candidates_for(semantics, "f") if entry[2] == reference}
        assert kinds == {"just_below", "at_boundary", "just_above"}


def test_parameter_to_parameter_boundary_stays_symbolic(tmp_path: Path) -> None:
    source = "def withdraw(balance: int, amount: int):\n    if amount <= balance:\n        ...\n"

    semantics = analyze(tmp_path, source)

    assert candidates_for(semantics, "withdraw") == [
        ("amount", "just_below", "balance"),
        ("amount", "at_boundary", "balance"),
        ("amount", "just_above", "balance"),
        ("amount", "zero", ""),
        ("amount", "one", ""),
        ("amount", "negative_one", ""),
        ("balance", "zero", ""),
        ("balance", "one", ""),
        ("balance", "negative_one", ""),
    ]


def test_attribute_reference_stays_symbolic(tmp_path: Path) -> None:
    source = (
        "class Counter:\n"
        "    def valid(self, x: int):\n"
        "        if x <= self.limit:\n"
        "            ...\n"
    )

    semantics = analyze(tmp_path, source)

    comparison_candidates = [
        entry for entry in candidates_for(semantics, "Counter.valid") if entry[2] == "self.limit"
    ]
    assert comparison_candidates == [
        ("x", "just_below", "self.limit"),
        ("x", "at_boundary", "self.limit"),
        ("x", "just_above", "self.limit"),
    ]


def test_both_sides_parameters_prefers_left_side(tmp_path: Path) -> None:
    source = "def f(x: int, y: int):\n    if x <= y:\n        ...\n"

    semantics = analyze(tmp_path, source)

    relational = [entry for entry in candidates_for(semantics, "f") if entry[2]]
    assert relational == [
        ("x", "just_below", "y"),
        ("x", "at_boundary", "y"),
        ("x", "just_above", "y"),
    ]


def test_call_reference_is_never_invoked_and_produces_no_candidate(tmp_path: Path) -> None:
    semantics = analyze(tmp_path, "def f(x):\n    if x < dangerous():\n        return x\n")

    assert target_of(semantics, "f").comparisons[0].right.text == "dangerous()"
    assert semantics.boundaries == ()


def test_complex_reference_produces_no_candidate(tmp_path: Path) -> None:
    semantics = analyze(tmp_path, "def f(x):\n    if x <= values[index + 1]:\n        ...\n")

    assert target_of(semantics, "f").comparisons[0].right.text == "values[index + 1]"
    assert semantics.boundaries == ()


def test_non_numeric_literal_inequality_produces_no_candidate(tmp_path: Path) -> None:
    semantics = analyze(tmp_path, "def f(role: str):\n    if role > 'admin':\n        ...\n")

    assert semantics.boundaries == ()


def test_string_equality_produces_only_at_boundary(tmp_path: Path) -> None:
    semantics = analyze(tmp_path, "def f(role: str):\n    if role == 'admin':\n        ...\n")

    assert candidates_for(semantics, "f") == [("role", "at_boundary", "'admin'")]


def test_identity_and_membership_produce_no_candidates(tmp_path: Path) -> None:
    source = (
        "def f(x, allowed):\n    if x is None:\n        ...\n    if x in allowed:\n        ...\n"
    )

    semantics = analyze(tmp_path, source)

    assert semantics.boundaries == ()


def test_identity_with_parameter_on_the_right_is_not_inverted(tmp_path: Path) -> None:
    source = "def f(x, other):\n    if None is other:\n        ...\n"

    semantics = analyze(tmp_path, source)

    assert semantics.boundaries == ()


def test_non_parameter_subject_produces_no_comparison_candidate(tmp_path: Path) -> None:
    source = "def f(x: int):\n    if config.limit < 10:\n        ...\n"

    semantics = analyze(tmp_path, source)

    comparison_candidates = [entry for entry in candidates_for(semantics, "f") if entry[2]]
    assert comparison_candidates == []


def test_candidates_are_deduplicated_and_collect_evidence(tmp_path: Path) -> None:
    source = "def f(x: int):\n    if x > 0:\n        ...\n    if x >= 0:\n        ...\n"

    semantics = analyze(tmp_path, source)

    entry = semantics.boundaries[0]
    at_zero = [
        candidate
        for candidate in entry.candidates
        if candidate.kind is BoundaryKind.AT_BOUNDARY and candidate.reference == "0"
    ]
    assert len(at_zero) == 1
    assert at_zero[0].evidence == (2, 4)
    generic = [
        candidate for candidate in entry.candidates if candidate.origin is BoundaryOrigin.NUMERIC
    ]
    assert len({candidate.kind for candidate in generic}) == len(generic)


def test_boundary_ordering_is_deterministic(tmp_path: Path) -> None:
    source = (
        "def zeta(z: float):\n"
        "    if z <= 5:\n"
        "        ...\n"
        "\n"
        "def alpha(a: float):\n"
        "    if a >= 2:\n"
        "        ...\n"
    )

    first = candidates_for(analyze(tmp_path, source), "alpha")
    second = candidates_for(analyze(tmp_path, source), "alpha")

    assert first == second
    assert [entry[1] for entry in first if entry[2] == "2"] == [
        "just_below",
        "at_boundary",
        "just_above",
    ]


def test_generic_candidates_come_after_comparison_candidates(tmp_path: Path) -> None:
    semantics = analyze(tmp_path, "def f(x: float):\n    if x <= 3:\n        ...\n")

    kinds = [entry[1] for entry in candidates_for(semantics, "f")]
    assert kinds[:3] == ["just_below", "at_boundary", "just_above"]
    assert kinds[3:] == [
        "zero",
        "one",
        "negative_one",
        "negative_zero",
        "nan",
        "positive_infinity",
        "negative_infinity",
    ]


def test_repository_scan_is_deterministic(tmp_path: Path) -> None:
    (tmp_path / "b.py").write_text(
        "def second(x: int):\n    if x > 1:\n        ...\n", encoding="utf-8"
    )
    (tmp_path / "a.py").write_text(
        "def first(x: float):\n    if x <= 1:\n        ...\n", encoding="utf-8"
    )

    first = analyze_repository_semantics(tmp_path)
    second = analyze_repository_semantics(tmp_path)

    assert first == second
    assert first.boundary_candidate_count > 0
    assert first.targets_with_boundaries == len(first.boundaries)
