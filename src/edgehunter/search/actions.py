"""The bounded, deterministic candidate universe the search selects from.

Everything here is pure arithmetic over integers the caller already holds. This
module does not import target code, read a repository, start a container, or
evaluate a condition written as text. Its entire job is to turn *evidence* --
one user-declared domain, one user-supplied integer, and the boundaries Phase 2
already mined -- into a finite, deduplicated, explainable list of candidate
inputs.

Three properties are load-bearing and each is enforced structurally rather than
by convention:

**Finiteness.** An open-ended inequality such as ``x < 0`` admits infinitely many
integers, so the generator never enumerates a domain. It applies a fixed set of
operators to a fixed set of starting points and then stops at
:attr:`SearchBudgets.bounded_candidates`. There is no unbounded loop anywhere in
this file.

**In-domain or absent.** Every candidate is checked against the *actual* declared
domain before it is offered, using the same :class:`IntegerDomain` the shrinker
uses. A proposal that fails the check simply does not exist, so an out-of-domain
input cannot reach execution and can never be reported as a contract violation.

**One origin each.** Deduplication is by value and keeps the first proposal in
the documented priority order, so a value that is both the anchor and the
user's seed is reported once, with the stronger provenance.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from edgehunter.analysis.models import (
    BoundaryCandidate,
    BoundaryKind,
    ComparisonInfo,
    ComparisonOperator,
    ExpressionInfo,
    ExpressionKind,
    invert_comparison_operator,
)
from edgehunter.execution.models import TargetRef
from edgehunter.generation.materialize import parse_integer_literal
from edgehunter.search.models import (
    MAX_SIGNED_INT,
    MIN_SIGNED_INT,
    ProposalOrigin,
    SearchAction,
    SearchActionKind,
    SearchBudgets,
)
from edgehunter.shrink.contract import Contract

MAX_MIDPOINT_PROPOSALS = 6
"""How many halvings between the start value and the anchor are considered.

