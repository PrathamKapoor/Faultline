"""Tests for ``str`` semantic actions, their values, and the display rules.

Phase 7A added five deterministic string edge actions for a recognized ``str``
parameter, which previously produced no action, no candidate case, and no
invocation plan at all. These tests pin the exact values, prove they round-trip
through the execution codec, and pin the two things that must *not* move: only
one adversarial action per plan, and no collapse of distinct typed values into
one another.
"""

from __future__ import annotations

from pathlib import Path

from edgehunter.cli import _display_text
from edgehunter.execution.codec import TypedValue, ValueKind, encode_value, recognize_literal
from edgehunter.generation import (
    ANNOTATION_ACTIONS,
    ActionOrigin,
    AssignmentOrigin,
    InvocationStatus,
    LiteralKind,
    MaterializationStatus,
    SemanticAction,
    SemanticActionKind,
    StrategyKind,
    render_string_literal,
)
from edgehunter.generation.baselines import baseline_for
from edgehunter.generation.materialize import (
    LONG_STRING_LENGTH,
    NULL_CHARACTER,
    UNICODE_STRING_SAMPLE,
)
from edgehunter.pipeline import RepositoryScan, analyze_repository
from edgehunter.scanner import AnnotationKind
from edgehunter.scanner.annotations import StaticAnnotation

STR_SOURCE = """def length(value: str) -> int:
    return len(value)


def maybe_name(value: str | None):
    if value is None:
        return "unset"
    return value


def opaque(user: User, value):
    return user, value
"""

STRING_ACTIONS = (
    SemanticActionKind.EMPTY_STRING,
    SemanticActionKind.WHITESPACE_STRING,
    SemanticActionKind.UNICODE_STRING,
    SemanticActionKind.NULL_CHARACTER_STRING,
    SemanticActionKind.LONG_STRING,
)


def _scan(tmp_path: Path, source: str = STR_SOURCE) -> RepositoryScan:
    (tmp_path / "service.py").write_text(source, encoding="utf-8")
    return analyze_repository(tmp_path)


def _actions(scan: RepositoryScan, target: str) -> tuple[SemanticAction, ...]:
    entry = next(e for e in scan.generation.action_sets if e.target == target)
    return entry.actions


def _value(action: SemanticAction) -> str:
    """The actual Python value behind a stored literal, via the codec only."""
    assert action.value_text is not None
    decoded = recognize_literal(action.value_text)
    assert decoded.kind is ValueKind.STRING
    value = decoded.to_python()
    assert isinstance(value, str)
    return value


# --- 1. the annotation is recognized ----------------------------------------


def test_str_annotation_is_recognized(tmp_path: Path) -> None:
    scan = _scan(tmp_path)

    shapes = {
        item.parameter: item.description
        for item in scan.annotations.parameters
        if item.target == "length"
    }
    assert shapes == {"value": "STR"}


# --- 2. all five actions are generated --------------------------------------


def test_all_five_string_actions_are_generated(tmp_path: Path) -> None:
    scan = _scan(tmp_path)

    assert tuple(a.kind for a in _actions(scan, "length")) == STRING_ACTIONS


# --- 3/4/5/6/7. the exact documented values ---------------------------------


def test_empty_string_materializes_exactly(tmp_path: Path) -> None:
    scan = _scan(tmp_path)

    action = next(a for a in _actions(scan, "length") if a.kind is SemanticActionKind.EMPTY_STRING)
    assert action.value_text == '""'
    assert action.literal_kind is LiteralKind.TEXT
    assert _value(action) == ""


def test_whitespace_string_materializes_exactly(tmp_path: Path) -> None:
    scan = _scan(tmp_path)

    action = next(
        a for a in _actions(scan, "length") if a.kind is SemanticActionKind.WHITESPACE_STRING
    )
    assert action.value_text == '" "'
    assert _value(action) == " "


