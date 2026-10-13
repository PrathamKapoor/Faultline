# EdgeHunter

**Oracle-aware counterexample search for Python repositories.**

Most tools that "find bugs" in Python either execute untrusted code on your
machine, or they tell you a function is suspicious without ever checking what
the code actually did. EdgeHunter does neither. It reads your repository as data,
runs one selected input at a time inside a hardened container, and reports a
finding **only when an explicit expectation you wrote is contradicted — twice, on
a source snapshot whose fingerprint it can verify.**

```text
Repository ──► static analysis ──► explicit oracle evidence
                                         │
                                         ▼
                        ┌──── oracle applicability check ────┐
                        │  "does this expectation even        │
                        │   govern the input I am about to    │
                        │   run?"                             │
                        └────────────────┬───────────────────┘
                                         ▼
                              Docker-isolated execution
                                         ▼
                              applicable-oracle verdict
                                         ▼
                        reproduced twice? ──► report   else ──► move on
```

---

## Why this exists

A property-based testing library tells you *that* an input failed. It rarely
tells you whether the failure is real, because it has no idea what the code was
supposed to do. A static analyser tells you where to *look*. Neither one closes
the loop.

Closing it requires answering three questions honestly, and most tools get at
least one of them wrong:

1. **Does an expectation actually apply to this input?**
   `assert double(100) == 200` describes *one invocation*. Running `double(10)`
   and comparing the result to `200` is not a test failure — it is a category
   error. EdgeHunter refuses to generalize an exact-input expectation, and it
   says why.

2. **Is the observation itself trustworthy?**
   A target that raises `TypeError` where you expected `ValueError` is a
   different observation from one that raises `ValueError`, and a container that
   timed out observed *nothing at all*. None of the three is "a bug".

3. **Does the finding repeat?**
   A single observation is an anecdote. EdgeHunter re-executes the identical
   request on the same staged snapshot before it will call anything reproduced.

Everything else in this project exists to make those three answers defensible.

---

## What it does

| Capability | Command | Status |
|---|---|---|
| Static repository analysis, never executing code | `scan` | Complete |
| Oracle mining from the repository's own asserts and guards | `scan --properties` | Complete |
| Structural risk ranking (a heuristic, not a probability) | `scan --risk` | Complete |
| Semantic action generation and typed input materialization | `scan --actions --candidates` | Complete |
| Invocation planning and framework-free strategy descriptions | `scan --plans --strategies` | Complete |
| Hardened Docker execution of selected inputs | `run` | Complete |
| Oracle evaluation with exact applicability | `run --evaluate` | Complete |
| Optional bounded re-execution to confirm an observation | `run --recheck N` | Complete |
| Contract-preserving counterexample minimization | `shrink` | Complete |
| Budgeted search that selects its next input from the last verdict | `explore` | Complete |
| Versioned synthetic benchmark corpus | `benchmark` | Complete |

## What it does not do

- It does **not** prove your repository correct, or bound its defect rate. No
  coverage figure, mutation score, or probability is reported, because nothing
  here instruments them.
- It does **not** find bugs in code it cannot execute. Methods needing a
  receiver, async functions, decorated targets, multi-parameter contracts, and
  targets whose signature is unclear are refused, not approximated.
- It does **not** generalize an exact test expectation to another input.
- It does **not** treat an exception as a defect. An exception where you expected
  one is a `MATCH`.
- It does **not** use a language model. The search policies are deterministic
  heuristics. The action interface is shaped so a learned policy could replace
  one, but no model is trained, bundled, or implied.
- It does **not** run target code on your host. There is no fallback and no flag
  to enable one.

---

## Installation

```bash
# from a checkout
pip install -e ".[dev]"

# from a built wheel
python -m build --wheel
pip install dist/edgehunter-0.1.0-py3-none-any.whl
```

Requires Python 3.10+. The only runtime dependency is `typer`. Docker is
required for `run`, `shrink`, `explore`, and `benchmark`; `scan` works without
it.

Verify:

```bash
edgehunter --version     # edgehunter 0.1.0
edgehunter --help
```

---

## Quickstart

