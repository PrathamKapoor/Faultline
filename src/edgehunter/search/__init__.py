"""Budgeted, feedback-guided semantic neighborhood search.

Until now every candidate EdgeHunter executed came from a list computed before
execution began. This package introduces the missing feedback loop:

.. code-block:: text

    available actions -> policy selects one -> Docker executes it
                      -> the applicable oracle judges it -> typed feedback
                      -> the state updates -> the next action is selected

The difference that matters is the last step. The next input is chosen *because
of what the previous input did*, and only one of the two policies here actually
does that. :class:`~edgehunter.search.policy.StaticPriorityPolicy` deliberately
does not, because a comparison is only worth reading if the baseline is honest.

Three properties are structural rather than aspirational:

* **The oracle must still apply.** Every proposed input is checked against the
  declared contract domain during enumeration and again immediately before
  execution. An input the contract does not govern is not a weaker candidate, it
  is not a candidate.
* **One mismatch is not a finding.** A contradiction is re-executed on the same
  staged snapshot and must agree on its failure signature before it is reported.
  Two executions that disagree produce ``FLAKY_OBSERVATION``, not a discovery.
* **A failed container is not progress.** An infrastructure failure consumes its
  execution slot and produces its own typed feedback, which no policy may read
  as a reward.

Nothing here is a learned model, and no benchmark is claimed. The policies are
deterministic heuristics, and their purpose is to be the baseline a future small
model has to beat under equal budgets.
"""

from edgehunter.search.actions import (
    MAX_MIDPOINT_PROPOSALS,
    SearchContext,
    enumerate_search_actions,
)
from edgehunter.search.models import (
    DEFAULT_CANDIDATE_LIMIT,
    DEFAULT_MAX_EXECUTIONS,
    DEFAULT_MAX_PROBES,
    DEFAULT_REPRODUCTION_ATTEMPTS,
    DEFAULT_WALL_CLOCK_SECONDS,
    HARD_MAX_CANDIDATES,
    HARD_MAX_EXECUTIONS,
    HARD_MAX_PROBES,
    HARD_MAX_WALL_CLOCK_SECONDS,
    MAX_SIGNED_INT,
    MIN_SIGNED_INT,
    TARGET_INDEPENDENT_OUTCOMES,
    PolicyName,
    ProposalOrigin,
    SearchAction,
    SearchActionKind,
    SearchBudgets,
    SearchFeedback,
    SearchResult,
    SearchSelection,
    SearchState,
    SearchStep,
    SearchTermination,
    SearchValueError,
    SelectionUse,
    feedback_for,
    is_target_independent,
)
from edgehunter.search.policy import (
    FeedbackGuidedPolicy,
    SearchPolicy,
    StaticPriorityPolicy,
    policy_for,
)
from edgehunter.search.repository import (
    SEARCH_ARTIFACT_SCHEMA,
    ExploreRequest,
    ProbeRunner,
    SessionProbeRunner,
    explore_integer,
    search_artifact_document,
    write_search_artifact,
)
from edgehunter.search.state import (
    note_first_mismatch,
    note_reproduced,
    record_step,
    terminate,
    update_state,
)

__all__ = [
    "DEFAULT_CANDIDATE_LIMIT",
    "DEFAULT_MAX_EXECUTIONS",
    "DEFAULT_MAX_PROBES",
    "DEFAULT_REPRODUCTION_ATTEMPTS",
    "DEFAULT_WALL_CLOCK_SECONDS",
    "HARD_MAX_CANDIDATES",
    "HARD_MAX_EXECUTIONS",
    "HARD_MAX_PROBES",
    "HARD_MAX_WALL_CLOCK_SECONDS",
    "MAX_MIDPOINT_PROPOSALS",
    "MAX_SIGNED_INT",
    "MIN_SIGNED_INT",
    "SEARCH_ARTIFACT_SCHEMA",
    "TARGET_INDEPENDENT_OUTCOMES",
    "ExploreRequest",
    "FeedbackGuidedPolicy",
    "PolicyName",
    "ProbeRunner",
    "ProposalOrigin",
    "SearchAction",
    "SearchActionKind",
    "SearchBudgets",
    "SearchContext",
    "SearchFeedback",
    "SearchPolicy",
    "SearchResult",
    "SearchSelection",
    "SearchState",
    "SearchStep",
    "SearchTermination",
    "SearchValueError",
    "SelectionUse",
    "SessionProbeRunner",
    "StaticPriorityPolicy",
    "enumerate_search_actions",
    "explore_integer",
    "feedback_for",
    "is_target_independent",
    "note_first_mismatch",
    "note_reproduced",
    "policy_for",
    "record_step",
    "search_artifact_document",
    "terminate",
    "update_state",
    "write_search_artifact",
]