def test_unicode_string_matches_the_documented_constant(tmp_path: Path) -> None:
    scan = _scan(tmp_path)

    action = next(
        a for a in _actions(scan, "length") if a.kind is SemanticActionKind.UNICODE_STRING
    )
    assert action.value_text == f'"{UNICODE_STRING_SAMPLE}"'
    assert _value(action) == UNICODE_STRING_SAMPLE == "é漢🙂"
    # The documented sample is a mix of widths and an astral character.
    assert len(_value(action)) == 3


def test_nul_string_contains_exactly_one_nul_character(tmp_path: Path) -> None:
    scan = _scan(tmp_path)

    action = next(
        a for a in _actions(scan, "length") if a.kind is SemanticActionKind.NULL_CHARACTER_STRING
    )
    value = _value(action)
    assert value == NULL_CHARACTER == "\x00"
    assert len(value) == 1
    assert value.count("\x00") == 1
    # The stored text carries the escape, never a raw NUL byte.
    assert action.value_text == '"\\x00"'
    assert "\x00" not in action.value_text


def test_long_string_has_exactly_the_documented_length(tmp_path: Path) -> None:
    scan = _scan(tmp_path)

    action = next(a for a in _actions(scan, "length") if a.kind is SemanticActionKind.LONG_STRING)
    value = _value(action)
    assert len(value) == LONG_STRING_LENGTH == 1024
    assert value == "a" * 1024
    # The value is stored in full, never truncated.
    assert action.value_text is not None
    assert _value(action) == value


def test_the_long_string_length_is_not_controllable(tmp_path: Path) -> None:
    """A target cannot influence the bound; only the module constant can."""
    scan = _scan(
        tmp_path,
        "def length(value: str, size: int = 999999) -> int:\n    return len(value) * size\n",
    )

    action = next(a for a in _actions(scan, "length") if a.kind is SemanticActionKind.LONG_STRING)
    assert len(_value(action)) == 1024


# --- 8. values are never evaluated as code ----------------------------------


def test_string_values_are_never_evaluated_as_code(tmp_path: Path) -> None:
    """A stored string is source text, and it is only ever *recognized*.

    The codec's AST whitelist accepts it as a literal. It is never ``eval``ed on
    the host, and a target module that would explode on import is not imported.
    """
    marker = tmp_path / "MUST_NOT_EXIST.txt"
    scan = _scan(
        tmp_path,
        "from pathlib import Path\n"
        "def boom() -> None:\n"
        f"    Path({str(marker)!r}).write_text('bad')\n"
        "Path("
        f"{str(marker)!r}"
        ").write_text('bad')\n"
        "def length(value: str) -> int:\n"
        "    return len(value)\n",
    )

    assert not marker.exists()
    for action in _actions(scan, "length"):
        assert action.value_text is not None
        # Recognized as a literal string and nothing more: no call, no name.
        assert recognize_literal(action.value_text).kind is ValueKind.STRING
        assert action.literal_kind is LiteralKind.TEXT


# --- 9. CLI display escapes NUL ---------------------------------------------