```bash
# 1. Look at the repository without running any of it
edgehunter scan . --targets --boundaries --risk

# 2. Inspect what it would propose, still without running anything
edgehunter scan . --actions --candidates --plans

# 3. Run selected inputs in a hardened container
edgehunter run . --target validators.py::validate --allow-execution

# 4. Judge the observations against the repository's own expectations
edgehunter run . --target validators.py::validate --allow-execution --evaluate
```

---

## Core workflows

### Static analysis

`scan` is static end to end: it never imports, executes, or evaluates anything
from the repository under analysis.

```console
$ edgehunter scan sample.py --targets

EdgeHunter scan
  target (file): sample.py

  Python files discovered: 1
  Parsed successfully:     1
  Syntax issues:           0
  Read issues:             0

  Classes:                 0
  Functions:               2
  Methods:                 0
  Async targets:           0
  Callable targets:        2

  Predicates:              4
  Comparisons:             2
  Arithmetic expressions:  1
  Boundary candidates:     6

  Oracle evidence
    Assertions:                   0
    Validation rules:             3
    Resolved test expectations:   0
    Unresolved test expectations: 0
    Ambiguous test expectations:  0
    Property candidates:          3
    Strongly supported targets:   2
    Risk-scored targets:          2

Targets

  sample.py:1
    validate_age(age: int, strict: bool = ...) -> int
  sample.py:12
    label(name: str, active: bool) -> str
```

### Materialization

Declared types produce concrete, bounded values — never fabricated ones.

```console
$ edgehunter scan sample.py --actions

      [annotation]  set_false                          concrete    False
      [annotation]  set_true                           concrete    True
      [annotation]  empty_string                       concrete    ""
      [annotation]  unicode_string                     concrete    "\xe9\u6f22\U0001f642"
      [annotation]  null_character_string              concrete    "\x00"
      [annotation]  long_string                        concrete
                    "aaaa…a... (1026 chars)"
```

An untyped parameter, an unresolved reference, or an element type the analyser
cannot establish yields **no value and a stated reason**. A placeholder is never
invented.

### Execution

`run` is the only path that loads target code, and it loads it in exactly one
place: `runtime/worker.py`, inside a container.

```console
$ edgehunter run sample.py --target sample.py::label --allow-execution --max-cases 1

  Sandbox: docker (ready)
  trusted worker image available
  Oracle evaluation: not performed
  Finding: none (this phase only observes)

  Target: sample.py::label
  Action: label:active:set_false:
    Arguments:
      name = ""  [static_type_baseline]
      active = False  [adversarial_action]
  Outcome: RAISED
  Exception: ValueError
  Message:   name required
  Duration:  0.037s
```

Note `Finding: none (this phase only observes)`. Without `--evaluate`,
EdgeHunter reports what happened and stops. It does not decide whether that was
a bug.

Every invocation runs with no network, a read-only root filesystem, all
capabilities dropped, `no-new-privileges`, bounded processes, memory and CPU, a
non-root user, and a read-only staged workspace. See [SECURITY.md](SECURITY.md).

### Evaluation and finding classification

With `--evaluate`, each observation is judged against the repository's *own*
explicit evidence — and only against evidence that actually applies.

```console
$ edgehunter run sample.py --target sample.py::label --allow-execution --evaluate

EdgeHunter — Oracle Evaluation

  Target: sample.py::label
  Execution: RAISED
  Source snapshot: 7fe56a77083e (verified)
  Invoked: sample.py::label(active=boolean:false, name=str:'')
  Observed: raised ValueError
  Oracle: test assertion
  Expected: no applicable explicit oracle
  Evidence: <no location recorded>
  Applicability: NO_EXPECTATION
  Evaluation: INCONCLUSIVE
  Reproduction: Not rechecked
  Finding: Suspicious Behavior
    id: ehf-f2017923dedf
```

This is the most important behaviour in the project. The target raised an
exception, and EdgeHunter still refuses to call it a defect: no *applicable*
explicit expectation covers this invocation, so the honest verdict is
`INCONCLUSIVE` and the finding is recorded as "suspicious behaviour", which is
not a claim of correctness or of incorrectness.

Six oracle statuses, none a synonym for another:

