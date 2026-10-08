"""Tests for the constrained semantic action vocabulary."""

from __future__ import annotations

from pathlib import Path

import pytest

from edgehunter.analysis import RepositorySemantics, analyze_repository_semantics
from edgehunter.analysis.models import BoundaryKind
from edgehunter.generation import (
    BOUNDARY_ACTIONS,
    ActionOrigin,
    AnnotationCategory,
    MaterializationStatus,
    SemanticAction,
    SemanticActionKind,
    TargetActions,
    classify_annotation,
    select_module_actions,
)
from edgehunter.generation.actions import required_parameters
from edgehunter.scanner import ModuleAnalysis

SOURCE = """def discount(amount: int, rate: float = 0.1):
    if amount <= 0:
        raise ValueError(amount)
    if amount > 100:
        return amount * rate
    return amount


def clamp(value: float, limit: float):
    if value < limit:
        return limit
    return value


def merge(items: list, index: dict):
    return items, index


def unrecognized(flag, custom: Widget, nums: list[int], maybe: Optional[int]):
    if flag:
        return 1
    return 2


class Account:
    def withdraw(self, amount: float, balance: float):
        if amount <= balance:
            raise ValueError("insufficient")
        return balance - amount
"""


def _actions(tmp_path: Path, source: str = SOURCE) -> dict[str, tuple[SemanticAction, ...]]:
    """Run the analysis and return the actions of every target, keyed by name."""
    target = tmp_path / "service.py"
    target.write_text(source, encoding="utf-8")
    semantics = analyze_repository_semantics(target)
    selected: list[TargetActions] = []
    for module in semantics.modules:
        selected.extend(select_module_actions(_module_of(semantics, module.path), module))
    return {entry.target: entry.actions for entry in selected}


def _module_of(semantics: RepositorySemantics, path: Path) -> ModuleAnalysis:
    """Return the structural analysis for one module path."""
    for module in semantics.structure.modules:
        if module.path == path:
            return module
    raise AssertionError(f"no structural analysis for {path}")


def _values(
    actions: tuple[SemanticAction, ...], parameter: str
) -> dict[SemanticActionKind, str | None]:
    """Map action kind to materialized value for one parameter."""
    return {action.kind: action.value_text for action in actions if action.parameter == parameter}


def test_boundary_kind_maps_onto_every_action_kind() -> None:
    assert set(BOUNDARY_ACTIONS) == set(BoundaryKind)
    assert len(set(BOUNDARY_ACTIONS.values())) == len(BOUNDARY_ACTIONS)


def test_integer_generic_values_follow_the_declared_type(tmp_path: Path) -> None:
    values = _values(_actions(tmp_path)["discount"], "amount")

    assert values[SemanticActionKind.SET_ZERO] == "0"
    assert values[SemanticActionKind.SET_ONE] == "1"
    assert values[SemanticActionKind.SET_NEGATIVE_ONE] == "-1"


def test_float_generic_values_are_special_floats(tmp_path: Path) -> None:
    values = _values(_actions(tmp_path)["discount"], "rate")

    assert values[SemanticActionKind.SET_ZERO] == "0.0"
    assert values[SemanticActionKind.SET_NEGATIVE_ZERO] == "-0.0"
    assert values[SemanticActionKind.USE_NAN] == 'float("nan")'
    assert values[SemanticActionKind.USE_POSITIVE_INFINITY] == 'float("inf")'
    assert values[SemanticActionKind.USE_NEGATIVE_INFINITY] == 'float("-inf")'


def test_integer_boundaries_use_exact_neighbours(tmp_path: Path) -> None:
    actions = _actions(tmp_path)

    pairs = {
        (action.kind, action.reference): action.value_text
        for action in actions["discount"]
        if action.parameter == "amount" and action.reference is not None
    }
    assert pairs[(SemanticActionKind.JUST_BELOW_BOUNDARY, "0")] == "-1"
    assert pairs[(SemanticActionKind.AT_BOUNDARY, "0")] == "0"
    assert pairs[(SemanticActionKind.JUST_ABOVE_BOUNDARY, "0")] == "1"
    assert pairs[(SemanticActionKind.JUST_BELOW_BOUNDARY, "100")] == "99"
    assert pairs[(SemanticActionKind.JUST_ABOVE_BOUNDARY, "100")] == "101"


def test_symbolic_reference_stays_symbolic(tmp_path: Path) -> None:
    actions = _actions(tmp_path)

    below = next(
        action
        for action in actions["clamp"]
        if action.kind is SemanticActionKind.JUST_BELOW_BOUNDARY
    )
    assert below.status is MaterializationStatus.SYMBOLIC
    assert below.value_text is None
    assert below.symbolic_text == "limit"
    assert "without knowing its runtime value" in below.materialization_reason