def test_cli_output_escapes_the_nul_character(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from edgehunter.cli import app

    (tmp_path / "service.py").write_text(STR_SOURCE, encoding="utf-8")
    result = CliRunner().invoke(app, ["scan", str(tmp_path), "--actions"])

    assert result.exit_code == 0
    # The escaped form is what the terminal shows ...
    assert '"\\x00"' in result.output
    # ... and no raw NUL byte is ever written to it.
    assert "\x00" not in result.output


def test_cli_output_compacts_the_long_string(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from edgehunter.cli import app

    (tmp_path / "service.py").write_text(STR_SOURCE, encoding="utf-8")
    result = CliRunner().invoke(app, ["scan", str(tmp_path), "--actions"])

    assert result.exit_code == 0
    assert "a" * 1024 not in result.output
    assert "... (1026 chars)" in result.output


def test_display_text_is_ascii_safe_and_deterministic() -> None:
    assert _display_text('"\\x00"') == '"\\x00"'
    assert _display_text(UNICODE_STRING_SAMPLE) == "\\xe9\\u6f22\\U0001f642"
    assert _display_text("a" * 1024) == "a" * 80 + "... (1024 chars)"
    # Every rendered form is pure ASCII, so no console encoding can fail on it.
    for text in ('"\\x00"', UNICODE_STRING_SAMPLE, "a" * 1024, 'float("nan")'):
        assert _display_text(text).isascii()


def test_display_text_leaves_ordinary_values_untouched() -> None:
    """The display layer must not mangle values that are already safe."""
    for text in ('float("nan")', '""', '" "', "-0.0", "[0, 0]", "set()", "None", "False"):
        assert _display_text(text) == text


# --- 10. the codec round-trips Unicode and NUL ------------------------------


def test_typed_codec_round_trips_unicode_and_nul(tmp_path: Path) -> None:
    scan = _scan(tmp_path)

    for kind, expected in (
        (SemanticActionKind.UNICODE_STRING, UNICODE_STRING_SAMPLE),
        (SemanticActionKind.NULL_CHARACTER_STRING, NULL_CHARACTER),
        (SemanticActionKind.LONG_STRING, "a" * LONG_STRING_LENGTH),
    ):
        action = next(a for a in _actions(scan, "length") if a.kind is kind)
        assert action.value_text is not None
        decoded = recognize_literal(action.value_text)
        assert decoded.kind is ValueKind.STRING
        assert decoded.to_python() == expected
        assert decoded.to_python() == expected


# --- 11. candidate cases ----------------------------------------------------


def test_string_actions_produce_candidate_cases(tmp_path: Path) -> None:
    scan = _scan(tmp_path)

    cases = [c for c in scan.generation.cases if c.target == "length"]
    assert tuple(c.action.kind for c in cases) == STRING_ACTIONS
    assert all(c.completeness.value == "complete" for c in cases)
    assert all(c.missing_parameters == () for c in cases)


# --- 12. complete invocation plans ------------------------------------------


def test_one_argument_string_function_produces_complete_plans(tmp_path: Path) -> None:
    scan = _scan(tmp_path)

    plans = [p for p in scan.planning.plans if p.target == "length"]
    assert len(plans) == 5
    for plan in plans:
        assert plan.status is InvocationStatus.COMPLETE_FUNCTION
        assert plan.requires_receiver is False
        assert plan.missing_parameters == ()
        assert len(plan.assignments) == 1
        assert plan.assignments[0].origin is AssignmentOrigin.ADVERSARIAL_ACTION


# --- 13. deterministic strategy output --------------------------------------


def test_string_strategy_plan_is_the_documented_edge_set(tmp_path: Path) -> None:
    scan = _scan(tmp_path)

    plan = next(e for e in scan.planning.strategies if e.target == "length")
    strategy = next(p for p in plan.parameters if p.parameter == "value")

    assert strategy.kind is StrategyKind.SAMPLED_FROM
    assert [v.descriptor for v in strategy.values] == [
        "empty",
        "space",
        "unicode",
        "nul",
        "long",
    ]
    assert [v.text for v in strategy.values] == [
        '""',
        '" "',
        f'"{UNICODE_STRING_SAMPLE}"',
        '"\\x00"',
        f'"{"a" * LONG_STRING_LENGTH}"',
    ]


def test_string_strategy_output_is_byte_identical_across_scans(tmp_path: Path) -> None:
    first = _scan(tmp_path)
    second = _scan(tmp_path)

    def render(scan: RepositoryScan) -> str:
        return "\n".join(
            f"{e.target}.{p.parameter}:{v.descriptor}={v.text}"
            for e in scan.planning.strategies
            for p in e.parameters
            for v in p.values
        )

    assert render(first) == render(second)


def test_the_strategy_and_action_layers_agree_on_the_text(tmp_path: Path) -> None:
    """One spelling per value, so the by-text dedup cannot list it twice."""
    scan = _scan(tmp_path)

    plan = next(e for e in scan.planning.strategies if e.target == "length")
    strategy = next(p for p in plan.parameters if p.parameter == "value")
    texts = [v.text for v in strategy.values]

    assert len(texts) == len(set(texts))
    assert {a.value_text for a in _actions(scan, "length")} == set(texts)


# --- 14. nullable str keeps SET_NONE ----------------------------------------


def test_nullable_str_keeps_the_existing_set_none_behaviour(tmp_path: Path) -> None:
    scan = _scan(tmp_path)

    kinds = tuple(a.kind for a in _actions(scan, "maybe_name"))
    assert kinds == (SemanticActionKind.SET_NONE, *STRING_ACTIONS)
    none_action = next(
        a for a in _actions(scan, "maybe_name") if a.kind is SemanticActionKind.SET_NONE
    )
    assert none_action.value_text == "None"
    assert none_action.literal_kind is LiteralKind.NOTHING


# --- 15. unknown annotations stay conservative ------------------------------


def test_unknown_annotations_gain_no_string_action(tmp_path: Path) -> None:
    scan = _scan(tmp_path)

    for target in ("opaque",):
        assert not [e for e in scan.generation.action_sets if e.target == target]
        assert not [c for c in scan.generation.cases if c.target == target]
        assert not [p for p in scan.planning.plans if p.target == target]


def test_a_parameter_named_like_a_string_is_not_treated_as_one(tmp_path: Path) -> None:
    """A name is not type evidence (spec: names such as ``name``/``text``/``path``)."""
    scan = _scan(
        tmp_path, "def f(name, text, message, path):\n    return name, text, message, path\n"
    )

    assert scan.generation.action_count == 0
    assert scan.planning.plan_count == 0


# --- provenance -------------------------------------------------------------


def test_every_string_action_carries_annotation_provenance(tmp_path: Path) -> None:
    scan = _scan(tmp_path)

    for action in _actions(scan, "length"):
        assert action.origin is ActionOrigin.ANNOTATION
        assert action.boundary_kind is None
        assert action.reference is None
        assert action.evidence == ()
        assert action.reason == "parameter annotated STR"
        assert action.annotation.kind is AnnotationKind.STR
        assert action.materialization_reason


def test_string_actions_are_mapped_in_the_annotation_action_table() -> None:
    assert ANNOTATION_ACTIONS[AnnotationKind.STR] == STRING_ACTIONS


# --- spec section 28: exactly one adversarial action ------------------------


def test_only_one_adversarial_action_per_plan_for_bool_and_string(tmp_path: Path) -> None:
    scan = _scan(tmp_path, "def f(flag: bool, name: str):\n    return flag, name\n")

    for plan in scan.planning.plans:
        adversarial = [
            a for a in plan.assignments if a.origin is AssignmentOrigin.ADVERSARIAL_ACTION
        ]
        assert len(adversarial) == 1
        assert adversarial[0].parameter == plan.primary_action.parameter


def test_set_true_on_flag_uses_the_empty_string_baseline(tmp_path: Path) -> None:
    scan = _scan(tmp_path, "def f(flag: bool, name: str):\n    return flag, name\n")

    plan = next(
        p for p in scan.planning.plans if p.primary_action.kind is SemanticActionKind.SET_TRUE
    )
    assert [(a.parameter, a.value_text, a.origin) for a in plan.assignments] == [
        ("flag", "True", AssignmentOrigin.ADVERSARIAL_ACTION),
        ("name", '""', AssignmentOrigin.STATIC_TYPE_BASELINE),
    ]
    assert plan.status is InvocationStatus.COMPLETE_FUNCTION


def test_nul_string_on_name_uses_the_false_baseline(tmp_path: Path) -> None:
    scan = _scan(tmp_path, "def f(flag: bool, name: str):\n    return flag, name\n")

    plan = next(
        p
        for p in scan.planning.plans
        if p.primary_action.kind is SemanticActionKind.NULL_CHARACTER_STRING
    )
    assert [(a.parameter, a.value_text, a.origin) for a in plan.assignments] == [
        ("flag", "False", AssignmentOrigin.STATIC_TYPE_BASELINE),
        ("name", '"\\x00"', AssignmentOrigin.ADVERSARIAL_ACTION),
    ]
    assert plan.status is InvocationStatus.COMPLETE_FUNCTION


def test_there_is_no_cartesian_product_of_actions(tmp_path: Path) -> None:
    """7 actions (2 bool + 5 string) means 7 cases, never 10 or more."""
    scan = _scan(tmp_path, "def f(flag: bool, name: str):\n    return flag, name\n")

    assert scan.generation.action_count == 7
    assert scan.generation.case_count == 7
    assert scan.planning.plan_count == 7
    assert len({(c.parameter, c.action.kind) for c in scan.generation.cases}) == 7


# --- spec section 29: no type confusion -------------------------------------


def test_distinct_string_values_stay_distinct(tmp_path: Path) -> None:
    scan = _scan(tmp_path)
    values = {a.kind: _value(a) for a in _actions(scan, "length")}

    assert values[SemanticActionKind.EMPTY_STRING] == ""
    assert values[SemanticActionKind.WHITESPACE_STRING] == " "
    assert values[SemanticActionKind.NULL_CHARACTER_STRING] == "\x00"

    # `""`, `" "`, and `"\x00"` are pairwise distinct even though two of them are
    # invisible, and none of them is the empty string.
    distinct = {
        values[SemanticActionKind.EMPTY_STRING],
        values[SemanticActionKind.WHITESPACE_STRING],
        values[SemanticActionKind.NULL_CHARACTER_STRING],
    }
    assert len(distinct) == 3
    assert "" in distinct


def test_none_is_not_a_string_value(tmp_path: Path) -> None:
    scan = _scan(tmp_path)
    none_action = next(
        a for a in _actions(scan, "maybe_name") if a.kind is SemanticActionKind.SET_NONE
    )
    string_actions = [a for a in _actions(scan, "maybe_name") if a.kind in STRING_ACTIONS]

    assert none_action.value_text == "None"
    assert none_action.literal_kind is LiteralKind.NOTHING
    assert all(a.literal_kind is LiteralKind.TEXT for a in string_actions)
    assert all(a.value_text != "None" for a in string_actions)


def test_false_and_zero_stay_different_typed_values() -> None:
    """``False == 0`` in Python, but the protocol keeps them apart."""
    false = recognize_literal("False")
    zero = recognize_literal("0")

    assert false.kind is ValueKind.BOOLEAN
    assert zero.kind is ValueKind.INTEGER
    assert false.to_python() is False
    assert zero.to_python() == 0
    assert isinstance(false.to_python(), bool)
    assert not isinstance(zero.to_python(), bool)
    # Python says they are equal; the protocol does not collapse them.
    assert false.to_python() == zero.to_python()
    assert _wire(false) != _wire(zero)


def _wire(value: TypedValue) -> str:
    return str(encode_value(value))


def test_render_string_literal_is_canonical_and_shared() -> None:
    assert render_string_literal("") == '""'
    assert render_string_literal(" ") == '" "'
    assert render_string_literal("\x00") == '"\\x00"'
    assert render_string_literal(UNICODE_STRING_SAMPLE) == f'"{UNICODE_STRING_SAMPLE}"'
    assert render_string_literal('a"b\\c') == '"a\\"b\\\\c"'
    # The stored `str` baseline must use the very same spelling, otherwise the
    # same input would be written two ways in one report.
    baseline = baseline_for(StaticAnnotation(kind=AnnotationKind.STR))
    assert baseline is not None
    assert baseline.value_text == render_string_literal("")


def test_every_string_action_is_concrete(tmp_path: Path) -> None:
    scan = _scan(tmp_path)

    for action in _actions(scan, "length"):
        assert action.status is MaterializationStatus.CONCRETE
        assert action.value_text is not None
        assert action.symbolic_text is None