Fixed and small. A longer chain would add candidates without adding evidence:
each step's verdict says nothing about the values between the two points, so a
deep bisection is arithmetic, not search.
"""


@dataclass(frozen=True, slots=True)
class SearchContext:
    """The static evidence one search is allowed to build candidates from."""

    contract: Contract
    target: TargetRef
    start_value: int
    budgets: SearchBudgets = SearchBudgets()
    boundaries: tuple[BoundaryCandidate, ...] = ()
    """Phase 2 boundary candidates already mined for this target."""

    comparisons: tuple[ComparisonInfo, ...] = ()
    """Phase 2 comparisons mined for this target.

    Needed because Phase 2 deliberately keeps a *signed* literal symbolic: for
    ``if x == -2`` it records the comparison but declines to emit a boundary
    whose reference is ``-2``. Consuming the comparison directly is reading
    Phase 2's own output, not re-extracting anything, and the recognizer below
    is strict enough that a symbolic reference still cannot become a value.
    """

    def admits(self, value: int) -> bool:
        """Whether one integer may be proposed at all.

        Both checks live together so no caller can apply one and forget the other:
        a value outside the representable range is refused, and so is one the
        declared domain does not contain.
        """

        if not MIN_SIGNED_INT <= value <= MAX_SIGNED_INT:
            return False
        return self.contract.domain.contains(value)


def enumerate_search_actions(context: SearchContext) -> tuple[SearchAction, ...]:
    """Build the finite, ordered, deduplicated candidate universe.

    Proposals are generated in the documented priority order -- contract anchor,
    user seed, source boundaries, midpoints, neighbours -- deduplicated by
    value, sorted by the shared static order key, and finally truncated to the
    configured limit. Because the sort is total, the truncation is deterministic:
    the same repository and contract always yield the same universe.
    """

    proposals: list[SearchAction] = []
    seen: set[int] = set()

    def offer(action: SearchAction) -> None:
        if action.value in seen:
            return
        if not context.admits(action.value):
            # The domain check is the whole point: an out-of-domain input is not
            # a weaker candidate, it is not a candidate.
            return
        seen.add(action.value)
        proposals.append(action)

    anchor = _anchor(context)
    offer(_anchor_action(context, anchor))

    if context.start_value != anchor:
        offer(_seed_action(context, context.start_value))

    for action in _source_boundary_actions(context):
        offer(action)

    for action in _comparison_boundary_actions(context):
        offer(action)

    for action in _direction_actions(context, anchor):
        offer(action)

    for action in _midpoint_actions(context, anchor):
        offer(action)

    for action in _neighbour_actions(context, anchor):
        offer(action)

    ordered = sorted(proposals, key=lambda action: action.static_order_key)
    limit = context.budgets.bounded_candidates()
    return tuple(
        SearchAction(
            kind=action.kind,
            target_identity=action.target_identity,
            parameter=action.parameter,
            value=action.value,
            contract_id=action.contract_id,
            source_sha256=action.source_sha256,
            origin=action.origin,
            reason=action.reason,
            priority=rank,
        )
        for rank, action in enumerate(ordered[:limit])
    )


def _anchor(context: SearchContext) -> int:
    """The closest-to-zero admissible integer, reusing the shrinker's helper.

    Not reimplemented. The minimizer needs the same starting point for the same
    reason, and two copies of "the anchor" could drift apart and silently make
    the two searches incomparable.
    """

    return context.contract.domain.closest_to_zero()


def _anchor_action(context: SearchContext, anchor: int) -> SearchAction:
    contract = context.contract
    return SearchAction(
        kind=SearchActionKind.CONTRACT_ANCHOR,
        target_identity=context.target.identity,
        parameter=contract.parameter,
        value=anchor,
        contract_id=contract.contract_id,
        source_sha256=contract.source_sha256,
        origin=ProposalOrigin.CONTRACT_BOUNDARY,
        reason=(
            f"closest-to-zero admissible integer of the declared domain "
            f"{contract.parameter} {contract.domain.describe()}; deterministic, but not "
            "guaranteed to be a failing or even a meaningful input"
        ),
        priority=0,
    )


def _seed_action(context: SearchContext, value: int) -> SearchAction:
    contract = context.contract
    return SearchAction(
        kind=SearchActionKind.USER_SEED,
        target_identity=context.target.identity,
        parameter=contract.parameter,
        value=value,
        contract_id=contract.contract_id,
        source_sha256=contract.source_sha256,
        origin=ProposalOrigin.USER_SUPPLIED_SEED,
        reason=(
            "the integer the user explicitly supplied as the search's starting point; recorded "
            "as a seed, not as a discovered action"
        ),
        priority=0,
    )


_BOUNDARY_ACTIONS: dict[BoundaryKind, tuple[SearchActionKind, int]] = {
    BoundaryKind.AT_BOUNDARY: (SearchActionKind.SOURCE_BOUNDARY_AT, 0),
    BoundaryKind.JUST_BELOW: (SearchActionKind.SOURCE_BOUNDARY_BELOW, -1),
    BoundaryKind.JUST_ABOVE: (SearchActionKind.SOURCE_BOUNDARY_ABOVE, 1),
}


def _source_boundary_actions(context: SearchContext) -> list[SearchAction]:
    """Reuse Phase 2's mined boundaries, keeping only concrete integers.

    Phase 2 deliberately leaves references symbolic -- for ``amount <= balance``
    it records "amount at balance", never ``balance - 1``. This function consumes
    only a reference that parses as a decimal integer, and silently drops
    everything else. That is the right behaviour rather than a limitation being
    hidden: a symbolic reference names a value that does not exist yet, and
    inventing one would be fabricating an input the source never mentions.
    """

    contract = context.contract
    actions: list[SearchAction] = []
    for candidate in context.boundaries:
        if candidate.parameter != contract.parameter:
            continue
        mapped = _BOUNDARY_ACTIONS.get(candidate.kind)
        if mapped is None:
            # Numeric generics (ZERO, ONE, NaN, infinities) and the float shapes
            # are not integer inputs; the negative ones that matter are already
            # the anchor's job.
            continue
        reference_text = candidate.reference
        if reference_text is None:
            continue
        reference = parse_integer_literal(reference_text.strip())
        if reference is None:
            continue
        kind, offset = mapped
        value = reference + offset
        if not MIN_SIGNED_INT <= value <= MAX_SIGNED_INT:
            continue
        lines = ", ".join(str(line) for line in candidate.evidence)
        actions.append(
            SearchAction(
                kind=kind,
                target_identity=context.target.identity,
                parameter=contract.parameter,
                value=value,
                contract_id=contract.contract_id,
                source_sha256=contract.source_sha256,
                origin=ProposalOrigin.STATIC_SOURCE_BOUNDARY,
                reason=(
                    f"Phase 2 mined {candidate.kind.value} {reference_text} for "
                    f"{contract.parameter} at line(s) {lines}; {candidate.reason}"
                ),
                priority=0,
            )
        )
    return actions


_ORDERED_OPERATORS: Final[frozenset[ComparisonOperator]] = frozenset(
    {
        ComparisonOperator.LESS_THAN,
        ComparisonOperator.LESS_THAN_OR_EQUAL,
        ComparisonOperator.GREATER_THAN,
        ComparisonOperator.GREATER_THAN_OR_EQUAL,
    }
)


def _signed_integer_literal(expression: ExpressionInfo) -> int | None:
    """Read a plain signed integer literal, or refuse.

    Phase 2 classifies ``-2`` as a unary expression and keeps it symbolic, which
    is the right call for boundary *description* but loses the one comparison
    pattern a search most wants: a target that special-cases a negative integer.
    So this reads it back, with a character-level test rather than a pattern
    engine and with ``int()`` rather than ``eval``: an optional single sign,
    then decimal digits, and nothing else.

    Everything else returns ``None`` -- ``-limit``, ``-2 + 3``, ``-x``, a float,
    a call -- and therefore becomes no candidate at all. Inventing a value for a
    reference the source names but does not define would be fabricating an input
    the code never mentions.
    """

    text = expression.text.strip()
    body = text[1:] if text[:1] in ("-", "+") else text
    if not body or not body.isdigit() or not body.isascii():
        return None
    # `parse_integer_literal` is the generation layer's existing reader; reusing
    # it means two layers cannot disagree about what an integer literal is.
    return parse_integer_literal(text)


def _comparison_reference(
    comparison: ComparisonInfo, parameter: str
) -> tuple[ComparisonOperator, int, str] | None:
    """Resolve ``<parameter> <op> <literal>``, or return ``None``.

    The parameter may be on either side; when it is on the right the operator is
    inverted through the one shared primitive Phase 2 already uses, so the two
    layers cannot disagree about which comparisons are orderable.
    """

    operator: ComparisonOperator
    reference_side: ExpressionInfo
    inverted = invert_comparison_operator(comparison.operator)
    if comparison.left.text.strip() == parameter and comparison.left.kind is ExpressionKind.NAME:
        operator = comparison.operator
        reference_side = comparison.right
    elif (
        comparison.right.text.strip() == parameter
        and comparison.right.kind is ExpressionKind.NAME
        and inverted is not None
    ):
        operator = inverted
        reference_side = comparison.left
    else:
        return None

    reference = _signed_integer_literal(reference_side)
    if reference is None or not MIN_SIGNED_INT <= reference <= MAX_SIGNED_INT:
        return None
    return operator, reference, reference_side.text.strip()


def _comparison_boundary_actions(context: SearchContext) -> list[SearchAction]:
    """Propose integers at, and immediately around, each mined comparison.

    The set follows Phase 2's own documented rule (section 8 of ``flow.md``): an
    ordered comparison against a concrete reference offers below, at, and above;
    ``==`` and ``!=`` offer only the reference itself. Every proposal is still
    checked against the declared domain before it is offered, so an in-source
    boundary that the contract does not govern simply never appears.
    """

    contract = context.contract
    actions: list[SearchAction] = []
    for comparison in context.comparisons:
        resolved = _comparison_reference(comparison, contract.parameter)
        if resolved is None:
            continue
        operator, reference, reference_text = resolved
        offsets = (
            (
                (SearchActionKind.SOURCE_BOUNDARY_BELOW, -1),
                (SearchActionKind.SOURCE_BOUNDARY_AT, 0),
                (SearchActionKind.SOURCE_BOUNDARY_ABOVE, 1),
            )
            if operator in _ORDERED_OPERATORS
            else ((SearchActionKind.SOURCE_BOUNDARY_AT, 0),)
        )
        for kind, offset in offsets:
            value = reference + offset
            if not MIN_SIGNED_INT <= value <= MAX_SIGNED_INT:
                continue
            actions.append(
                SearchAction(
                    kind=kind,
                    target_identity=context.target.identity,
                    parameter=contract.parameter,
                    value=value,
                    contract_id=contract.contract_id,
                    source_sha256=contract.source_sha256,
                    origin=ProposalOrigin.STATIC_SOURCE_BOUNDARY,
                    reason=(
                        f"Phase 2 mined the comparison {contract.parameter} {operator.value} "
                        f"{reference_text} at line {comparison.lineno}, read back as a concrete "
                        "signed integer literal"
                    ),
                    priority=0,
                )
            )
    return actions


def _direction_actions(context: SearchContext, anchor: int) -> list[SearchAction]:
    """One step from the seed towards zero, and one step away from it."""

    seed = context.start_value
    if seed == 0:
        # "Towards" and "away from" zero coincide at zero; proposing it twice
        # under two names would be the same input listed twice.
        return []
    toward = seed - (1 if seed > 0 else -1)
    away = seed + (1 if seed > 0 else -1)
    contract = context.contract
    actions: list[SearchAction] = []
    for kind, value, direction in (
        (SearchActionKind.STEP_TOWARD_ZERO, toward, "towards"),
        (SearchActionKind.STEP_AWAY_FROM_ZERO, away, "away from"),
    ):
        actions.append(
            SearchAction(
                kind=kind,
                target_identity=context.target.identity,
                parameter=contract.parameter,
                value=value,
                contract_id=contract.contract_id,
                source_sha256=contract.source_sha256,
                origin=ProposalOrigin.SEARCH_NEIGHBOR,
                reason=f"one unit from the supplied seed {seed} {direction} zero",
                priority=0,
            )
        )
    return actions


def _midpoint_actions(context: SearchContext, anchor: int) -> list[SearchAction]:
    """Repeated halvings from the seed towards the anchor.

    Floor division, no randomness, and a stop as soon as two consecutive
    proposals coincide -- which is what bounds it even before
    :data:`MAX_MIDPOINT_PROPOSALS`.
    """

    contract = context.contract
    actions: list[SearchAction] = []
    current = context.start_value
    for _ in range(MAX_MIDPOINT_PROPOSALS):
        midpoint = (current + anchor) // 2
        if midpoint == current or midpoint == anchor:
            break
        if not context.admits(midpoint):
            break
        actions.append(
            SearchAction(
                kind=SearchActionKind.MIDPOINT_TO_ANCHOR,
                target_identity=context.target.identity,
                parameter=contract.parameter,
                value=midpoint,
                contract_id=contract.contract_id,
                source_sha256=contract.source_sha256,
                origin=ProposalOrigin.SEARCH_MIDPOINT,
                reason=(
                    f"floor midpoint between {current} and the anchor {anchor}; a verdict here "
                    "says nothing about the values between the two points"
                ),
                priority=0,
            )
        )
        current = midpoint
    return actions


def _neighbour_actions(context: SearchContext, anchor: int) -> list[SearchAction]:
    """The adjacent admissible integers of the anchor and of the seed."""

    contract = context.contract
    actions: list[SearchAction] = []
    for base, base_label in ((anchor, "the contract anchor"), (context.start_value, "the seed")):
        for value, kind, direction in (
            (base - 1, SearchActionKind.INTEGER_NEIGHBOR_BELOW, "below"),
            (base + 1, SearchActionKind.INTEGER_NEIGHBOR_ABOVE, "above"),
        ):
            if not MIN_SIGNED_INT <= value <= MAX_SIGNED_INT:
                continue
            actions.append(
                SearchAction(
                    kind=kind,
                    target_identity=context.target.identity,
                    parameter=contract.parameter,
                    value=value,
                    contract_id=contract.contract_id,
                    source_sha256=contract.source_sha256,
                    origin=ProposalOrigin.SEARCH_NEIGHBOR,
                    reason=f"the nearest admissible integer {direction} {base_label} {base}",
                    priority=0,
                )
            )
    return actions
