"""Tests for explainable structural risk scoring."""

from __future__ import annotations

from pathlib import Path

from edgehunter.analysis.models import RepositoryRiskProfile, RiskFeatureKind, TargetRiskProfile
from edgehunter.analysis.repository import analyze_discovered_files
from edgehunter.analysis.risk import (
    MAX_SCORE,
    count_boundary_groups,
    count_input_parameters,
    rank_profiles,
    score_module,
    score_target,
)
from edgehunter.scanner.repository import scan_repository


def profiles_for(tmp_path: Path) -> RepositoryRiskProfile:
    """Rank risk profiles for every Python file currently in ``tmp_path``."""
    analysis = analyze_discovered_files(scan_repository(tmp_path))
    scored = [profile for item in analysis for profile in score_module(item.module, item.semantics)]
    return rank_profiles(tuple(scored))


def write(tmp_path: Path, source: str, name: str = "module.py") -> None:
    (tmp_path / name).write_text(source, encoding="utf-8")


def only_profile(tmp_path: Path, source: str, name: str = "module.py") -> TargetRiskProfile:
    write(tmp_path, source, name)
    ranked = profiles_for(tmp_path)
    assert len(ranked.profiles) == 1
    return ranked.profiles[0]


def points(profile: TargetRiskProfile, kind: RiskFeatureKind) -> int:
    for contribution in profile.contributions:
        if contribution.kind is kind:
            return contribution.points
    raise AssertionError(f"contribution not found: {kind}")


def detail(profile: TargetRiskProfile, kind: RiskFeatureKind) -> str:
    for contribution in profile.contributions:
        if contribution.kind is kind:
            return contribution.detail
    raise AssertionError(f"contribution not found: {kind}")


def test_identity_target_scores_zero_features(tmp_path: Path) -> None:
    profile = only_profile(tmp_path, "def identity(x):\n    return x\n")

    # 1 input parameter (+3) plus a 2-line span, one block (+3).
    assert profile.score == 6
    assert points(profile, RiskFeatureKind.PREDICATES) == 0
    assert points(profile, RiskFeatureKind.ARITHMETIC) == 0
    assert points(profile, RiskFeatureKind.BOUNDARY_GROUPS) == 0
    assert points(profile, RiskFeatureKind.INPUT_PARAMETERS) == 3
    assert points(profile, RiskFeatureKind.SOURCE_SPAN) == 3


def test_complex_target_scores_higher_than_simple_target(tmp_path: Path) -> None:
    simple = only_profile(tmp_path, "def simple(x):\n    return x\n")
    complex_target = only_profile(
        tmp_path,
        "def complex(a: int, b: int):\n"
        "    if a > 0:\n"
        "        if b <= a:\n"
        "            return a - b\n"
        "    return a + b\n",
    )

    assert complex_target.score > simple.score
    assert points(complex_target, RiskFeatureKind.PREDICATES) == 12
    assert points(complex_target, RiskFeatureKind.ARITHMETIC) == 8
    assert points(complex_target, RiskFeatureKind.INPUT_PARAMETERS) == 6


def test_method_receiver_is_not_counted_as_input(tmp_path: Path) -> None:
    profile = only_profile(
        tmp_path, "class Example:\n    def method(self, value, limit):\n        ...\n"
    )

    assert points(profile, RiskFeatureKind.INPUT_PARAMETERS) == 6
    assert "2 input parameter(s)" in detail(profile, RiskFeatureKind.INPUT_PARAMETERS)


def test_classmethod_receiver_is_not_counted_as_input(tmp_path: Path) -> None:
    profile = only_profile(
        tmp_path,
        "class Example:\n    @classmethod\n    def make(cls, value, limit):\n        ...\n",
    )

    assert points(profile, RiskFeatureKind.INPUT_PARAMETERS) == 6


def test_free_function_named_self_is_counted(tmp_path: Path) -> None:
    profile = only_profile(tmp_path, "def helper(self, value):\n    ...\n")

    assert points(profile, RiskFeatureKind.INPUT_PARAMETERS) == 6


def test_boundary_groups_collapse_kinds_and_generic_values(tmp_path: Path) -> None:
    source = (
        "def f(x: float, limit: int):\n"
        "    if x < 10:\n"
        "        ...\n"
        "    if x <= limit:\n"
        "        ...\n"
    )
    profile = only_profile(tmp_path, source)

    # groups: (x, '10'), (x, 'limit'), (x, None), (limit, None)
    assert "4 boundary group(s)" in detail(profile, RiskFeatureKind.BOUNDARY_GROUPS)
    assert points(profile, RiskFeatureKind.BOUNDARY_GROUPS) == 16