| Status | Meaning |
|---|---|
| `MATCH` | An applicable oracle was evaluated and the observation satisfied it. |
| `MISMATCH` | An applicable oracle was evaluated and the observation contradicted it. |
| `NOT_APPLICABLE` | The oracle does not cover this target, input, or invocation. |
| `INCONCLUSIVE` | Timeout, import failure, unavailable sandbox, or unverifiable snapshot. |
| `UNSUPPORTED` | The comparison or runtime feature is outside what is supported. |
| `CONFLICTING_EVIDENCE` | Applicable expectations disagree. Neither is chosen. |

Runtime observations are a separate axis and are never rewritten into a verdict:

```text
Execution: RAISED    Oracle: MATCH         ← normal; this is what pytest.raises verifies
Execution: RETURNED  Oracle: MISMATCH      ← normal; the test expected an exception
Execution: TIMEOUT   Oracle: INCONCLUSIVE  ← infrastructure failure, not a violation
```

### Reproduction

```console
$ edgehunter run . --target validators.py::validate --allow-execution --evaluate --recheck 2

  Reproduction
    status:  reproduced
    attempts: 2
    equivalent mismatches: 2
```

An attempt that could not run is a failure, never confirmation. Attempts are
compared by observation identity — outcome kind plus typed value or exception
class — never by message, duration, or container id.

### Shrinking a counterexample

`shrink` minimizes one integer against one **user-declared contract**. A contract
states a condition over a declared integer domain, so unlike an exact test
expectation it keeps applying as the input changes.

```json
{
  "version": 1,
  "contracts": [
    {
      "id": "negative-input-rejected",
      "target": "validators.py::validate",
      "source_sha256": "<the real SHA-256 of validators.py>",
      "parameter": "x",
      "domain": { "operator": "lt", "integer": "0" },
      "expectation": { "kind": "raises_builtin", "exception": "ValueError" }
    }
  ]
}
```

```bash
edgehunter shrink . \
  --target validators.py::validate \
  --contract-file contracts.json \
  --contract-id negative-input-rejected \
  --start-int=-1000000 \
  --allow-execution
```

The contract is bound to a source revision by SHA-256 and is never silently
rebound. A reduced input must preserve the failure's *character*, not merely
contradict the contract: a trial that returns where the original raised `ValueError`
is a different observation and is rejected.

### Deterministic semantic search

`explore` is the first command whose **next input** depends on what the previous
input did.

```console
$ edgehunter explore . --target validators.py::validate \
    --contract-file contracts.json --contract-id negative-input-rejected \
    --start-int=-64 --policy feedback --allow-execution

  Step 1 (probe)  contract_anchor  x = -1
    Origin:   contract_boundary
    Why:      no probe has run yet, so the shared static order applies: x = -1
    Observed: RAISED ValueError
    Oracle:   match  ->  feedback expected_behavior

  Step 2 (probe)  source_boundary_at  x = -2
    Origin:   static_source_boundary
    Why:      the previous probe at x = -1 behaved as the contract expects, so the
              nearest untested domain-valid input (x = -2) is tried next to test
              whether the observed behaviour holds just past that point
    Observed: RETURNED -2
    Oracle:   mismatch  ->  feedback expectation_mismatch

  Step 3 (recheck)  source_boundary_at  x = -2
    Why:      x = -2 contradicted the contract once; re-executing the identical
              request on the same source snapshot before any other input is tried,
              because a single contradiction is not a finding
    Observed: RETURNED -2
    Recheck:  the identical request was re-executed: reproduced: two independent
              executions agreed on returned normally

  Result: violation_reproduced
```

Every proposed input is checked against the contract's declared domain before it
is offered, and again immediately before execution. Two policies share one
candidate universe, one budget, one oracle, one container, and one reproduction
rule:

- `--policy static` follows a fixed documented order and reads **no** runtime
  feedback. It exists so any claim about the other policy has something honest
  to be measured against.
- `--policy feedback` tries the nearest untested in-domain neighbour after a
  probe behaved as expected.

Ten stopping statuses, never collapsed: `violation_reproduced`,
`no_reproduced_violation`, `probe_budget_exhausted`, `execution_budget_exhausted`,
`wall_time_budget_exhausted`, `flaky_observation`, `inconclusive`,
`conflicting_evidence`, `refused`, `sandbox_unavailable`.

