"""Tests for the single-parse pipeline and cross-stage aggregation."""

from __future__ import annotations

from pathlib import Path

import pytest

import edgehunter.scanner.ast_parser as ast_parser_module
from edgehunter.generation import AssignmentOrigin, SemanticActionKind
from edgehunter.oracle import OracleSupport, PropertyKind, TargetResolution
from edgehunter.pipeline import analyze_repository
from edgehunter.scanner.ast_parser import SourceText


def write_repo(tmp_path: Path, files: dict[str, str]) -> None:
    for name, source in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")


def test_full_pipeline_reads_each_file_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole pipeline must not re-read source for oracle mining.

    Counting reads is sufficient to bound parsing too: parsing needs the source
    text, so a second parse of a file would necessarily require a second read.
    Patching ``ast.parse`` itself is not viable because it is a process-global
    module attribute that the test runner also uses.
    """
    write_repo(
        tmp_path,
        {
            "m.py": "def add(a, b):\n    return a + b\n",
            "test_m.py": "def test_add():\n    assert add(1, 2) == 3\n",
        },
    )

    reads: list[Path] = []
    original_read = ast_parser_module._read_source

    def counting_read(path: Path) -> SourceText:
        reads.append(path)
        return original_read(path)

    monkeypatch.setattr(ast_parser_module, "_read_source", counting_read)

    analysis = analyze_repository(tmp_path)

    assert len(reads) == 2
    # Phase 2 semantics remain file-agnostic: the test file's own assert is a
    # predicate, while oracle mining treats it as a test expectation instead.
    assert analysis.semantics.predicate_count == 1
    assert analysis.oracles.assertion_count == 0
    assert analysis.oracles.resolved_test_expectation_count == 1
    assert analysis.risk.scored_target_count == 2


def test_pipeline_produces_structure_semantics_oracles_risk_and_actions(
    tmp_path: Path,
) -> None:
    write_repo(
        tmp_path,
        {
            "account.py": (
                "def withdraw(balance: float, amount: float) -> float:\n"
                "    if amount < 0:\n"
                "        raise ValueError('amount must be non-negative')\n"
                "\n"
                "    assert balance >= 0\n"
                "\n"
                "    if amount <= balance:\n"
                "        return balance - amount\n"
                "\n"
                "    return balance\n"
            )
        },
    )

    scan = analyze_repository(tmp_path)

    assert scan.semantics.structure.python_file_count == 1
    assert scan.semantics.predicate_count == 3
    assert scan.semantics.boundary_candidate_count > 0
    assert scan.oracles.assertion_count == 1
    assert scan.oracles.validation_rule_count == 1
    assert scan.oracles.property_candidate_count == 2
    assert scan.risk.scored_target_count == 1
    assert scan.risk.profiles[0].target == "withdraw"
    assert scan.risk.highest_score > 0
    assert scan.generation.targets_with_actions == 1
    assert scan.generation.action_count == scan.semantics.boundary_candidate_count
    assert scan.generation.case_count == scan.generation.action_count


def test_action_selection_does_not_add_a_second_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Actions are selected from the already-parsed AST, never by re-reading."""
    write_repo(
        tmp_path,
        {
            "m.py": (
                "def f(x: int, items: list):\n    if x > 0:\n        return items\n    return []\n"
            ),
        },
    )

    reads: list[Path] = []
    original_read = ast_parser_module._read_source

    def counting_read(path: Path) -> SourceText:
        reads.append(path)
        return original_read(path)

    monkeypatch.setattr(ast_parser_module, "_read_source", counting_read)

    scan = analyze_repository(tmp_path)

    assert len(reads) == 1
    assert scan.generation.action_count > 0


def test_generation_is_independent_of_risk_and_oracles(tmp_path: Path) -> None:
    """The same target yields the same actions regardless of oracle evidence."""
    write_repo(
        tmp_path,
        {
            "quiet.py": "def quiet(x: int):\n    if x > 5:\n        return x\n    return 0\n",
            "test_quiet.py": "def test_quiet():\n    assert quiet(1) == 0\n",
        },
    )

    scan = analyze_repository(tmp_path)

    assert scan.oracles.resolved_test_expectation_count == 1
    assert scan.generation.targets_with_actions == 1
    kinds = {action.kind for action in scan.generation.action_sets[0].actions}
    assert SemanticActionKind.JUST_BELOW_BOUNDARY in kinds
    assert scan.generation.symbolic_action_count == 0


def test_syntax_issue_yields_no_actions_for_that_file(tmp_path: Path) -> None:
    write_repo(tmp_path, {"broken.py": "def broken(\n"})

    scan = analyze_repository(tmp_path)

    assert scan.semantics.structure.syntax_issue_count == 1
    assert scan.generation.action_count == 0
    assert scan.generation.case_count == 0


def test_support_is_strong_for_explicit_evidence(tmp_path: Path) -> None:
    write_repo(
        tmp_path,
        {
            "m.py": "def f(x):\n    assert x >= 0\n",
            "test_m.py": "def test_f():\n    assert f(1) == 1\n",
        },
    )

    scan = analyze_repository(tmp_path)

    assert len(scan.oracles.support) == 1
    support = scan.oracles.support[0]
    assert support.target == "f"
    assert support.level is OracleSupport.STRONG
    assert support.assertion_count == 1
    assert support.validation_rule_count == 0
    assert support.resolved_test_expectation_count == 1
    assert scan.oracles.strongly_supported_target_count == 1


def test_target_without_evidence_has_no_support_entry(tmp_path: Path) -> None:
    write_repo(tmp_path, {"m.py": "def plain(x):\n    return x\n"})

    scan = analyze_repository(tmp_path)

    assert scan.oracles.support == ()
    assert scan.oracles.strongly_supported_target_count == 0
    assert scan.oracles.property_candidate_count == 0


