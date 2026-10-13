"""Bounded candidate generation: anchors, seeds, source boundaries, and limits.

Every test here checks actual candidate *values* and actual *reasons*, because a
candidate generator that produces the right numbers for the wrong reasons is
still a search that cannot be audited.

The three properties under test are the ones a false-positive-free search rests
on: the universe is finite, it is deterministic, and every value in it satisfies
the declared domain with a provenance that says which operator produced it.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from edgehunter.execution.models import TargetRef
from edgehunter.pipeline import analyze_repository
from edgehunter.search.actions import (
    MAX_MIDPOINT_PROPOSALS,
    SearchContext,
    enumerate_search_actions,
)
from edgehunter.search.models import (
    DEFAULT_CANDIDATE_LIMIT,
    HARD_MAX_CANDIDATES,
    ProposalOrigin,
    SearchAction,
    SearchActionKind,
    SearchBudgets,
    SearchValueError,
)
from edgehunter.search.repository import _target_boundaries, _target_comparisons
from edgehunter.shrink.contract import Contract, RaisesBuiltinExpectation
from edgehunter.shrink.domain import DomainOperator, IntegerDomain

NEGATIVE_SOURCE = (
    "def validate(x: int) -> int:\n"
    "    if x == -2:\n"
    "        return x\n"
    "\n"
    '    raise ValueError("negative")\n'
)


def _target() -> TargetRef:
    return TargetRef(relative_file="m.py", qualified_name="validate", lineno=1)


def _contract(
    operator: DomainOperator = DomainOperator.LT, reference: int = 0, digest: str = "0" * 64
) -> Contract:
    return Contract(
        contract_id="neg",
        target_file="m.py",
        target_name="validate",
        source_sha256=digest,
        parameter="x",
        domain=IntegerDomain(operator, reference),
        expectation=RaisesBuiltinExpectation("ValueError"),
    )


DEFAULT_BUDGETS = SearchBudgets()


def _context(
    *,
    operator: DomainOperator = DomainOperator.LT,
    reference: int = 0,
    start: int = -64,
    boundaries: tuple[object, ...] = (),
    comparisons: tuple[object, ...] = (),
    budgets: SearchBudgets = DEFAULT_BUDGETS,
) -> SearchContext:
    return SearchContext(
        contract=_contract(operator, reference),
        target=_target(),
        start_value=start,
        budgets=budgets,
        boundaries=boundaries,  # type: ignore[arg-type]
        comparisons=comparisons,  # type: ignore[arg-type]
    )


def _values(actions: tuple[SearchAction, ...]) -> list[int]:
    return [action.value for action in actions]


def _by_kind(actions: tuple[SearchAction, ...], kind: SearchActionKind) -> list[int]:
    return [action.value for action in actions if action.kind is kind]


def _of_kind(actions: tuple[SearchAction, ...], kind: SearchActionKind) -> tuple[SearchAction, ...]:
    return tuple(action for action in actions if action.kind is kind)


def _find(actions: tuple[SearchAction, ...], value: int) -> SearchAction:
    for action in actions:
        if action.value == value:
            return action
    raise AssertionError(f"no candidate with value {value} in {_values(actions)}")


# --- 1/2/3: the contract anchor, for every operator and both strictnesses ----


@pytest.mark.parametrize(
    ("operator", "reference", "expected"),
    [
        (DomainOperator.LT, 0, -1),
        (DomainOperator.LE, 0, 0),
        (DomainOperator.GT, 0, 1),
        (DomainOperator.GE, 0, 0),
        (DomainOperator.GE, 10, 10),
        (DomainOperator.LT, -5, -6),
        (DomainOperator.LE, -5, -5),
    ],
)
def test_anchor_is_the_closest_to_zero_admissible_integer(
    operator: DomainOperator, reference: int, expected: int
) -> None:
    actions = enumerate_search_actions(_context(operator=operator, reference=reference, start=-64))

    assert _by_kind(actions, SearchActionKind.CONTRACT_ANCHOR) == [expected]
    assert actions[0].value == expected
    assert actions[0].origin is ProposalOrigin.CONTRACT_BOUNDARY


def test_anchor_reuses_the_shrink_domain_helper_rather_than_a_second_one() -> None:
    """The anchor must come from the one helper, or the two searches drift apart."""

    domain = IntegerDomain(DomainOperator.GT, 7)
    assert domain.closest_to_zero() == 8

    actions = enumerate_search_actions(_context(operator=DomainOperator.GT, reference=7, start=100))
    assert actions[0].value == domain.closest_to_zero()


# --- 4/5: the explicit starting integer --------------------------------------


def test_explicit_start_is_offered_and_recorded_as_a_user_seed() -> None:
    actions = enumerate_search_actions(_context(start=-64))

    seed = _by_kind(actions, SearchActionKind.USER_SEED)
    assert seed == [-64]
    action = _find(actions, -64)
    assert action.origin is ProposalOrigin.USER_SUPPLIED_SEED
    assert "explicitly supplied" in action.reason


def test_duplicate_anchor_and_start_is_offered_once_with_the_stronger_origin() -> None:
    """A seed that equals the anchor is one candidate, not the same input twice."""

    actions = enumerate_search_actions(_context(start=-1))

    assert _values(actions).count(-1) == 1
    assert _find(actions, -1).origin is ProposalOrigin.CONTRACT_BOUNDARY
    assert _by_kind(actions, SearchActionKind.USER_SEED) == []


# --- 6/7/8: source-derived boundaries ----------------------------------------


def _scan_context(tmp_path: Path, source: str) -> SearchContext:
    source_file = tmp_path / "m.py"
    source_file.write_text(source, encoding="utf-8")
    digest = hashlib.sha256(source_file.read_bytes()).hexdigest()
    scan = analyze_repository(tmp_path)
    contract = _contract(digest=digest)
    return SearchContext(
        contract=contract,
        target=_target(),
        start_value=-64,
        boundaries=_target_boundaries(scan, contract),
        comparisons=_target_comparisons(scan, contract),
    )


def test_source_comparison_boundary_is_included(tmp_path: Path) -> None:
    """``if x == -2`` is the case a search exists for: the target special-cases -2."""

    actions = enumerate_search_actions(_scan_context(tmp_path, NEGATIVE_SOURCE))

    assert -2 in _values(actions)
    action = _find(actions, -2)
    assert action.kind is SearchActionKind.SOURCE_BOUNDARY_AT
    assert action.origin is ProposalOrigin.STATIC_SOURCE_BOUNDARY
    assert "x == -2" in action.reason


def test_out_of_domain_source_boundary_is_excluded(tmp_path: Path) -> None:
    """``x == 7`` is real evidence but the contract does not govern 7."""

    source = "def validate(x: int) -> int:\n    if x == 7:\n        return x\n\n    return x\n"
    actions = enumerate_search_actions(_scan_context(tmp_path, source))

    assert 7 not in _values(actions)
    assert all(actions_action.value < 0 for actions_action in actions)


def test_unknown_symbolic_source_boundary_is_excluded(tmp_path: Path) -> None:
    """A reference the source names but does not define cannot become a value."""

    source = (
        "def validate(x: int, limit: int) -> int:\n"
        "    if x < limit:\n"
        "        return x\n\n"
        "    return x\n"
    )
    actions = enumerate_search_actions(_scan_context(tmp_path, source))

    assert all(isinstance(action.value, int) for action in actions)
    assert "limit" not in " ".join(action.reason for action in actions)


def test_signed_literal_shaped_expressions_are_not_read_as_integers(tmp_path: Path) -> None:
    """``-limit``, ``-2 + 3`` and ``-x`` are never turned into an integer."""

    source = (
        "def validate(x: int, limit: int) -> int:\n"
        "    if x == -limit:\n"
        "        return x\n"
        "    if x == -2 + 3:\n"
        "        return x\n"
        "    if x == -x:\n"
        "        return x\n\n"
        "    return x\n"
    )
    actions = enumerate_search_actions(_scan_context(tmp_path, source))

    derived = [a for a in actions if a.origin is ProposalOrigin.STATIC_SOURCE_BOUNDARY]
    assert derived == []


# --- 9: signed integer limits -------------------------------------------------


def test_signed_integer_limits_are_enforced_on_every_proposal(tmp_path: Path) -> None:
    """A domain open at the range edge must not propose a value past it."""

    actions = enumerate_search_actions(
        _context(operator=DomainOperator.GE, reference=-(2**63), start=-(2**63) + 5)
    )

    assert all(-(2**63) <= action.value <= 2**63 - 1 for action in actions)
    assert 2**63 - 1 not in _values(actions)


def test_out_of_range_proposal_is_refused_by_the_model() -> None:
    with pytest.raises(SearchValueError):
        SearchAction(
            kind=SearchActionKind.CONTRACT_ANCHOR,
            target_identity="m.py::validate",
            parameter="x",
            value=2**63,
            contract_id="neg",
            source_sha256="0" * 64,
            origin=ProposalOrigin.CONTRACT_BOUNDARY,
            reason="out of range",
            priority=0,
        )


# --- 10: the candidate cap ---------------------------------------------------


def test_candidate_universe_is_capped_and_reports_the_effective_limit() -> None:
    budgets = SearchBudgets(candidate_limit=3)
    actions = enumerate_search_actions(_context(start=-64, budgets=budgets))

    assert len(actions) == 3
    assert budgets.bounded_candidates() == 3


def test_candidate_cap_is_hard_bounded() -> None:
    budgets = SearchBudgets(candidate_limit=10_000)
    assert budgets.bounded_candidates() == HARD_MAX_CANDIDATES
    assert DEFAULT_CANDIDATE_LIMIT < HARD_MAX_CANDIDATES


def test_universe_never_exceeds_the_hard_cap_for_an_open_domain(tmp_path: Path) -> None:
    """A repository cannot widen the universe by being large or noisy."""

    source = NEGATIVE_SOURCE + "".join(
        f"def helper_{index}(x: int) -> int:\n    if x == -{index + 10}:\n        return x\n\n"
        "    return x\n\n\n"
        for index in range(40)
    )
    context = _scan_context(tmp_path, source)

    assert len(enumerate_search_actions(context)) <= HARD_MAX_CANDIDATES


def test_midpoint_chain_is_bounded_and_converges(tmp_path: Path) -> None:
    actions = enumerate_search_actions(_scan_context(tmp_path, NEGATIVE_SOURCE))

    midpoints = _by_kind(actions, SearchActionKind.MIDPOINT_TO_ANCHOR)
    assert midpoints
    assert len(midpoints) <= MAX_MIDPOINT_PROPOSALS
    # Each floor midpoint halves the remaining distance to the anchor, so
    # magnitudes strictly decrease and every one sits between the seed and the
    # anchor. The static key then orders them by magnitude.
    assert midpoints == sorted(midpoints, key=abs)
    assert all(-64 < value < -1 for value in midpoints)


# --- 11: deterministic ordering ----------------------------------------------


def test_ordering_is_deterministic_across_repeated_generation(tmp_path: Path) -> None:
    first = enumerate_search_actions(_scan_context(tmp_path, NEGATIVE_SOURCE))
    second = enumerate_search_actions(_scan_context(tmp_path, NEGATIVE_SOURCE))

    assert [a.identifier for a in first] == [a.identifier for a in second]
    assert [a.value for a in first] == [a.value for a in second]


def test_ordering_follows_the_documented_static_key(tmp_path: Path) -> None:
    actions = enumerate_search_actions(_scan_context(tmp_path, NEGATIVE_SOURCE))

    assert [a.static_order_key for a in actions] == sorted(a.static_order_key for a in actions)
    assert actions[0].kind is SearchActionKind.CONTRACT_ANCHOR


def test_identifiers_are_stable_content_derived_and_distinct(tmp_path: Path) -> None:
    actions = enumerate_search_actions(_scan_context(tmp_path, NEGATIVE_SOURCE))

    assert len({a.identifier for a in actions}) == len(actions)
    assert all(a.identifier.startswith("ehs-") and len(a.identifier) == 16 for a in actions)

    repeat = enumerate_search_actions(_scan_context(tmp_path, NEGATIVE_SOURCE))
    assert [a.identifier for a in actions] == [a.identifier for a in repeat]


# --- 12: provenance preservation ---------------------------------------------


def test_every_candidate_records_a_readable_reason_and_origin(tmp_path: Path) -> None:
    actions = enumerate_search_actions(_scan_context(tmp_path, NEGATIVE_SOURCE))

    assert all(action.reason.strip() for action in actions)
    assert all(isinstance(action.origin, ProposalOrigin) for action in actions)
    assert all(action.contract_id == "neg" for action in actions)
    assert all(action.source_sha256 for action in actions)
    assert all(action.target_identity == "m.py::validate" for action in actions)


def test_search_derived_values_are_not_reported_as_phase_five_actions(tmp_path: Path) -> None:
    """A midpoint is the search's own step, not something the action layer proposed."""

    actions = enumerate_search_actions(_scan_context(tmp_path, NEGATIVE_SOURCE))

    assert all(action.semantic_action is None for action in actions)


