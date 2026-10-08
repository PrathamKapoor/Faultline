"""Tests for candidate cases and repository-level generation aggregation."""

from __future__ import annotations

from pathlib import Path

from edgehunter.analysis import RepositorySemantics, analyze_repository_semantics
from edgehunter.generation import (
    ACTION_KIND_ORDER,
    CandidateCase,
    CandidateCompleteness,
    MaterializationStatus,
    RepositoryGeneration,
    SemanticActionKind,
    build_repository_generation,
    select_module_actions,
)
from edgehunter.scanner import ModuleAnalysis

SOURCE = """def single(amount: int):
    if amount <= 0:
        return 0
    return amount


def pair(amount: int, rate: float = 0.1):
    if amount <= 0:
        return rate
    return rate


def collection(items: list):
    return items


def complex_ref(z: complex):
    if z > 1j:
        return z
    return 0j
"""


def _generation(tmp_path: Path, source: str = SOURCE) -> RepositoryGeneration:
    """Analyze one file and build the repository generation aggregate."""
    target = tmp_path / "service.py"
    target.write_text(source, encoding="utf-8")
    semantics = analyze_repository_semantics(target)
    module = _module_of(semantics, target)
    return build_repository_generation(select_module_actions(module, semantics.modules[0]))


def _module_of(semantics: RepositorySemantics, path: Path) -> ModuleAnalysis:
    for module in semantics.structure.modules:
        if module.path == path:
            return module
    raise AssertionError(f"no structural analysis for {path}")


def _cases(generation: RepositoryGeneration, target: str) -> tuple[CandidateCase, ...]:
    return tuple(case for case in generation.cases if case.target == target)


def test_single_parameter_target_produces_complete_cases(tmp_path: Path) -> None:
    generation = _generation(tmp_path)

    cases = _cases(generation, "single")
    assert cases
    assert all(case.completeness is CandidateCompleteness.COMPLETE for case in cases)
    assert all(case.missing_parameters == () for case in cases)


def test_case_leaves_other_required_parameters_uncovered(tmp_path: Path) -> None:
    generation = _generation(tmp_path)

    rate_cases = [
        case
        for case in _cases(generation, "pair")
        if case.parameter == "rate" and case.status is MaterializationStatus.CONCRETE
    ]
    assert rate_cases
    for case in rate_cases:
        assert case.completeness is CandidateCompleteness.PARTIAL
        assert case.missing_parameters == ("amount",)


def test_no_case_invents_a_value_for_another_parameter(tmp_path: Path) -> None:
    generation = _generation(tmp_path)

    for case in generation.cases:
        assert case.missing_parameters or case.completeness is CandidateCompleteness.COMPLETE


def test_symbolic_action_yields_a_symbolic_case(tmp_path: Path) -> None:
    generation = _generation(
        tmp_path,
        "def clamp(value: float, limit: float):\n"
        "    if value < limit:\n"
        "        return limit\n"
        "    return value\n",
    )

    assert generation.symbolic_case_count > 0
    symbolic = next(
        case for case in generation.cases if case.action.status is MaterializationStatus.SYMBOLIC
    )
    assert symbolic.completeness is CandidateCompleteness.SYMBOLIC
    assert symbolic.action.symbolic_text == "limit"


def test_unsupported_action_is_not_a_candidate_case(tmp_path: Path) -> None:
    generation = _generation(tmp_path)

    assert generation.unsupported_action_count == 2
    cases = _cases(generation, "complex_ref")
    assert [case.action.kind for case in cases] == [SemanticActionKind.AT_BOUNDARY]


def test_every_case_carries_exactly_one_action(tmp_path: Path) -> None:
    generation = _generation(tmp_path)

    for case in generation.cases:
        assert case.action.parameter == case.parameter
        assert case.action.kind is case.action.kind


def test_cases_are_ordered_deterministically(tmp_path: Path) -> None:
    generation = _generation(tmp_path)

    keys = [
        (
            case.relative_path.as_posix(),
            case.lineno,
            case.target,
            case.parameter,
            ACTION_KIND_ORDER[case.action.kind],
        )
        for case in generation.cases
    ]
    assert keys == sorted(keys)


def test_actions_are_ordered_by_vocabulary_not_alphabetically(tmp_path: Path) -> None:
    generation = _generation(tmp_path)

    kinds = [case.action.kind for case in _cases(generation, "single")]
    assert kinds[0] is SemanticActionKind.JUST_BELOW_BOUNDARY
    assert kinds[1] is SemanticActionKind.AT_BOUNDARY
    assert kinds[2] is SemanticActionKind.JUST_ABOVE_BOUNDARY


def test_counts_add_up(tmp_path: Path) -> None:
    generation = _generation(tmp_path)

    assert generation.action_count == generation.concrete_action_count + (
        generation.symbolic_action_count + generation.unsupported_action_count
    )
    assert generation.case_count == generation.complete_case_count + (
        generation.partial_case_count + generation.symbolic_case_count
    )
    assert generation.case_count == generation.action_count - generation.unsupported_action_count


def test_generation_is_repeatable(tmp_path: Path) -> None:
    first = _generation(tmp_path)
    second = _generation(tmp_path)

    assert first == second


def test_targets_without_evidence_are_omitted(tmp_path: Path) -> None:
    generation = _generation(
        tmp_path,
        "def quiet(value):\n    return value\n",
    )

    assert generation.action_count == 0
    assert generation.case_count == 0
    assert generation.targets_with_actions == 0


def test_collection_target_is_reported(tmp_path: Path) -> None:
    generation = _generation(tmp_path)

    kinds = {case.action.kind for case in _cases(generation, "collection")}
    assert kinds == {
        SemanticActionKind.EMPTY_COLLECTION,
        SemanticActionKind.SINGLETON_COLLECTION,
    }