def test_risk_and_oracle_support_are_independent(tmp_path: Path) -> None:
    write_repo(
        tmp_path,
        {
            "m.py": (
                "def quiet(x):\n"
                "    assert x >= 0\n"
                "    return x\n"
                "\n"
                "def busy(a: int, b: int, c: int):\n"
                "    total = 0\n"
                "    if a > 0:\n"
                "        if b > 1:\n"
                "            if c > 2:\n"
                "                total = a * b\n"
                "    return total\n"
            )
        },
    )

    scan = analyze_repository(tmp_path)
    profiles = {profile.target: profile for profile in scan.risk.profiles}
    supported = {entry.target for entry in scan.oracles.support}

    # The asserted target scores lower than the unasserted busy target.
    assert profiles["quiet"].score < profiles["busy"].score
    assert "quiet" in supported
    assert "busy" not in supported


def test_ambiguous_resolution_does_not_create_source_failure(tmp_path: Path) -> None:
    write_repo(
        tmp_path,
        {
            "a.py": "def parse(value):\n    ...\n",
            "b.py": "def parse(value):\n    ...\n",
            "test_parse.py": "def test_parse():\n    assert parse('x') is None\n",
        },
    )

    scan = analyze_repository(tmp_path)

    assert scan.semantics.structure.issue_count == 0
    assert scan.oracles.ambiguous_test_expectation_count == 1
    assert scan.oracles.resolved_test_expectation_count == 0
    assert scan.oracles.unresolved_test_expectation_count == 0
    expectation = scan.oracles.test_expectations[0]
    assert expectation.resolution is TargetResolution.AMBIGUOUS
    assert expectation.kind is PropertyKind.EXAMPLE_EXPECTATION


def test_syntax_issue_keeps_other_files_analyzed(tmp_path: Path) -> None:
    write_repo(
        tmp_path,
        {"broken.py": "def broken(\n", "m.py": "def f(x):\n    assert x >= 0\n"},
    )

    scan = analyze_repository(tmp_path)

    assert scan.semantics.structure.syntax_issue_count == 1
    assert scan.oracles.assertion_count == 1
    assert scan.risk.scored_target_count == 1


def test_annotation_and_planning_do_not_add_a_second_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Shapes, baselines, and plans all come from the single existing parse."""
    write_repo(
        tmp_path,
        {
            "m.py": (
                "def f(x: int, tags: list[int], limit: int | None = None):\n"
                "    if x <= 0:\n"
                "        return tags\n"
                "    return tags\n"
            ),
            "test_m.py": "def test_f():\n    assert f(1) == [1]\n",
        },
    )

    reads: list[Path] = []
    original_read = ast_parser_module._read_source

    def counting_read(path: Path) -> SourceText:
        reads.append(path)
        return original_read(path)

    monkeypatch.setattr(ast_parser_module, "_read_source", counting_read)

    scan = analyze_repository(tmp_path)

    assert len(reads) == 2
    assert scan.annotations.parameter_count == 3
    assert scan.annotations.recognized_count == 3
    assert scan.planning.plan_count > 0
    assert scan.planning.strategy_plan_count > 0


def test_planning_composes_over_the_candidate_layer(tmp_path: Path) -> None:
    write_repo(
        tmp_path,
        {
            "m.py": (
                "def f(x: int, name: str):\n"
                "    if x > 0:\n"
                "        return x, name\n"
                "    return None\n"
            )
        },
    )

    scan = analyze_repository(tmp_path)

    # One plan per candidate case: planning adds no cases of its own.
    assert scan.planning.plan_count == scan.generation.case_count
    assert scan.planning.complete_plan_count == scan.generation.case_count

    # Phase 7A: a recognized ``str`` parameter is reachable, so ``name`` now has
    # plans of its own. Before that change the assertion here was
    # ``all(... == "x")``, which held only because ``str`` produced no action at
    # all. The structural property the test is really about is that each plan
    # carries exactly one primary action and never a combination of them, so
    # that is what is asserted now.
    assert {plan.primary_action.parameter for plan in scan.planning.plans} == {"x", "name"}
    for plan in scan.planning.plans:
        adversarial = [
            item for item in plan.assignments if item.origin is AssignmentOrigin.ADVERSARIAL_ACTION
        ]
        assert len(adversarial) == 1
        assert adversarial[0].parameter == plan.primary_action.parameter


def test_syntax_issue_yields_no_plans(tmp_path: Path) -> None:
    write_repo(tmp_path, {"broken.py": "def broken(\n"})

    scan = analyze_repository(tmp_path)

    assert scan.semantics.structure.syntax_issue_count == 1
    assert scan.planning.plan_count == 0
    assert scan.planning.strategy_plan_count == 0
    assert scan.annotations.parameter_count == 0


def test_repeated_scans_are_equal(tmp_path: Path) -> None:
    write_repo(
        tmp_path,
        {
            "m.py": "def f(x: int):\n    if x > 0:\n        return x * 2\n",
            "test_m.py": "def test_f():\n    assert f(1) == 2\n",
        },
    )

    assert analyze_repository(tmp_path) == analyze_repository(tmp_path)


def test_single_file_target_is_supported(tmp_path: Path) -> None:
    target = tmp_path / "solo.py"
    target.write_text("def f(x):\n    assert x >= 0\n", encoding="utf-8")

    scan = analyze_repository(target)

    assert scan.semantics.structure.is_file is True
    assert scan.oracles.assertion_count == 1
    assert scan.risk.profiles[0].relative_path == Path("solo.py")