def test_collection_annotations_drive_collection_actions(tmp_path: Path) -> None:
    actions = _actions(tmp_path)

    items = _values(actions["merge"], "items")
    index = _values(actions["merge"], "index")
    assert items[SemanticActionKind.EMPTY_COLLECTION] == "[]"
    assert items[SemanticActionKind.SINGLETON_COLLECTION] == "[None]"
    assert index[SemanticActionKind.EMPTY_COLLECTION] == "{}"
    assert index[SemanticActionKind.SINGLETON_COLLECTION] == "{None: None}"


def test_collection_actions_come_from_the_annotation(tmp_path: Path) -> None:
    actions = _actions(tmp_path)

    for action in actions["merge"]:
        assert action.origin is ActionOrigin.ANNOTATION
        assert action.boundary_kind is None
        assert action.reference is None
        assert action.evidence == ()


def test_unrecognized_annotations_get_no_generic_action(tmp_path: Path) -> None:
    actions = _actions(tmp_path)

    assert "unrecognized" not in actions


def test_untyped_parameter_still_gets_relational_actions(tmp_path: Path) -> None:
    actions = _actions(
        tmp_path,
        "def compare(a, b):\n    if a < b:\n        return a\n    return b\n",
    )

    assert {action.kind for action in actions["compare"]} == {
        SemanticActionKind.JUST_BELOW_BOUNDARY,
        SemanticActionKind.AT_BOUNDARY,
        SemanticActionKind.JUST_ABOVE_BOUNDARY,
    }


def test_unknown_annotations_stay_unknown() -> None:
    assert classify_annotation(None) is AnnotationCategory.UNKNOWN
    assert classify_annotation("Widget") is AnnotationCategory.UNKNOWN
    assert classify_annotation("list[int]") is AnnotationCategory.UNKNOWN
    assert classify_annotation("Optional[int]") is AnnotationCategory.UNKNOWN
    assert classify_annotation("list") is AnnotationCategory.LIST


def test_untyped_parameter_is_reported_as_unknown(tmp_path: Path) -> None:
    actions = _actions(tmp_path)

    assert all(
        action.annotation_category is AnnotationCategory.UNKNOWN for action in actions["discount"]
    )


def test_receiver_is_not_counted_as_a_required_parameter(tmp_path: Path) -> None:
    actions = _actions(tmp_path)

    entry_actions = actions["Account.withdraw"]
    assert entry_actions
    assert all(action.parameter != "self" for action in entry_actions)


def test_actions_are_ordered_by_parameter_and_vocabulary(tmp_path: Path) -> None:
    keys = [(action.parameter, action.kind.value) for action in _actions(tmp_path)["clamp"]]
    assert [key[0] for key in keys] == sorted(key[0] for key in keys)
    assert keys[0][0] == "limit"
    assert keys[-1][0] == "value"


def test_actions_are_deduplicated(tmp_path: Path) -> None:
    actions = _actions(
        tmp_path,
        "def repeat(amount: int):\n"
        "    if amount > 5:\n"
        "        return amount\n"
        "    if amount < 5:\n"
        "        return amount\n"
        "    return amount\n",
    )

    keys = [(action.parameter, action.kind, action.reference) for action in actions["repeat"]]
    assert len(keys) == len(set(keys))


def test_evidence_lines_are_recorded_for_boundary_actions(tmp_path: Path) -> None:
    actions = _actions(tmp_path)

    at_boundary = next(
        action
        for action in actions["discount"]
        if action.parameter == "amount" and action.kind is SemanticActionKind.SET_ZERO
    )
    assert at_boundary.evidence
    assert all(entry.detail for entry in at_boundary.evidence)
    assert all(entry.relative_path.as_posix() == "service.py" for entry in at_boundary.evidence)


@pytest.mark.parametrize(
    ("annotation", "expected"),
    [
        ("list", "[]"),
        ("tuple", "()"),
        ("dict", "{}"),
        ("set", "set()"),
        ("frozenset", "frozenset()"),
    ],
)
def test_every_recognized_collection_has_an_empty_value(
    tmp_path: Path, annotation: str, expected: str
) -> None:
    actions = _actions(tmp_path, f"def take(value: {annotation}):\n    return value\n")

    empty = next(
        action for action in actions["take"] if action.kind is SemanticActionKind.EMPTY_COLLECTION
    )
    assert empty.value_text == expected


def test_defaulted_parameter_is_not_required(tmp_path: Path) -> None:
    target = tmp_path / "service.py"
    target.write_text(
        "def discount(amount: int, rate: float = 0.1):\n"
        "    if amount <= 0:\n"
        "        return rate\n"
        "    return rate\n",
        encoding="utf-8",
    )
    semantics = analyze_repository_semantics(target)

    function = semantics.structure.modules[0].functions[0]
    assert required_parameters(function) == ("amount",)