`no_reproduced_violation` means *within the probes this bounded search ran*, and
nothing about inputs that were never tried. It is never reported as "the target
is correct".

---

## Benchmark evidence

```console
$ edgehunter benchmark --allow-execution

  Corpus version:   1.0.0
  Cases:            12 (12 as declared, 0 unexpected)

  fixture                 policy            univ  probes  execs   mm  repro  termination
  --------------------------------------------------------------------------------------
  correct-rejection       static_priority     10       8      8    0      -  probe_budget_exhausted
  correct-rejection       feedback_guided     10       8      8    0      -  probe_budget_exhausted
  interior-value-fault    static_priority     10       3      4    2      3  violation_reproduced
  interior-value-fault    feedback_guided     10       4      5    2      4  violation_reproduced
  anchor-fault            static_priority     10       1      2    2      1  violation_reproduced
  anchor-fault            feedback_guided     10       1      2    2      1  violation_reproduced
  seed-fault              static_priority     10       2      3    2      2  violation_reproduced
  seed-fault              feedback_guided     10       8      8    0      -  probe_budget_exhausted
  far-value-fault         static_priority     11       3      4    2      3  violation_reproduced
  far-value-fault         feedback_guided     11       8      8    0      -  probe_budget_exhausted
  non-monotonic           feedback_guided     10       1      2    2      1  violation_reproduced
  wrong-exception         feedback_guided     10       1      2    2      1  violation_reproduced

  Head to head (fixtures every compared policy ran)

    reproduced only by static_priority  2
    reproduced by both                 2
    reproduced by neither              1  (correct-rejection)

    static_priority  reproduced 4/5, mean probes to first reproduction: 2.25
    feedback_guided  reproduced 2/5, mean probes to first reproduction: 2.5
```

### Read that result honestly

**On this corpus the deterministic baseline outperforms the adaptive policy.**
It reproduced 4 of 5 comparable fixtures; the neighbourhood policy reproduced 2.
The neighbourhood policy won no fixture outright.

The two fixtures it misses explain why, and both were put in the corpus
deliberately:

- `seed-fault` — the defect is at the *user-supplied starting value*, which the
  static order tries second. Proximity walks away from it.
- `far-value-fault` — the defect is at `-1000`. Stepping one integer at a time
  cannot reach it inside an eight-probe budget, while a mined comparison names it
  directly.

That is a real negative result about a real heuristic. It has not been "fixed" by
tuning the policy against the corpus, because a benchmark used to tune the thing
it measures measures nothing afterwards. The corpus version, the traces, and the
per-fixture numbers are all reproducible with one command.

### What these numbers are not

- Not a defect rate. The fixtures are synthetic, with behaviour seeded on purpose.
- Not a claim about any real repository.
- Not a coverage figure. Nothing here instruments coverage.
- Not statistical evidence. Five comparable fixtures cannot support a claim about
  the general superiority of either policy.

### Reproducing

```bash
pip install -e ".[dev]"
edgehunter benchmark --allow-execution                      # the table above
edgehunter benchmark --allow-execution --report out.json    # full traces
pytest -m integration -k benchmark                          # the same run, asserted
```

---

## Architecture

```text
src/edgehunter/
├── cli.py            presentation only: parse, dispatch, print
├── pipeline.py       composition root — one read + ast.parse per file
│
├── scanner/          WHAT EXISTS       structure, annotations, targets
├── analysis/         WHAT IT CARES ABOUT  predicates, comparisons, boundaries, risk
├── oracle/           WHAT IT PROMISES  production asserts, guards, test expectations
├── generation/       WHAT TO TRY       actions, materialization, plans, strategies
├── execution/        HOW TO RUN IT     staging, typed protocol, hardened Docker
├── runtime/          WHAT RUNS INSIDE  the container-only worker
├── evaluation/       WAS IT RIGHT      applicability, verdicts, findings, reproduction
├── shrink/           MAKE IT SMALLER   contract-preserving minimization
├── search/           FIND ONE          bounded, feedback-guided neighborhood search
└── benchmark/        MEASURE IT        versioned synthetic corpus and harness
```

