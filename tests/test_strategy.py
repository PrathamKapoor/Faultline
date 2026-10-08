"""Tests for the framework-independent strategy-plan layer."""

from __future__ import annotations

from pathlib import Path

from edgehunter.generation import ParameterStrategy, StrategyKind, StrategyPlan
from edgehunter.pipeline import analyze_repository

SIGNATURE_SOURCE = """class Custom:
    pass


def take(
    count: int,
    ratio: float,
    name: str,
    flag: bool,
    tags: list[int],
    cfg: dict[str, int],
    items: list[Custom],
    limit: int | None,
    everything: int | str,
    user: User,
    values,
):
    return count, ratio, name, flag, tags, cfg, items, limit, everything, user, values
"""


def _plan(tmp_path: Path, source: str = SIGNATURE_SOURCE, target: str = "take") -> StrategyPlan:
    (tmp_path / "service.py").write_text(source, encoding="utf-8")
    scan = analyze_repository(tmp_path)
    return next(entry for entry in scan.planning.strategies if entry.target == target)


def _strategy(plan: StrategyPlan, name: str) -> ParameterStrategy:
    return next(item for item in plan.parameters if item.parameter == name)


def _descriptors(plan: StrategyPlan, name: str) -> list[str]:
    return [value.descriptor for value in _strategy(plan, name).values]


def _texts(plan: StrategyPlan, name: str) -> list[str]:
    return [value.text for value in _strategy(plan, name).values]


def test_integer_strategy_uses_the_semantic_action_set(tmp_path: Path) -> None:
    plan = _plan(tmp_path)

    assert _strategy(plan, "count").kind is StrategyKind.SAMPLED_FROM
    assert _descriptors(plan, "count") == ["zero", "one", "negative_one"]
    assert _texts(plan, "count") == ["0", "1", "-1"]


def test_float_strategy_has_stable_semantic_descriptors(tmp_path: Path) -> None:
    plan = _plan(tmp_path)

    assert _descriptors(plan, "ratio") == [
        "zero",
        "one",
        "negative_one",
        "negative_zero",
        "nan",
        "positive_infinity",
        "negative_infinity",
    ]


def test_nan_is_not_deduplicated_by_equality(tmp_path: Path) -> None:
    """``NaN != NaN``; the plan must still list each float special exactly once."""
    plan = _plan(tmp_path)

    texts = _texts(plan, "ratio")
    assert texts.count('float("nan")') == 1
    assert len(texts) == len(set(texts))


def test_string_strategy_is_a_deterministic_edge_set(tmp_path: Path) -> None:
    plan = _plan(tmp_path)

    descriptors = _descriptors(plan, "name")
    assert descriptors == ["empty", "space", "unicode", "nul", "long"]
    assert _texts(plan, "name")[0] == '""'
    assert _texts(plan, "name")[1] == '" "'


def test_list_of_known_element_strategy(tmp_path: Path) -> None:
    plan = _plan(tmp_path)

    assert _descriptors(plan, "tags") == ["empty", "singleton", "repeated_element"]
    assert _texts(plan, "tags") == ["[]", "[0]", "[0, 0]"]


def test_dict_strategy(tmp_path: Path) -> None:
    plan = _plan(tmp_path)

    assert _texts(plan, "cfg") == ["{}", '{"": 0}']


def test_unknown_element_collection_strategy_omits_singletons(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    strategy = _strategy(plan, "items")

    assert _texts(plan, "items") == ["[]"]
    assert "[None]" not in _texts(plan, "items")
    assert strategy.annotation.description == "LIST[UNKNOWN]"


def test_optional_strategy_includes_none_and_the_primitives(tmp_path: Path) -> None:
    plan = _plan(tmp_path)

    assert _texts(plan, "limit") == ["0", "1", "-1", "None"]


def test_custom_type_strategy_is_unsupported(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    strategy = _strategy(plan, "user")

    assert strategy.kind is StrategyKind.UNSUPPORTED
    assert strategy.values == ()
    assert "not recognized" in strategy.reason


def test_untyped_parameter_strategy_is_unsupported(tmp_path: Path) -> None:
    plan = _plan(tmp_path)

    assert _strategy(plan, "values").kind is StrategyKind.UNSUPPORTED


def test_multi_type_union_is_unsupported(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    strategy = _strategy(plan, "everything")

    assert strategy.annotation.description == "UNION"
    assert strategy.kind is StrategyKind.UNSUPPORTED


def test_boundary_values_join_the_strategy(tmp_path: Path) -> None:
    (tmp_path / "service.py").write_text(
        "def clamp(value: int):\n    if value < 10:\n        return value\n    return 10\n",
        encoding="utf-8",
    )
    scan = analyze_repository(tmp_path)
    plan = next(entry for entry in scan.planning.strategies if entry.target == "clamp")

    assert _texts(plan, "value") == ["0", "1", "-1", "9", "10", "11"]
    assert "just_above.10" in _descriptors(plan, "value")


def test_symbolic_dependency_is_reported(tmp_path: Path) -> None:
    (tmp_path / "service.py").write_text(
        "def compare(x: int, limit: int):\n"
        "    if x <= limit:\n"
        "        return x\n"
        "    return limit\n",
        encoding="utf-8",
    )
    scan = analyze_repository(tmp_path)
    plan = next(entry for entry in scan.planning.strategies if entry.target == "compare")
    strategy = _strategy(plan, "x")

    assert strategy.kind is StrategyKind.SAMPLED_FROM
    assert strategy.depends_on == ("limit",)
    assert "depends on limit" in strategy.reason


def test_variadic_parameters_have_no_strategy(tmp_path: Path) -> None:
    (tmp_path / "service.py").write_text(
        "def f(x: int, *items, **options):\n    if x > 0:\n        return x\n    return 0\n",
        encoding="utf-8",
    )
    scan = analyze_repository(tmp_path)
    plan = next(entry for entry in scan.planning.strategies if entry.target == "f")

    assert [item.parameter for item in plan.parameters] == ["x"]


def test_strategy_output_is_deterministic(tmp_path: Path) -> None:
    (tmp_path / "service.py").write_text(SIGNATURE_SOURCE, encoding="utf-8")

    first = analyze_repository(tmp_path).planning.strategies
    second = analyze_repository(tmp_path).planning.strategies

    assert first == second


def test_strategies_exist_only_for_targets_with_actions(tmp_path: Path) -> None:
    """An annotated ``int`` has generic actions, so use an unrecognized type."""
    (tmp_path / "service.py").write_text(
        "def quiet(value: Widget):\n    return value\n", encoding="utf-8"
    )
    scan = analyze_repository(tmp_path)

    assert scan.planning.strategies == ()
