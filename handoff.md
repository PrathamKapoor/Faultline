# EdgeHunter — Session Handoff

## 1. Current Phase

- **Phase:** Final consolidation — audit, benchmark, package, document, prepare
  for publication
- **Objective:** Take the accumulated multi-phase implementation and make it a
  coherent, independently verifiable developer tool — without adding claims the
  evidence does not support.
- **Status:** complete, with one named publication blocker (see §8)

Commits in this session: `bd7b46c` (Phase 7B, committed late), `bf03a2b`
(Phase 7C), `e2b5cad` (Phase 7C docs), `c35e1f5` (this session's work).

## 2. Work Completed

### Resolved before anything else

The repository did not match the brief's description. `handoff.md` still
described **Phase 7A**; the whole of Phase 7B was uncommitted (`shrink/` plus
five test files untracked, ten tracked files modified); and the brief's
`1,066 passed, 1 skipped` baseline did not reproduce — the actual figure was
`1008 passed, 59 skipped` with the Docker daemon down. There were no Phase 7B
commit hashes to verify, because no Phase 7B commit existed. Phase 7B was
preserved and committed as `bd7b46c` rather than overwritten or reset.

### Phase 7C independently confirmed present

`src/edgehunter/search/` exists with `models.py`, `actions.py`, `policy.py`,
`state.py`, and `repository.py`, 149 unit tests and 10 real-Docker tests. Every
requirement in the Phase 7C brief is implemented: typed actions, bounded
deterministic enumeration, both policies, pure state, budgeted orchestration,
applicability checks, reproduction before discovery, versioned trace artifacts,
and a matched-budget comparison. Nothing was rebuilt.

### Added in this session

- **A versioned benchmark corpus** (`src/edgehunter/benchmark/`): nine real
  fixture sources, a twelve-case declaration, and a harness that runs the real
  engine against a real container. `edgehunter benchmark` reproduces it.
- **An honest measurement.** The deterministic baseline reproduced 4 of 5
  comparable fixtures; the feedback-guided policy 2, winning none outright. The
  result is reported in the README verbatim and no policy rule was changed
  afterwards.
- **`SECURITY.md`** — a threat model separating the trust zones, and a full
  section on what the container boundary does *not* solve.
- **Two CI workflows** — a fast secret-free static workflow (3.10–3.12 plus
  Windows, and a wheel-install smoke test) and a separate restricted Docker
  workflow that rebuilds the worker image and fails on leftover containers.
- **Packaging verified properly**: sdist and wheel built, wheel installed into a
  clean virtual environment, and `scan`, `run`, and `benchmark` all executed
  from the installed distribution.
- **The README rewritten as a product page**, with every terminal block captured
  from an actual run in this session.
- **Four stray empty directories** removed after verifying each held zero
  entries.

Two real defects in my own new harness were found and fixed by the harness
itself reporting them rather than by inspection: the fixture source path was
joined with the fixture id twice, and `source_file` was initially conflated with
the in-repository target spec.

## 3. Files Changed

Created:

```text
src/edgehunter/benchmark/__init__.py
src/edgehunter/benchmark/models.py
src/edgehunter/benchmark/corpus.py
src/edgehunter/benchmark/harness.py
src/edgehunter/benchmark/fixtures/{anchor_fault,correct_rejection,far_value_fault,
                                   missing_rejection,mixed_types,non_monotonic,
                                   seed_fault,unsupported_shape,wrong_exception}.py
tests/test_benchmark_corpus.py
tests/test_benchmark_integration.py
tests/test_workflows.py
SECURITY.md
.github/workflows/ci.yml
.github/workflows/docker-integration.yml
```

Modified:

```text
src/edgehunter/cli.py        the `benchmark` command and its printer
pyproject.toml               fixture package data, pyyaml dev extra, gate exclusions
README.md                    rewritten as a product page
decisions.md                 D-111..D-120
flow.md                      sections 21-22 and the session changes table
handoff.md                   this file
```

Removed: `t1/`, `t2/`, `t3/`, `t4/` — four empty directories, verified empty
before deletion, untracked by Git and therefore invisible to `git status`.

## 4. Current Architecture / State

Eleven packages under `src/edgehunter/`, each with one responsibility and exactly
one implementation of the shared subsystems:

| Package | Responsibility |
|---|---|
| `scanner` | What exists: structure, annotations, targets |
| `analysis` | What the code cares about: predicates, comparisons, boundaries, risk |
| `oracle` | What it promises: production asserts, guards, test expectations |
| `generation` | What to try: actions, materialization, plans, strategies |
| `execution` | How to run it: staging, typed protocol, hardened Docker |
| `runtime` | What runs inside: the container-only worker |
| `evaluation` | Was it right: applicability, verdicts, findings, reproduction |
| `shrink` | Make it smaller: contract-preserving minimization |
| `search` | Find one: bounded, feedback-guided neighborhood search |
| `benchmark` | Measure it: versioned synthetic corpus and harness |
| `cli` / `pipeline` | Presentation and the single-parse composition root |

Five commands: `scan`, `run`, `shrink`, `explore`, `benchmark`.

The benchmark harness is deliberately not a second engine. It builds a scratch
repository, writes a contract bound to that repository's real SHA-256, and calls
`explore_integer(..., runner=None)` — the same function the CLI calls. The only
substitution anywhere is `open_session` for the one case whose property *is* that
no backend exists.

## 5. Decisions Made

D-111 through D-120, all new in this session:

| Decision | Summary |
|---|---|
| D-111 | The corpus is synthetic, versioned, mixed, and runs the real engine |
| D-112 | A benchmark must be able to report that the tool under test lost |
| D-113 | Cross-policy aggregates cover only fixtures both policies ran |
| D-114 | The engine must not be able to see the benchmark |
| D-115 | The trust model is documented as a threat model, limits included |
| D-116 | CI is split into secret-free static and restricted Docker workflows |
| D-117 | Packaging is verified by installing a built wheel into a clean env |
| D-118 | The public README is a product page; history stays in the engineering docs |
| D-119 | No license is selected; the project is described as having no granted rights |
| D-120 | Stray empty directories and generated artifacts are removed |

No decision was superseded. D-112 is the most important: it is the reason the
corpus contains two fixtures the adaptive policy loses on, and the reason the
integration tests contain no assertion of the form "the adaptive policy wins".

## 6. Requirements and Constraints

- Docker remains the only execution backend; no host fallback was introduced.
- `execution/docker.py` was **not** modified.
- `scan` remains static and never imports `shrink`, `search`, or `benchmark`.
- One runtime dependency: `typer`. `pyyaml` was added to the **dev** extra only,
  to validate the workflow files.
- No LLM, PyTorch, Transformers, Hypothesis, NumPy, SymPy, or Z3.
- All commits authored `PrathamKapoor <prathamkapoor027@gmail.com>`, no trailers.
- `paper/`, `research/`, `*.tex` were never staged.
- No publication action was taken: no repository creation, no push, no tag, no
  release, no PyPI upload, no visibility change.

## 7. Testing and Verification

```text
pytest -q -m "not integration"   → 1198 passed, 1 skipped, 81 deselected  (100.09s)
pytest -q -m integration         →   81 passed, 1199 deselected          (600.62s)
ruff check .                     → All checks passed!
ruff format --check .            → 127 files already formatted
mypy                             → Success: no issues found in 122 source files
git diff --check                 → clean
```

**Combined: 1279 passed, 1 skipped.** The skip is
`tests/test_execution_staging.py:109`, "symlinks are unavailable for this user on
this platform" — a Windows privilege limitation, exercised on Linux CI.

The two halves must be run separately. A monolithic `pytest -q` chains ~1279
tests and ~80 containers back to back on this host and produced one container
timeout per run in earlier sessions — a *different* pre-existing test each time,
passing in isolation. See §8.

Packaging verified from a clean environment:

```text
python -m build --sdist --wheel
  → edgehunter-0.1.0-py3-none-any.whl and edgehunter-0.1.0.tar.gz
wheel contents include: edgehunter/runtime/Dockerfile
                        edgehunter/runtime/worker.py
                        edgehunter/benchmark/fixtures/*.py  (9 files)

fresh venv + pip install dist/*.whl
  → edgehunter --version                edgehunter 0.1.0
  → python -m edgehunter --version      edgehunter 0.1.0
  → edgehunter --help                   five commands listed
  → edgehunter scan --help              works
  → edgehunter run … --allow-execution  executed a real container
  → edgehunter benchmark --allow-execution  ran a real corpus fixture
```

Worker image freshness verified by hash rather than by tag:

```text
local  runtime/worker.py  51a54ff0a1c1b572f3491525ce4052e15b8263de07ea8488b4c4b96445abccd0
image runtime/worker.py  51a54ff0a1c1b572f3491525ce4052e15b8263de07ea8488b4c4b96445abccd0
→ MATCH — the image reflects current source
```

## 8. Known Issues / Risks

1. **No license.** `LICENSE` does not exist. Until the owner selects one,
   EdgeHunter has no granted rights and no open-source license. This is a
   publication blocker. `typer` is MIT, so it is compatible with MIT or
   Apache-2.0 and there is no third-party obstacle — but the choice is the
   owner's and this session did not make it.
2. **The GitHub repository does not exist at the expected path.**
   `https://github.com/PrathamKapoor/EdgeHunter` returns 404. The configured
   remote is `https://github.com/PrathamKapoor/Faultline.git` — a different
   repository name. Local `main` is **7 commits ahead** of `origin/main`. Nothing
   was pushed.
3. **No CI has ever run.** Both workflows are prepared and locally validated
   (the workflow files are parsed and asserted by `tests/test_workflows.py`), but
   no GitHub Actions run has been observed, so no CI status is claimed anywhere.
4. **Monolithic `pytest -q` is flaky on this host.** One container timeout per
   ~10-minute run. Not a regression; the split halves are deterministic.
5. **One Windows test is skipped.** The symlink-refusal rule cannot be exercised
   where symlink creation needs a privilege this account lacks. Linux CI covers
   it once the workflow has run.
6. **The benchmark is small and synthetic.** Twelve fixtures, five of them
   comparable across both policies. It demonstrates the engine behaves as
   documented; it supports no claim about the world.
7. **The feedback policy is measurably the weaker of the two on this corpus.**
   That is a finding, not a defect in the measurement, and it has deliberately
   not been tuned away.

## 9. Unfinished Work

Nothing in this session's scope is unfinished. Deliberately out of scope, in
priority order for whoever picks this up:

- **Owner decisions**: license selection, GitHub repository creation, and
  whether to push, rename `Faultline`, or create a new repository.
- First CI run, and a badge only after one is observed green.
- A larger, permissively licensed benchmark corpus. The current one's main
  limitation is that five comparable fixtures cannot support a statistical claim.
- Multi-type semantic neighbourhoods and richer contract shapes.
- A learned policy behind the existing `SearchPolicy` interface — the interface
  was built for it in D-093, and the benchmark now exists to measure it against.

## 10. Next Subphase

There is no next engineering phase. This was a consolidation session and the
remaining work is **publication and maintenance**, not features.

Recommended order:

1. **Owner selects a license** (MIT or Apache-2.0 are both compatible with the
   single runtime dependency). Add the `LICENSE` file and update the README
   section.
2. **Owner decides the repository destination.** Options, in order of least
   disruption:
   - create `https://github.com/PrathamKapoor/EdgeHunter` and push `main`;
   - rename `Faultline` to `EdgeHunter` if it holds nothing else;
   - keep `Faultline` as the remote and accept the name.
   `BugSynth` must not be touched under any option.
3. **Push and let CI run.** Do not add a status badge until a run is observed.
4. **Then**, if search quality work resumes, start from the benchmark's
   unfavourable result: the neighbourhood policy misses two fixtures a mined
   comparison names directly. That is a concrete, measured improvement target
   for whatever replaces `FeedbackGuidedPolicy`.

## 11. Critical Context

### The remote is not named EdgeHunter

`git remote -v` reports `https://github.com/PrathamKapoor/Faultline.git`. That
is why `github.com/PrathamKapoor/EdgeHunter` 404s. Local `main` is 7 commits
ahead of `origin/main` — the entire Phase 7B and 7C work and this session's work
exist only locally. **Nothing has been published and nothing has been pushed.**

`PrathamKapoor/BugSynth` is a separate repository and was not inspected beyond
confirming it is not EdgeHunter. It must not be modified, renamed, or repurposed.

### The benchmark's negative result is the most important fact here

```text
comparable fixtures: 5
  correct-rejection      neither reproduced        (correct target — required case)
  anchor-fault           both reproduced at probe 1
  interior-value-fault   both reproduced           static 3, feedback 4
  seed-fault             static only               static 2, feedback missed
  far-value-fault        static only               static 3, feedback missed

static_priority  reproduced 4/5, mean probes to first reproduction 2.25
feedback_guided  reproduced 2/5, mean probes to first reproduction 2.50
```

This contradicts the single-fixture result the previous session reported
(feedback 2 probes vs static 3). The broader corpus is the better evidence, and
it is unfavourable to the adaptive policy. It has **not** been tuned away, and
`tests/test_benchmark_integration.py` deliberately contains no
"the adaptive policy wins" assertion, with a comment explaining why.

If a future change makes the feedback policy better, the right response is to
re-run `edgehunter benchmark` and record the new numbers — not to edit a test.

### How the benchmark prevents itself from being rigged

`tests/test_benchmark_corpus.py` scans `search/policy.py`, `search/actions.py`,
`search/state.py`, and `search/repository.py` for any fixture id, fixture source
name, or distinctive seeded trigger value, and asserts no module outside
`cli.py` and the `benchmark` package imports `edgehunter.benchmark`. Triggers of
magnitude 1 and 2 are excluded from the literal scan with the reason written
down: `-1` is the contract's own domain anchor and `-2` its neighbour, so any
honest neighbour rule produces them from arithmetic.

### Anti-flake instructions

- Start Docker Desktop if the daemon is unreachable. It is installed, not running.
- Never `pytest -q` monolithically; use the two `-m` halves.
- After editing `runtime/worker.py`, `docker image rm edgehunter-runtime:1`
  before integration tests.
- Verify worker freshness by hashing the file inside a throwaway container rather
  than trusting the tag. See SECURITY.md §7.6.

### Reusable APIs

Do not reimplement: `shrink.contract.read_contract_file`,
`shrink.repository.preflight_contract`, `IntegerDomain.contains` /
`closest_to_zero`, `shrink.repository.evaluate_against_contract`,
`shrink.signature.signature_of`, `execution.repository.open_session`,
`evaluation.repository.SourceSnapshot.verify`,
`evaluation.evaluator.is_infrastructure_outcome`,
`evaluation.exceptions.match_exception_observed`,
`analysis.models.invert_comparison_operator`,
`generation.materialize.parse_integer_literal`,
`evaluation.repository._safe_run_id`.

## 12. Agent Instructions

- Verify the working tree before trusting any written claim about it. The
  Phase 7B incident happened because a handoff document was believed over
  `git status`.
- Run the suite in two halves: `pytest -q -m "not integration"` then
  `pytest -q -m integration`. A single failure in a monolithic run is not
  evidence of a regression; re-run the test in isolation and check whether the
  change touched that path.
- Never assert that the adaptive search policy wins a benchmark. Report what
  was measured. If it improves, re-run and record; do not edit the test.
- Never add a coverage, probability, confidence, mutation, or severity field.
  If it cannot be counted, omit it.
- Do not change `execution/docker.py` without a verified defect, and document
  and test any such change separately.
- Keep `scan` static. It must not import `shrink`, `search`, or `benchmark`.
- Do not create a `LICENSE` without explicit owner instruction. Record the
  blocker instead.
- Do not create a GitHub repository, push, tag, release, or upload to PyPI
  without explicit authorization. Do not touch `BugSynth`.
- Commit only as `PrathamKapoor <prathamkapoor027@gmail.com>`, with no co-author
  or attribution trailers. Never stage `paper/`, `research/`, or `*.tex`.
- Append a decision to `decisions.md` for every architectural and
  release-engineering choice, update `flow.md` with real function names, and use
  `uncommitted` until a hash exists.