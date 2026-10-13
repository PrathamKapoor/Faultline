# EdgeHunter — Session Handoff

## 1. Current Phase

- **Phase:** Phase 7C — Budgeted Adaptive Semantic Neighborhood Search
- **Subphase:** none (single-session phase)
- **Objective:** Make EdgeHunter's next executed input a function of the previous
  input's observed behaviour, under a policy interface a future learned model can
  replace, and measure it against an honest nonadaptive baseline.
- **Status:** complete

Repository state at handoff: branch `main`, remote `origin` ->
`PrathamKapoor/Faultline`. Implementation commit `bf03a2b` ("Select the next
semantic input from the previous probe's verdict"). Phase 7B, which was uncommitted
at the start of this session, is now committed as `bd7b46c`. Version remains
`0.1.0`; no bump was made (D-002 makes the version literal the single source and
no decision requires a bump).

## 2. Work Completed

Baseline at the start of this session: `1008 passed, 59 skipped` with the Docker
daemon down (58 skips were Docker integration tests, 1 was a symlink permission).
The brief's stated baseline of `1066 passed, 1 skipped` could not be reproduced,
and investigation showed why: **Phase 7B was never committed** and `handoff.md`
still described Phase 7A. See §11.

Docker Desktop 4.93.0 was already installed; it was started (not installed, not
reconfigured) to obtain a real Linux daemon, after which the full Docker
integration suite ran for real. This session added:

Blocking, before any code:

- Phase 7B committed as `bd7b46c` rather than left as untracked work, since
  Phase 7C builds directly on its contract parser, domain, signature, and
  execution route

Architecture:

- `src/edgehunter/search/` — a new package in five modules with a strict split:
  `models.py` (typed vocabulary), `actions.py` (pure candidate universe),
  `policy.py` (pure selection), `state.py` (pure transitions), `repository.py`
  (orchestration, budgets, artifact)
- `SearchAction` with ten `SearchActionKind` values crossed with five
  `ProposalOrigin` values, a content-derived `ehs-…` identifier, and an optional
  `semantic_action` field that is `None` for every search-derived value (D-094)
- `SearchFeedback` with six members, mapped exactly from the existing evaluator's
  verdict in one total function `feedback_for()` (D-100, D-105)
- `SearchTermination` with ten distinct statuses that are never collapsed (D-105)
- `enumerate_search_actions()` — anchor, user seed, mined boundaries, bounded
  midpoint chain, integer neighbours; deduplicated, totally sorted, truncated to
  a cap; every value domain-checked inside one `admits()` helper (D-095)
- `_comparison_boundary_actions()` reads Phase 2's mined comparisons directly, so
  a target that special-cases a *signed* integer (`if x == -2`) contributes a
  boundary that Phase 2's miner deliberately keeps symbolic (D-096)
- `StaticPriorityPolicy` — the documented fixed order, reads no feedback at all
  (D-097)
- `FeedbackGuidedPolicy` — after a match, the nearest untested in-domain
  neighbour; an explicit decision table coded literally rather than described in
  prose (D-098)
- Immutable `SearchState` advanced only by pure transitions, with no
  process-global registry anywhere in the package (D-093)
- `explore_integer()` orchestrating one snapshot-fixed session: all static gates
  via the shrink package's shared `preflight_contract()`, one `_stage_workspace`
  for the whole search, one `SourceSnapshot.verify()` before the first probe
  (D-102, D-103)
- `SessionProbeRunner` calling the existing `ExecutionSession.execute` — no new
  execution primitive, no new staging, no new evaluator

Three defects found and fixed during development:

- **Reproduction was inside the feedback policy**, which meant the static baseline
  could never report `violation_reproduced` at all — the matched-budget
  comparison would have measured strictness, not ordering. Reproduction is now
  orchestrator-owned and applies to both policies identically (D-099).
- **`update_state()` counted a recheck by execution count** rather than by whether
  the candidate was already tested, so a probe that ran and rechecked in one call
  misreported its own accounting. Found by a unit test (D-104).
- **Infrastructure failures were not split.** A timeout stopped the search even
  though a timeout can genuinely depend on the input. `SearchState.last_outcome`
  now carries the raw outcome and `TARGET_INDEPENDENT_OUTCOMES` decides (D-100).

CLI and docs:

- `edgehunter explore PATH --target … --contract-file … --contract-id … --start-int
  … [--policy static|feedback] [--max-probes] [--max-executions] [--timeout]
  [--artifact-id] --allow-execution`
- The measurements block is printed only when something actually executed, so a
  refusal is never followed by a table of zeroes (D-107)
- `.edgehunter/runs/<run-id>/search.json`: versioned, evidence-only, sanitized by
  the same `_safe_run_id` as the Phase 6B and 7B artifacts (D-106)
- README section "Searching around one integer input"; `decisions.md` D-093
  through D-110; `flow.md` section 20 (D-110)

Tests: 149 new non-integration tests across five files, plus 10 real-Docker
integration tests.

## 3. Files Changed

Created:

```text
src/edgehunter/search/__init__.py
src/edgehunter/search/models.py
src/edgehunter/search/actions.py
src/edgehunter/search/policy.py
src/edgehunter/search/state.py
src/edgehunter/search/repository.py
tests/test_search_candidates.py
tests/test_search_policy.py
tests/test_search_loop.py
tests/test_search_cli.py
tests/test_search_integration.py
```

Modified:

```text
src/edgehunter/cli.py                      explore command, _print_search, _index_text
src/edgehunter/shrink/repository.py        added public preflight_contract() wrapper
src/edgehunter/shrink/__init__.py          export preflight_contract
README.md                                  explore section, status, roadmap, architecture
decisions.md                               D-093..D-110
flow.md                                    section 20 + the Phase 7C changes table
handoff.md                                 this file
```

**No existing test was modified or weakened.** Every pre-existing test still
passes unchanged.

## 4. Current Architecture / State

```text
explore_integer(scan, root, request, authorized, repository_context, runner)
 │
 ├─ _run_static_gates ─────► preflight_contract()  [shrink]  ──► REFUSED / INCONCLUSIVE
 │                                                                / CONFLICTING_EVIDENCE
 ├─ enumerate_search_actions(SearchContext)       [search.actions]  pure arithmetic
 │
 └─ open_session() ────────────────────────────────► [execution]  ONE staged snapshot
      └─ SourceSnapshot.verify() ──────────────────► [evaluation]  INCONCLUSIVE if unverified
      └─ _run_loop(contract, state, policy, runner)
           ├─ _budget_stop(state, started, recheck_pending)
           ├─ pending recheck?  ──► _recheck_selection(pending)
           ├─ else               ──► policy.select(state, state.available)
           ├─ contract.domain.contains(action.value)          ← third domain check
           ├─ runner.execute_probe(action)                    [SessionProbeRunner]
           ├─ evaluate_against_contract(...)                  [shrink]  ← the only adapter
           ├─ feedback_for(outcome, status)                   [search.models]
           ├─ _judge_recheck(...)  or  PendingRecheck(...)
           ├─ update_state(...)                               [search.state]  pure
           └─ record_step(...)                               [search.state]  pure
```

Separation of concerns, as required:

| Concern | Module | May import |
|---|---|---|
| what input is proposed | `search/actions.py` | `analysis`, `shrink` (typed models only) |
| which input comes next | `search/policy.py` | `search.models` only |
| what happened | `search/models.py::feedback_for` | `evaluation.evaluator` |
| how state advances | `search/state.py` | `search.models`, `execution.models` |
| when to stop | `search/repository.py` | everything, and only here |

A policy cannot start a container, import target code, or evaluate an oracle. A
test asserts `time.monotonic`, `random.random`, and `random.randint` all raise
during selection.

## 5. Decisions Made

D-093 through D-110, all new this phase, all in `decisions.md`:

| Decision | Summary |
|---|---|
| D-093 | A new `search` package; action, policy, and feedback stay separate |
| D-094 | Closed ten-kind integer action vocabulary with five recorded origins |
| D-095 | Finite-by-construction universe; every value domain-checked before it is offered |
| D-096 | Phase 2 comparisons read directly so a signed literal boundary is not lost |
| D-097 | Static baseline order fixed once, total, never revised after seeing a result |
| D-098 | Feedback policy prefers the nearest untested in-domain neighbour after a match |
| D-099 | Reproduction is the orchestrator's rule, applied identically to both policies |
| D-100 | Infrastructure failures split into target-independent and candidate-specific |
| D-101 | Both policies share one universe, one budget, one reproduction threshold |
| D-102 | Every static gate delegated to the shrink package's shared preflight |
| D-103 | The oracle is checked for every probe, three times, at three different times |
| D-104 | Budgets clamped on read, counted on every launch, never escalate |
| D-105 | Ten distinct termination statuses; no measurement that was not counted |
| D-106 | The trace carries no duration; the artifact reuses the run-id sanitizer |
| D-107 | `explore` is explicit and opt-in, and adds no automatic path |
| D-108 | The search package adds no dependency and reaches no new execution primitive |
| D-109 | `explore` discovers a contradiction and stops; it never shrinks |
| D-110 | A measured comparison is one observation, never a percentage |

No decision was superseded.

## 6. Requirements and Constraints

- Docker is the only execution backend; there is no host fallback, and none was
  added.
- Docker hardening is unchanged. `execution/docker.py` was **not modified** this
  phase; a test re-asserts every flag on the shared command builder.
- No dependency was added. `pyproject.toml` is unchanged; `typer` remains the
  only runtime dependency.
- No Hypothesis, LLM, embeddings, RAG, FAISS, Z3, SymPy, or NumPy.
- No multi-parameter mutation, no stateful sequences, no shrinking inside search.
- All contracts authored `PrathamKapoor <prathamkapoor027@gmail.com>`, with no
  co-author or attribution trailers.
- `paper/`, `research/`, and `*.tex` were never staged.

## 7. Testing and Verification

Final results, Docker daemon live:

```text
.venv\Scripts\python.exe -m pytest -q -m "not integration"
  → 1157 passed, 1 skipped, 68 deselected   (47.81s)

.venv\Scripts\python.exe -m pytest -q -m integration
  → 68 passed, 1158 deselected              (487.23s)

.venv\Scripts\python.exe -m ruff check .
  → All checks passed!

.venv\Scripts\python.exe -m ruff format --check .
  → 119 files already formatted

.venv\Scripts\python.exe -m mypy
  → Success: no issues found in 115 source files
```

**Combined: 1225 passed, 1 skipped.** The single skip is
`tests/test_execution_staging.py:109`, "symlinks are unavailable for this user on
this platform" — a pre-existing platform limitation, not a gap in this phase.

Two full monolithic runs (`pytest -q`, ~10 minutes each) each produced exactly one
failure, and **a different pre-existing test each time**:

```text
run 1: tests/test_execution_integration.py::TestCleanup::test_result_documents_never_reach_the_container_stdout
       → TIMEOUT instead of RETURNED
run 2: tests/test_shrink_integration.py::TestDFailureSignatureChanges::test_a_trial_with_a_different_failure_is_rejected
       → TIMEOUT instead of the expected verdict
```

Both are Docker container timeouts under the load of ~1200 back-to-back tests, and
both reproduce as passing in isolation. Neither was confirmed to be caused by
this phase: the second is in `test_shrink_integration.py`, which this phase did
not touch, and `test_result_documents_never_reach_the_container_stdout` exercises
the `run` command, which was not modified. Splitting the run into the two halves
above makes both halves deterministic. **Running `pytest -q` as a single command
is not currently reliable on this host.**

Real-Docker integration (`tests/test_search_integration.py`, 10 passed, 51.56s) —
Tests A through H from the phase brief:

| Case | Outcome |
|---|---|
| A — every in-domain probe matches a correct target | 0 mismatches, 0 reproduced, no bug claim |
| B — feedback finds and reproduces a nearby violation | `-1` MATCH → `-2` MISMATCH → recheck MISMATCH → `violation_reproduced` |
| B — `-2` is not hardcoded | selected action is `source_boundary_at` / `static_source_boundary`, reason contains `x == -2` |
| C — static baseline, same snapshot and budget | reproduced at probe 3 vs probe 2 |
| D — out-of-domain start value | `refused`, 0 executions, no container created |
| E — unavailable sandbox (monkeypatched) | `sandbox_unavailable`, 0 executions, no fallback |
| F — contract bound to changed source | `refused`, 0 executions, no container created |
| G — all probes share one staged snapshot | every step's fingerprint equals the analyzed digest |
| H — cleanup after success, finding, and budget stop | container list identical before and after |
| H — sandbox flags unchanged | all eight hardening flags present, no docker.sock, read-only mounts |

Test F (flaky target) is covered by unit tests with a fabricated runner
(`test_a_mismatch_then_a_match_is_flaky_not_a_finding`,
`test_a_mismatch_then_a_different_exception_is_not_the_same_failure`) rather than
by a deliberately flaky container, per the brief's allowance.

## 8. Known Issues / Risks

1. **Monolithic `pytest -q` is flaky on this host.** One Docker container timeout
   per ~10-minute run, a different pre-existing test each time. See §7. This is
   not a Phase 7C regression but it will confuse a future session; splitting the
   run by `-m integration` avoids it.
2. **One fixture is not a benchmark.** The feedback policy reproduced a
   contradiction in 2 distinct probes where the static baseline took 3. That is
   one observation on one fixture. No percentage, no general claim, and none is
   supported.
3. **The phase-specific neighbourhood is narrow by design.** One integer
   parameter, one inequality, one expected builtin exception, one changed
   parameter at a time. No strings, collections, floats, or multiple parameters.
4. **Midpoint verdicts carry no information about the values between.** The
   generator says so in every reason string, but the limitation is structural:
   a verdict at a midpoint says nothing about the half-interval around it.
5. **The candidate universe is static per search.** Feedback reorders what was
   already generated; it cannot add an action that was not in the universe at the
   start. That is a deliberate bound, but it means the policy can never widen its
   own reach.
6. **Phase 2's boundary miner still keeps signed literals symbolic.** Phase 7C
   reads the comparisons back (D-096) rather than changing Phase 2. Any other
   consumer of `TargetBoundaries` still does not see `-2`.
7. **The worker image is cached.** After editing `runtime/worker.py` an old
   `edgehunter-runtime:1` still contains the previous worker; `docker image rm
   edgehunter-runtime:1` forces a rebuild. This phase did not edit the worker, so
   the risk was not triggered, but the next phase that does must remember it.

## 9. Unfinished Work

Nothing in the Phase 7C scope is unfinished. Deliberately deferred:

- a learned policy and any model training (D-098's `SearchPolicy` protocol is the
  seam)
- an empirical benchmark corpus and action-effectiveness evaluation
- multi-type semantic neighbourhoods
- broader oracle extraction and framework-specific invocation support
- automatic shrinking from a discovered contradiction (D-109 keeps them separate)

## 10. Next Subphase

**Phase 8 — Search Evaluation Harness + Benchmark Corpus**

The natural next step is not a better policy but the ability to *measure* one.
The phase's own numbers show why: a single fixture cannot tell a good policy from
a lucky one.

Expected shape:

- a harness that runs both policies across many real targets under matched
  budgets and records the measurement fields D-105 already defines
- a corpus built from *permissively licensed* repositories, or from the project's
  own fixtures, with every contract written by hand and bound to a real digest
- per-fixture results plus an aggregate, reported as counts of probes and
  containers — never as a coverage figure, a probability, or a percentage that
  the corpus cannot support
- the discipline D-110 already established: if the feedback policy does not win
  on a fixture, that is recorded too

The `SearchPolicy` protocol from D-093 is the insertion point for a learned policy
once there is a corpus to measure it against.

## 11. Critical Context

**The starting repository state did not match the brief.** Recorded here because
the discrepancy is itself a finding:

- The brief stated "Working tree is clean" alongside "git status shows modified
  production files and new shrink sources". Both cannot be true.
- `git status` showed 10 modified tracked files and `src/edgehunter/shrink/` plus
  five shrink test files **untracked**. Phase 7B was uncommitted.
- `handoff.md` described **Phase 7A** ("Action Coverage Repair + Exception
  Identity Hardening"), not Phase 7B. It had never been rewritten for 7B.
- HEAD was `8519c75`, "Record the Phase 7A commit hash and rewrite the session
  handoff". **No Phase 7B commit existed**, so the brief's request to "verify
  the actual Phase 7B commit hashes" could not be satisfied — there were none to
  verify, and none were invented.
- The brief's baseline of `1066 passed, 1 skipped` was not reproducible. Observed:
  `1008 passed, 59 skipped`, because the Docker daemon was down.
- The brief's expected version `0.1.0` was correct, and remains so.

Resolution: the uncommitted Phase 7B work was preserved and committed as
`bd7b46c` (not overwritten, reset, or stashed); Docker Desktop was started to
obtain the Linux daemon; the real baseline was then measured.

**Reusable APIs this phase depends on** — do not reimplement any of them:

| Need | Existing function |
|---|---|
| contract parsing | `shrink.contract.read_contract_file()` |
| all static gates | `shrink.repository.preflight_contract()` |
| domain applicability | `shrink.domain.IntegerDomain.contains()` |
| domain anchor | `shrink.domain.IntegerDomain.closest_to_zero()` |
| contract verdict | `shrink.repository.evaluate_against_contract()` |
| failure signature | `shrink.signature.signature_of()` |
| typed request for one integer | `shrink.repository._seed_request()` |
| one snapshot-fixed session | `execution.repository.open_session()` / `ExecutionSession` |
| snapshot verification | `evaluation.repository.SourceSnapshot.verify()` |
| infrastructure classification | `evaluation.evaluator.is_infrastructure_outcome()` |
| exception identity | `evaluation.exceptions.match_exception_observed()` |
| operator inversion | `analysis.models.invert_comparison_operator()` |
| integer literal parsing | `generation.materialize.parse_integer_literal()` |
| artifact run-id sanitizing | `evaluation.repository._safe_run_id()` |

**Things that look wrong but are deliberate:**

- `SearchAction.semantic_action` is always `None` today. The field exists so a
  genuine Phase 5 action keeps its identity; reporting a search-derived midpoint
  as an action-layer proposal would be false provenance.
- `_seed_request` is imported despite the leading underscore. It is the one place
  that turns an integer into a typed protocol value; copying it would let a
  search input arrive as evaluated text.
- `preflight_contract` is a thin public wrapper around a private function rather
  than a rename, so Phase 7B's own tests and call sites keep working.
- The static policy still reproduces findings. It is a baseline for *ordering*,
  not for strictness.

## 12. Agent Instructions

- Read `decisions.md`, `flow.md`, and this file completely before changing code.
  D-093 through D-110 are authoritative for the search package.
- Verify the working tree before assuming anything. The Phase 7B incident in §11
  happened because a written claim was trusted over `git status`.
- Run the suite in two halves, not one command:

  ```powershell
  .venv\Scripts\python.exe -m pytest -q -m "not integration"
  .venv\Scripts\python.exe -m pytest -q -m integration
  ```

  A single `pytest -q` is flaky on this host (§8). A single failure in the
  monolithic run is not evidence of a regression; re-run the test in isolation
  and check whether this phase touched that path.
- Start Docker Desktop if the daemon is unreachable. It is installed; it is not
  running by default. Never delete unrelated Docker images or containers.
- If you edit `runtime/worker.py`, run `docker image rm edgehunter-runtime:1`
  before the integration tests or they will exercise the previous worker.
- Do not add a policy that can reproduce findings differently from another
  policy (D-099). Do not give one policy a larger budget (D-101). Do not reorder
  the static baseline after seeing a result (D-097, D-110).
- Do not add a domain check in only one of the three places D-103 names.
- Do not add a coverage, probability, confidence, or severity field. If it
  cannot be counted, omit it (D-105).
- Do not report a single fixture as general superiority. Print the numbers and
  say what they are (D-110).
- Do not modify `execution/docker.py` unless a verified defect requires it, and
  document and test any such change separately.
- Keep `scan` static and unchanged. `scan` must never import `shrink` or `search`.
- Commit only as `PrathamKapoor <prathamkapoor027@gmail.com>`, with no co-author
  or attribution trailers. Never stage `paper/`, `research/`, or `*.tex`.
- Append a decision to `decisions.md` for every architectural change, update
  `flow.md` with real function names, and use `uncommitted` until a hash exists.