One property worth calling out: **there is exactly one executor, one staging
implementation, one oracle adapter, and one failure-signature comparison.**
`run`, `shrink`, and `explore` share all of them. A second copy of any would
eventually disagree with the first, and a disagreement about *whether an oracle
applies* is precisely the bug this project exists to prevent.

---

## Safety and trust model

Full detail in **[SECURITY.md](SECURITY.md)**. The short version:

| Zone | Trust | Runs target code |
|---|---|---|
| Host orchestrator | trusted | No |
| Repository under analysis | untrusted, treated as data | No |
| Staged snapshot | bounded copy: containment checked, symlinks refused, ≤100 files / ≤8 MiB | No |
| Container (worker + target) | untrusted | Yes |
| Evidence artifacts | Git-ignored, excludes captured output | No |

What the container guarantees: no network, read-only root, all capabilities
dropped, `no-new-privileges`, ≤64 processes, ≤256 MiB, 1 CPU, non-root user, a
16 MiB `nosuid,nodev` tmpfs as the only writable path, read-only workspace and
request mounts, and no Docker socket.

What it does **not** guarantee, stated plainly:

- **The target and the worker share a process.** The target can reach the
  worker's objects and can attempt to subvert the observation path. The
  nonce/execution-id echo is a consistency check, not a cryptographic proof.
- **Container escape is out of scope.** Isolation reduces risk; a Docker or
  kernel vulnerability defeats everything above.
- **A timeout proves nothing.** It is reported as `INCONCLUSIVE`, never as a
  verdict.
- **The worker image is cached by tag.** After editing `runtime/worker.py`, run
  `docker image rm edgehunter-runtime:1` or the old worker keeps running.

EdgeHunter does not claim a perfectly secure sandbox. It claims that the
containment is real, the limits are enforced, and the paths that would produce a
confident false report are closed and tested.

---

## Limitations

- **Narrow oracle model.** One user-declared contract covers one integer
  parameter, one inequality, and one expected builtin exception. No OR/AND
  domains, no custom predicates, no solver, no multi-parameter search.
- **Unsupported targets are refused, not approximated.** Methods, async
  functions, decorated targets, and variadic parameters.
- **The neighbourhood is integer-only.** Strings, collections, and floats have
  actions and plans but no search policy.
- **The feedback policy is a weak heuristic.** The benchmark above shows it is
  weaker than a fixed order on the fixtures where a mined comparison names the
  interesting value directly.
- **The benchmark is small and synthetic.** Twelve fixtures. It demonstrates the
  engine behaves as documented; it does not estimate anything about the world.
- **A stale worker image can silently run old code.** See SECURITY.md §7.6.
- **One environment-specific test is skipped on Windows**, because creating a
  symlink there requires a privilege this account does not have. The symlink
  refusal rule is exercised on Linux CI.

---

## Development

```bash
pip install -e ".[dev]"

python -m pytest -q -m "not integration"   # fast; no target execution
python -m pytest -q -m integration         # real Docker containers
ruff check .
ruff format --check .
mypy
```

Run the suite in two halves. A single `pytest -q` chains ~1250 tests and several
hundred containers back to back on a Windows host, which has produced one
container timeout per run — a different pre-existing test each time, passing in
isolation. Splitting the run is deterministic.

## License

**No license file has been chosen yet.** Until the repository owner selects one,
EdgeHunter has no granted rights and no open-source license. See the publication
notes in `handoff.md`.

## Roadmap

- [x] repository discovery, AST extraction, semantic analysis, boundary mining
- [x] oracle mining, semantic actions, materialization, invocation planning
- [x] hardened Docker execution, runtime observation, oracle evaluation
- [x] bounded reproduction, contract-preserving shrinking
- [x] deterministic static search baseline, budgeted feedback-guided search
- [x] versioned synthetic benchmark corpus
- [ ] a larger, permissively licensed benchmark corpus
- [ ] multi-type semantic neighbourhoods
- [ ] richer user-declared contracts (compound domains, custom expectations)
- [ ] a learned policy behind the existing `SearchPolicy` interface