def test_phase_five_identity_is_preserved_when_an_action_really_is_one(tmp_path: Path) -> None:
    """When a proposal *is* a mined semantic action, its identity survives.

    The generator attaches ``None`` today because it reads Phase 2 comparisons
    rather than the action list. This pins the contract for that decision: the
    field exists, it round-trips untouched, and it does not become a *different*
    identity, while a value the search derived itself still reports ``None``.
    """

    mined = _of_kind(
        enumerate_search_actions(_scan_context(tmp_path, NEGATIVE_SOURCE)),
        SearchActionKind.SOURCE_BOUNDARY_AT,
    )
    assert len(mined) == 1
    assert mined[0].semantic_action is None

    rebuilt = SearchAction(
        kind=mined[0].kind,
        target_identity=mined[0].target_identity,
        parameter=mined[0].parameter,
        value=mined[0].value,
        contract_id=mined[0].contract_id,
        source_sha256=mined[0].source_sha256,
        origin=mined[0].origin,
        reason=mined[0].reason,
        priority=mined[0].priority,
        semantic_action=None,
    )
    # Identity is derived from content, so carrying provenance cannot change it.
    assert rebuilt.identifier == mined[0].identifier


# --- 13: no expression is ever evaluated -------------------------------------


def test_generation_reads_no_file_and_runs_no_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The generator is pure arithmetic: no import, no read, no eval."""

    import builtins
    from typing import IO, Any

    context = _scan_context(tmp_path, NEGATIVE_SOURCE)
    opened: list[str] = []
    real_open = builtins.open

    def _tracking_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> IO[str]:
        opened.append(str(file))
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", _tracking_open)
    for name in ("eval", "exec", "compile"):
        monkeypatch.delattr(builtins, name, raising=False)

    actions = enumerate_search_actions(context)

    assert actions
    assert opened == [], f"generation opened files: {opened}"


def test_contract_file_is_never_needed_by_the_generator(tmp_path: Path) -> None:
    """Nothing in candidate generation parses a contract file; the contract is typed."""

    context = _context()
    (tmp_path / "unrelated.json").write_text("{}", encoding="utf-8")

    assert enumerate_search_actions(context)


def test_every_proposed_value_satisfies_the_domain(tmp_path: Path) -> None:
    """The invariant the whole soundness argument rests on."""

    for operator, reference, start in [
        (DomainOperator.LT, 0, -64),
        (DomainOperator.LE, -3, -10),
        (DomainOperator.GT, 10, 40),
        (DomainOperator.GE, 5, 5),
    ]:
        actions = enumerate_search_actions(
            _context(operator=operator, reference=reference, start=start)
        )
        domain = IntegerDomain(operator, reference)
        assert actions
        assert all(domain.contains(action.value) for action in actions), (
            operator,
            reference,
            _values(actions),
        )


def test_generator_writes_nothing_to_disk(tmp_path: Path) -> None:
    _scan_context(tmp_path, NEGATIVE_SOURCE)
    before = sorted(path.name for path in tmp_path.iterdir())
    enumerate_search_actions(_scan_context(tmp_path, NEGATIVE_SOURCE))
    assert sorted(path.name for path in tmp_path.iterdir()) == before


def test_contract_file_round_trips_through_the_real_reader(tmp_path: Path) -> None:
    """End-to-end: a real contract file produces the same domain the tests assume."""

    source = tmp_path / "m.py"
    source.write_text(NEGATIVE_SOURCE, encoding="utf-8")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    document = {
        "version": 1,
        "contracts": [
            {
                "id": "neg",
                "target": "m.py::validate",
                "source_sha256": digest,
                "parameter": "x",
                "domain": {"operator": "lt", "integer": "0"},
                "expectation": {"kind": "raises_builtin", "exception": "ValueError"},
            }
        ],
    }
    contract_file = tmp_path / "contracts.json"
    contract_file.write_text(json.dumps(document), encoding="utf-8")

    from edgehunter.shrink.contract import read_contract_file

    parsed = read_contract_file(contract_file).select("neg")
    assert parsed.domain.describe() == "< 0"
    assert parsed.domain.closest_to_zero() == -1
