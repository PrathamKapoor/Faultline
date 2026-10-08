"""Tests for production oracle mining (assertions and validation guards)."""

from __future__ import annotations

from pathlib import Path

from edgehunter.analysis.repository import analyze_discovered_files
from edgehunter.oracle import (
    ExpectedOutcome,
    OracleStrength,
    ProductionPropertyCandidate,
    PropertyKind,
    extract_production_properties,
)
from edgehunter.scanner.repository import scan_repository


def mine(
    tmp_path: Path, source: str, name: str = "module.py"
) -> tuple[ProductionPropertyCandidate, ...]:
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    analyzed = analyze_discovered_files(scan_repository(tmp_path))
    return extract_production_properties(analyzed[0].source, analyzed[0].semantics)


def by_kind(
    items: tuple[ProductionPropertyCandidate, ...], kind: PropertyKind
) -> ProductionPropertyCandidate:
    for item in items:
        if item.kind is kind:
            return item
    raise AssertionError(f"no {kind} property mined: {[item.kind for item in items]}")


def test_assertion_is_mined_as_strong_evidence(tmp_path: Path) -> None:
    properties = mine(tmp_path, "def f(x: int):\n    assert x >= 0\n    return x\n")

    assertion = by_kind(properties, PropertyKind.ASSERTED_CONDITION)
    assert assertion.target == "f"
    assert assertion.condition == "x >= 0"
    assert assertion.strength is OracleStrength.STRONG
    assert assertion.expected_outcome is ExpectedOutcome.CONDITION_HOLDS
    assert assertion.evidence[0].lineno == 2
    assert assertion.evidence[0].relative_path.as_posix() == "module.py"
    assert assertion.evidence[0].detail == "assert x >= 0"


def test_literal_assertion_message_is_stored(tmp_path: Path) -> None:
    properties = mine(tmp_path, 'def f(x):\n    assert x, "x is required"\n')

    assert by_kind(properties, PropertyKind.ASSERTED_CONDITION).message == "x is required"


def test_complex_assertion_message_is_stored_as_syntax(tmp_path: Path) -> None:
    properties = mine(tmp_path, 'def f(x):\n    assert x, "x is " + "required"\n')

    assert by_kind(properties, PropertyKind.ASSERTED_CONDITION).message == "'x is ' + 'required'"


def test_validation_guard_is_mined_as_rejection_rule(tmp_path: Path) -> None:
    source = 'def f(x):\n    if x < 0:\n        raise ValueError("negative")\n    return x\n'

    rule = by_kind(mine(tmp_path, source), PropertyKind.REJECTION_RULE)

    assert rule.target == "f"
    assert rule.condition == "x < 0"
    assert rule.expected_outcome is ExpectedOutcome.RAISES
    assert rule.exception == "ValueError"
    assert rule.strength is OracleStrength.STRONG
    assert rule.evidence[0].lineno == 2


def test_attribute_exception_is_recorded_syntactically(tmp_path: Path) -> None:
    source = "def f(x):\n    if x is None:\n        raise errors.InvalidInput()\n"

    rule = by_kind(mine(tmp_path, source), PropertyKind.REJECTION_RULE)

    assert rule.condition == "x is None"
    assert rule.exception == "errors.InvalidInput"


def test_guard_with_extra_statement_is_not_promoted(tmp_path: Path) -> None:
    source = "def f(x):\n    if x < 0:\n        log_problem()\n        raise ValueError\n"

    assert mine(tmp_path, source) == ()


def test_return_guard_is_not_a_rejection_rule(tmp_path: Path) -> None:
    properties = mine(tmp_path, "def f(x):\n    if x < 0:\n        return None\n")

    assert [item.kind for item in properties] == []


def test_call_factory_exception_keeps_syntax_without_claiming_a_type(tmp_path: Path) -> None:
    source = "def f(x):\n    if x < 0:\n        raise make_exception()\n"

    rule = by_kind(mine(tmp_path, source), PropertyKind.REJECTION_RULE)

    assert rule.exception is None
    assert rule.evidence[0].detail == "raises make_exception()"


def test_nested_ownership_is_lexical(tmp_path: Path) -> None:
    source = (
        "def outer(x):\n"
        "    assert x > 0\n"
        "\n"
        "    def inner(y):\n"
        "        if y < 0:\n"
        "            raise ValueError\n"
    )

    properties = mine(tmp_path, source)
    assertion = by_kind(properties, PropertyKind.ASSERTED_CONDITION)
    rule = by_kind(properties, PropertyKind.REJECTION_RULE)

    assert assertion.target == "outer"
    assert assertion.condition == "x > 0"
    assert rule.target == "outer.inner"
    assert rule.condition == "y < 0"
    assert rule.exception == "ValueError"


def test_method_owns_its_validation_rule(tmp_path: Path) -> None:
    source = (
        "class Account:\n"
        "    def withdraw(self, amount):\n"
        "        if amount < 0:\n"
        "            raise ValueError\n"
    )

    rule = by_kind(mine(tmp_path, source), PropertyKind.REJECTION_RULE)

    assert rule.target == "Account.withdraw"


def test_duplicate_evidence_is_deduplicated_with_all_lines(tmp_path: Path) -> None:
    source = "def f(x):\n    assert x >= 0\n    assert x >= 0\n"

    properties = mine(tmp_path, source)

    assert len(properties) == 1
    assert properties[0].evidence[0].lineno == 2
    assert properties[0].evidence[1].lineno == 3


def test_module_level_assertion_is_not_mined(tmp_path: Path) -> None:
    assert mine(tmp_path, "assert 1 == 1\n") == ()


def test_module_with_syntax_issue_yields_no_properties(tmp_path: Path) -> None:
    path = tmp_path / "broken.py"
    path.write_text("def broken(\n", encoding="utf-8")
    analyzed = analyze_discovered_files(scan_repository(tmp_path))

    assert extract_production_properties(analyzed[0].source, analyzed[0].semantics) == ()


def test_empty_repository_produces_no_properties(tmp_path: Path) -> None:
    assert mine(tmp_path, "") == ()