def test_source_span_uses_structural_metadata(tmp_path: Path) -> None:
    short = only_profile(tmp_path, "def small():\n    ...\n")
    body = "".join(f"    x{index} = {index}\n" for index in range(24))
    large = only_profile(tmp_path, "def large():\n" + body + "    ...\n")

    # small spans 2 lines (1 block); large spans 26 lines (2 blocks).
    assert points(short, RiskFeatureKind.SOURCE_SPAN) == 3
    assert "26 line(s) span, 2 block(s)" in detail(large, RiskFeatureKind.SOURCE_SPAN)
    assert points(large, RiskFeatureKind.SOURCE_SPAN) == 6


def test_feature_caps_are_enforced(tmp_path: Path) -> None:
    body = "".join(
        f"    if x > {index}:\n        total += x * {index + 1}\n" for index in range(1, 12)
    )
    source = f"def huge(x, a, b, c, d, e, f):\n    total = 0\n{body}    return total\n"

    profile = only_profile(tmp_path, source)

    # 11 predicates and 11 arithmetic expressions saturate their caps; the eleven
    # distinct literal references give eleven boundary groups, also capped.
    assert points(profile, RiskFeatureKind.PREDICATES) == 30
    assert points(profile, RiskFeatureKind.ARITHMETIC) == 20
    assert points(profile, RiskFeatureKind.BOUNDARY_GROUPS) == 20
    assert points(profile, RiskFeatureKind.INPUT_PARAMETERS) == 15
    assert points(profile, RiskFeatureKind.SOURCE_SPAN) == 6
    assert profile.score <= MAX_SCORE
    assert profile.score == 91


def test_score_is_capped_at_one_hundred(tmp_path: Path) -> None:
    lines = ["def saturated(x: int, a: int, b: int, c: int, d: int, e: int):", "    total = 0"]
    lines.extend(f"    if x > {index}: total += x * {index}" for index in range(1, 12))
    lines.extend(f"    x{index} = {index}" for index in range(40))
    lines.extend(f"    if x > {name}: total += 1" for name in "abcdefg")
    lines.extend(f"    y{index} = {index}" for index in range(60))
    lines.append("    return total")

    profile = only_profile(tmp_path, "\n".join(lines) + "\n")

    assert points(profile, RiskFeatureKind.PREDICATES) == 30
    assert points(profile, RiskFeatureKind.ARITHMETIC) == 20
    assert points(profile, RiskFeatureKind.BOUNDARY_GROUPS) == 20
    assert points(profile, RiskFeatureKind.INPUT_PARAMETERS) == 15
    assert points(profile, RiskFeatureKind.SOURCE_SPAN) == 15
    assert profile.score == MAX_SCORE
    assert profile.score == 100


def test_ranking_is_deterministic_with_equal_scores(tmp_path: Path) -> None:
    write(tmp_path, "def second(x):\n    ...\n", "z.py")
    write(tmp_path, "def first(x):\n    ...\n", "a.py")

    first_run = profiles_for(tmp_path)
    second_run = profiles_for(tmp_path)

    assert first_run == second_run
    assert [profile.target for profile in first_run.profiles] == ["first", "second"]


def test_ranking_orders_by_score_first(tmp_path: Path) -> None:
    write(tmp_path, "def risky(x: int, y: int):\n    if x > 0:\n        return x * y\n", "b.py")
    write(tmp_path, "def plain(x):\n    ...\n", "a.py")

    ranked = profiles_for(tmp_path)

    assert [profile.target for profile in ranked.profiles] == ["risky", "plain"]


def test_helpers_agree_with_module_scoring(tmp_path: Path) -> None:
    write(
        tmp_path, "class C:\n    def m(self, value: int):\n        if value < 3:\n            ...\n"
    )
    analyzed = analyze_discovered_files(scan_repository(tmp_path))
    module = analyzed[0].module
    semantics = analyzed[0].semantics
    function = module.functions[0]
    boundaries = semantics.boundaries[0] if semantics.boundaries else None

    assert count_input_parameters(function) == 1
    # groups: (value, '3') from the comparison and (value, None) from the int generics
    assert count_boundary_groups(boundaries) == 2

    direct = score_target(module.relative_path, function, semantics.targets[0], boundaries)
    through_module = score_module(module, semantics)[0]
    assert direct.score == through_module.score


def test_repository_risk_statistics(tmp_path: Path) -> None:
    write(tmp_path, "def plain(x):\n    ...\n")
    ranked = profiles_for(tmp_path)

    assert ranked.scored_target_count == 1
    assert ranked.highest_score == 6
    assert ranked.targets_with_score_above_zero == 1
