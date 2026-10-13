# EdgeHunter

**Small model. Massive paranoia. Find the cases your tests forgot.**

EdgeHunter is an oracle-aware counterexample search engine for Python, under
active development. The long-term goal is to analyze a Python repository,
infer properties that should hold, generate semantically meaningful edge
cases, execute them in isolation, and report small reproducible
counterexamples when those properties are violated. It is deliberately not
another unit-test generator.

## The problem

Most edge-case bugs are not caused by exotic inputs. They are caused by
assumptions nobody wrote down: a balance that must never increase on a
withdrawal, a collection that must stay sorted, an argument that must be
non-negative. Unit tests cover the happy path and miss the assumptions, and
hand-written edge-case lists miss the interactions between boundaries, data
types, and operation order.

## The long-term idea

An oracle-aware search over the repository:

1. **Static analysis** — AST, types, branches, and relationships.
2. **Property inference** — assertions, tests, docstrings, invariants.
3. **Semantic edge search** — deterministic boundary values plus a search
   policy that proposes constrained actions from a closed vocabulary, such as
   `USE_NAN`, `AT_BOUNDARY`, `JUST_BELOW_BOUNDARY`, or `EMPTY_COLLECTION`,
   rather than free-form generated tests. Strategy *plans* describe how a
   parameter could be generated without committing to a generator library.
4. **Isolated execution** — hypotheses are executed, and an oracle decides
   whether a property holds. A model-generated statement is never proof of a
   bug; execution proves findings.
5. **Counterexample minimization** — shrink a violation to the smallest input
   that still reproduces it, then report it with a reproduction command.

Deterministic logic comes first: ordinary algorithms handle boundaries and
canonical inputs; a model makes semantic decisions, not replacements for
straightforward code.

## Current status

**Early development. Structure, semantics, boundaries, structural risk ranking,
explicit oracle mining, static annotation shapes, safe baseline synthesis,
invocation planning, strategy planning, an isolated Docker execution backend,
runtime oracle evaluation, and bounded single-integer counterexample minimization
are implemented; adaptive search is not.**

What exists today:

- an installable package with a working CLI (`edgehunter --help`,
  `edgehunter --version`)
- safe repository discovery and a single read + `ast.parse` per file feeding
  every later stage
- structural extraction of modules, classes, functions, methods (including
  async and nested definitions), parameters with kinds/annotations/defaults,
  decorators, bases, and source line spans
- semantic extraction of branch predicates (`if`, `while`, `assert`),
  comparisons (including chained comparisons such as `0 < x <= 10`), literal
  constants (with `bool` never treated as numeric), and maximal arithmetic
  expressions with their operators, names, and context
- deterministic boundary candidates: symbolic regions worth testing, such as
  "amount just below/at/just above balance", plus generic numeric regions
  (`0`, `1`, `-1`, and for floats `-0.0`, `NaN`, `±Infinity`)
- an explainable structural risk score per callable target: five capped
  contributions (predicates, arithmetic, boundary groups, input parameters,
  source span), deterministic ranking, and an explicit statement that it is a
  search-priority heuristic rather than a probability of a bug
- oracle mining from statements the repository itself writes: production
  `assert` conditions, conservative `if ...: raise` validation guards, and a
  small subset of existing test expectations (`assert call == value` and
  `with pytest.raises(...)`), each with file/line provenance and a categorical
  strength
- conservative target resolution for test expectations: unique names resolve,
  ambiguous names are reported as ambiguous, receiver calls stay unresolved
- conservative static annotation interpretation of modern syntax: `int`,
  `float`, `bool`, `str`, `list[T]`, `tuple[T, ...]`, `set[T]`, `dict[K, V]`,
  bare containers, the `typing` spellings, `Optional[...]`, and PEP 604 unions
  such as `int | None`
- a closed semantic-action vocabulary (`SemanticActionKind`) where every mined
  boundary candidate maps onto exactly one action, plus annotation-driven
  `SET_NONE`, the three collection shapes, the two boolean values
  (`SET_FALSE`, `SET_TRUE`), and the five string edge shapes
  (`EMPTY_STRING`, `WHITESPACE_STRING`, `UNICODE_STRING`,
  `NULL_CHARACTER_STRING`, `LONG_STRING`)
- deterministic, fixed string values — `""`, `" "`, `"é漢🙂"`, `"\x00"`, and
  `"a" * 1024` — where the long-string length is a module constant that neither
  the target nor the CLI can influence, and booleans that are always written as
  the Python literals `False`/`True` rather than `0`/`1`
- input materialization that writes values down as Python source text:
  `CONCRETE` for literal references (exact integer neighbours such as
  `99`/`100`/`101`, and adjacent floats via `math.nextafter`), `SYMBOLIC` when
  the value depends on a name, and `UNSUPPORTED` when no value exists
- safe baseline synthesis: a deterministic filler value for a recognized
  annotation shape, with its provenance recorded on every value
- typed invocation plans: exactly one adversarial action per plan, every other
  argument either a labelled baseline, an observed literal from an existing
  test, a declared default that is used without being evaluated, or reported as
  missing
- framework-independent strategy plans: fixed, ordered descriptor/value tables
  per parameter, with boundary-derived values labelled by the boundary they came
  from
- structured reporting of syntax errors and unreadable files without aborting
  the scan, plus `--targets`, `--boundaries`, `--risk`, `--properties`,
  `--actions`, `--candidates`, `--annotations`, `--plans`, and `--strategies`
- an isolated execution backend, reached only through `edgehunter run` and only
  with explicit opt-in: eligible Phase 5 plans are assembled into a typed JSON
  request, executed one container per plan inside a hardened Docker sandbox, and
  decoded back into structured observations
- a deterministic eligibility gate that refuses unsupported targets (async
  functions, methods needing a receiver, nested or decorated functions, unsafe
  paths, unsupported values) with a named reason instead of attempting them
- safe source staging that copies only `.py` files, refuses symlinks, enforces
  containment, and applies hard file-count and size limits
- bounded output capture on both sides of the boundary: the worker keeps 16 KiB
  of Python-level output and discards the rest as it is written, and host-side
  Docker output is read through a 64 KiB cap
- runtime oracle evaluation behind `run --evaluate`: each observation is checked
  against the explicit expectations the repository itself records for that exact
  invocation, with a six-value status vocabulary (`MATCH`, `MISMATCH`,
  `NOT_APPLICABLE`, `INCONCLUSIVE`, `UNSUPPORTED`, `CONFLICTING_EVIDENCE`) where
  no status is a synonym for another
- exact applicability: an expectation applies only to the same target with the same
  arguments bound through the target's own signature, so `assert add(0, 0) == 0`
  says nothing about `add(1, 0)` and is never generalized into a contract
- safe result comparison that applies Python's own operator semantics (so
  `True == 1` still holds), supports `==`, `!=`, `<`, `<=`, `>`, `>=`, and
  identity against `None` only, and reports `UNSUPPORTED` rather than `MISMATCH`
  whenever Python itself refuses a comparison
- expected-exception verification that uses the real builtin exception hierarchy and
  the observed class's actual inheritance, so a genuine `class MyValueError(
  ValueError)` satisfies `pytest.raises(ValueError)` the way Python does — while
  custom exception identity stays `INCONCLUSIVE` rather than guessed
- observed exception identity recorded by the sandbox itself: the class's module,
  its qualified name, whether it *is* a real `builtins` exception class, and which
  `builtins` exceptions appear in its real MRO, read without invoking any
  target-defined code
- source-snapshot verification: the SHA-256 of the analyzed bytes is compared with
  the bytes that were staged and executed, and an unverifiable snapshot produces no
  verdict
- optional bounded re-execution (`--recheck N`, hard maximum 5) that reuses the
  same staged snapshot, distinguishes `REPRODUCED` from `FLAKY` and
  `RECHECK_FAILED`, and never treats an environment failure as confirmation
- deterministic, evidence-backed findings that keep every source location and
  report a contradiction as a contradiction — never as a confirmed defect, a
  vulnerability, or a severity

Safety: `scan` analyzes code and never imports or executes it, including tests.
The scanner reads text and parses it; nothing else. Annotations are interpreted
syntactically and never resolved or evaluated — a forward reference such as
`"User"` stays unknown rather than being parsed, and an annotation that is a
call expression is classified unknown and never invoked. Materialized values and
planned argument values are source text produced for a later phase and are never
run here. Regression tests prove module-level side effects in scanned files do
not run, that mined test expectations are not executed, that hostile annotations
are not invoked, and that the package contains no execution primitives.

Execution exists, but it is strictly separated. Target code is imported and
called in exactly one module, `edgehunter.runtime.worker`, and only inside a
container. Every host-side component — scanner, analysis, oracle, generation,
pipeline, and the execution orchestration itself — treats the repository as
data. A test walks the package's own AST and fails if any module other than the
worker gains the ability to execute code, import the import system, or
deserialize an object. There is no fallback: if Docker is missing or unusable,
the report says so and nothing runs.

What does **not** exist yet: risk calibration, property inference from
docstrings or metamorphic relations, executable test generation, counterexample
search, failure shrinking, and any model-based search.
Three things are deliberately *not* claimed here: a boundary candidate
is not a bug, mined oracle evidence is not a defect — it is an explicit
expectation that a later stage can check against an observation — and neither a
candidate case nor an invocation plan is a reproducing input until something
actually runs it. A baseline value is not a validity claim: `0` is the simplest
statically constructible integer, not a "valid" one.

**A recorded outcome is not a finding.** `edgehunter run` reports `RETURNED`,
`RAISED`, `TIMEOUT`, `IMPORT_FAILURE`, `TARGET_NOT_FOUND`, `PROTOCOL_ERROR`, and
`WORKER_FAILURE`. Without `--evaluate` nothing is judged at all: an exception
observed during a run is a fact about that run, not a defect claim, and the report
states this explicitly.

**A mismatch is still not a confirmed bug.** With `--evaluate`, EdgeHunter reports
a contradiction between one explicit, applicable expectation and one observed
behaviour. That is real evidence, and it is the narrowest claim that is true. It
is not a diagnosis: the expectation itself may be outdated, the assertion may
express a precondition rather than a contract, and a runtime observation may be
unstable. Bug severity and impact are not assessed in this phase, and structural
risk scores are search-priority heuristics rather than severity (see
[Current status](#current-status)).

### Known limitation: annotation interpretation is syntactic

Static annotation interpretation recognizes shapes, not meanings. `List[int]` is
recognized because of the *syntax* of that subscript, not because EdgeHunter has
proved that the name `List` refers to `typing.List`. A user alias such as
`MyInt = int` therefore stays unknown, as does any project-specific or imported
type. Forward references (`"User"`) are kept as text and never parsed, and
`Annotated`, `Literal`, `NewType`, `TypeVar`, `Protocol`, `Callable`,
`Sequence`, `Mapping`, `Iterable`, and `frozenset` are not supported. This is a
deliberate conservative trade: reporting fewer known types is preferable to
inventing a value for a type EdgeHunter did not actually recognize.

A second consequence is visible in a real scan: a large majority of parameters in
an ordinary codebase stay unknown, because most are annotated with project types
or subscripted generics of them. Unknown means "no baseline, no plan", not
"broken".

### Known limitation: the sandbox is a strong default, not a proof

Execution requires a working Docker installation with a **Linux** backend; a
Windows or macOS Docker daemon that reports a non-Linux server is refused, and
`edgehunter run` reports `unavailable` without executing anything. The container
runs with no network, a read-only root filesystem, all capabilities dropped,
`no-new-privileges`, a non-root user, PID, memory, and CPU limits, a small
`/tmp`, and the staged source mounted read-only. Docker's default seccomp
profile is retained.

What this does **not** claim:

- A Docker sandbox reduces risk; it does not eliminate it. Container escapes have
  existed, and a kernel or Docker vulnerability is outside EdgeHunter's control.
- The staged copy is a snapshot. Files the target creates at run time vanish with
  the container and are not reported back to the host.
- Targets needing third-party packages are refused rather than installed. Nothing
  is installed into the sandbox or the host.
- Only synchronous, undecorated, top-level functions whose arguments all have
  supported values are eligible. Everything else is refused with a stated reason.
- A timeout kills the container; it does not explain what the target was doing.
- Captured output is bounded and truncated, so it is a sample, not a transcript.
  Native writes to file descriptors 1 and 2 are discarded entirely.
- A `RAISED` outcome is an observation. Without `--evaluate` nothing is compared
  against any oracle, so it cannot and does not report a bug.

### Known limitation: worker output is an observation, not cryptographic proof

Phase 6B adds a nonce-checked result document, which proves the document belongs to
one request. It does **not** prove a deliberately hostile target could not have
influenced the worker process that produced it: target code runs inside that
process, so a determined target could in principle interfere with process state.
The isolation model reduces this risk; it does not eliminate it. Strengthening
worker/target integrity is a separate future security task and is not addressed
here.

### Known limitation: observed exception identity is real, not absolute

When a target raises, the worker records more than a bare class name: the class's
module, its qualified name, whether it **is** one of the real `builtins` exception
classes, and which `builtins` exception classes appear in its real MRO. That is
read from the class object itself inside the container, using
`type.__getattribute__` so a custom metaclass never runs on the observation path.

This is enough to tell the real `ValueError` apart from a target's own
`class ValueError(Exception)`, and to let `class MyValueError(ValueError)` satisfy
`pytest.raises(ValueError)` exactly as Python's own semantics do. It is **not**
enough to resolve a custom expected class: `pytest.raises(app.errors.
InvalidInput)` stays `INCONCLUSIVE` even when the observed qualified name is
character-for-character the same, because the test module's own binding of that
name is unknown and resolving it would require importing target code on the host.

So the limitation from Phase 6B still stands, unchanged in substance: custom
exception identity is not resolved, only *observed*. Richer observation narrows
what is unknown; it never manufactures a verdict.

A target is not prevented from lying about its own class's `__module__` or
`__qualname__`, and neither is it prevented from influencing the worker process —
that is the worker-trust limitation above, and this change does not weaken it.

## Installation

Requires Python 3.10 or newer.

```bash
git clone https://github.com/PrathamKapoor/Faultline.git
cd Faultline
python -m venv .venv
# Windows:
.venv\Scripts\activate
# POSIX:
source .venv/bin/activate
pip install -e ".[dev]"
```

`edgehunter scan` needs nothing beyond this. `edgehunter run` additionally needs
Docker with a Linux backend; the first run builds a small trusted worker image
from EdgeHunter's own packaged sources.

## Usage

```console
$ edgehunter --version
edgehunter 0.1.0

$ edgehunter scan .
EdgeHunter scan
  target (directory): /path/to/your/repository

  Python files discovered: 49
  Parsed successfully:     49
  Syntax issues:           0
  Read issues:             0

  Classes:                 80
  Functions:               522
  Methods:                 85
  Async targets:           0
  Callable targets:        607
  Excluded directories:    12

  Predicates:              1314
  Comparisons:             1183
  Arithmetic expressions:  182
  Boundary candidates:     30

  Oracle evidence
    Assertions:                   0
    Validation rules:             3
    Resolved test expectations:   83
    Unresolved test expectations: 38
    Ambiguous test expectations:  5
    Property candidates:          129
    Strongly supported targets:   16
    Risk-scored targets:          607

  Semantic actions
    Actions proposed:          187
    Concrete values:           113
    Symbolic values:           7
    Unsupported actions:       67
    Targets with actions:      65
    Candidate cases:           120
    Complete cases:            17
    Partial cases:             96
    Symbolic cases:            7

  Planning
    Parameters recognized:    136
    Parameters unknown:       622
    Invocation plans:         120
    Complete function plans:  38
    Needs receiver:           1
    Partial plans:            74
    Symbolic plans:           7
    Strategy plans:           62
```

`scan --boundaries` lists boundary candidates per target. Real output for a
small example project (`bank.py` containing `Account.withdraw`,
`discount`, and `clamp`), excerpted:

```console
$ edgehunter scan ./example --boundaries
...
Boundaries

  bank.py:5
    Account.withdraw(self, amount: float) -> float

    amount
      [comparison] just below self.balance
      [comparison] at self.balance
      [comparison] just above self.balance
      [numeric] 0
      [numeric] 1
      [numeric] -1
      [numeric] -0.0
      [numeric] NaN
      [numeric] +Infinity
      [numeric] -Infinity
  bank.py:12
    discount(total: float) -> float

    total
      [comparison] just below 100
      [comparison] at 100
      [comparison] just above 100
      [numeric] 0
      ...
  bank.py:18
    clamp(value: int) -> int

    value
      [comparison] just below 0
      [comparison] just below 10
      [comparison] at 0
      [comparison] at 10
      [comparison] just above 0
      [comparison] just above 10
      [numeric] 0
      [numeric] 1
      [numeric] -1
```

Two things to note in that output. Boundaries are **symbolic**: `amount` is
"at `self.balance`", never `self.balance - 1`, because the correct step depends
on a type that this phase deliberately does not resolve. And scanning
EdgeHunter's own source yields few boundary candidates because almost nothing is
annotated exactly `int` or `float`; generic numeric candidates require that
literal annotation.

`--risk` ranks targets by the structural score and always shows why. Real
output for a small example project:

```console
$ edgehunter scan ./example --risk
...
Structural risk
  Search-priority heuristic, not a probability of a bug.

   40  account.py:5
      Account.withdraw(self, amount: float) -> float
      predicates         +18  3 predicate(s), +6 each (cap 30)
      arithmetic         +4   1 arithmetic expression(s), +4 each (cap 20)
      boundary_groups    +12  3 boundary group(s), +4 each (cap 20)
      input_parameters   +3   1 input parameter(s), +3 each (cap 15)
      source_span        +3   11 line(s) span, 1 block(s) of 20, +3 each (cap 15)

   24  account.py:18
      discount(total: float) -> float
      ...
```

`--properties` lists mined oracle evidence with provenance, including evidence
whose target could not be resolved:

```console
$ edgehunter scan ./example --properties
...
Properties

  Mined evidence, not confirmed defects.

  Production

    [STRONG] rejection_rule  Account.withdraw
      evidence: account.py:6
      when:      amount < 0
      expected:  raises ValueError

    [STRONG] asserted_condition  Account.withdraw
      evidence: account.py:9
      condition: self.balance >= 0

  Existing test expectations

    [STRONG] example_raises  [unresolved]
      evidence: test_account.py:7
      expect:   Account(100).withdraw(-1) raises ValueError
      callee:   Account(100).withdraw
      target:   unresolved
      reason:   receiver resolution not implemented

    [STRONG] example_expectation  [resolved]
      evidence: test_account.py:16
      expect:   discount(99) == 99
      callee:   discount
      target:   discount
      reason:   unique target with this name
```

`--actions` turns each target's boundary candidates and annotations into
concrete, symbolic, or unsupported inputs. Real output for a small example
project:

```console
$ edgehunter scan ./example --actions
...
Actions

  Constrained input vocabulary. Nothing here is executed.

  account.py:5
    Account.withdraw(self, amount: float) -> float

    amount
      [boundary] just_below_boundary 0              concrete    -1
      [boundary] just_below_boundary self.balance   symbolic    <self.balance>
        reference 'self.balance' is a name, so no neighbour of it can be written down without knowing its runtime value
      [boundary] at_boundary 0                      concrete    0
      [boundary] at_boundary self.balance           symbolic    <self.balance>
        reference 'self.balance' is a name, so its value is not known statically
      [boundary] just_above_boundary 0              concrete    1
      [boundary] set_zero                           concrete    0.0
      [boundary] set_negative_zero                  concrete    -0.0
      [boundary] use_nan                            concrete    float("nan")
      ...
  account.py:20
    clamp(value: int) -> int

    value
      [boundary] just_below_boundary 0              concrete    -1
      [boundary] just_below_boundary 10             concrete    9
      [boundary] at_boundary 10                     concrete    10
      [boundary] just_above_boundary 10             concrete    11
      [boundary] set_zero                           concrete    0
      ...
  account.py:28
    dedupe(seen: list, values: dict) -> list

    seen
      [annotation] empty_collection                   concrete    []
      [annotation] singleton_collection               concrete    [None]
    values
      [annotation] empty_collection                   concrete    {}
      [annotation] singleton_collection               concrete    {None: None}
```

Three things to note. `[boundary]` actions come from a comparison in the source,
while `[annotation]` actions come only from the parameter's declared type.
`concrete` values are Python source text for a later phase to use; `symbolic`
values are names whose runtime value is unknown, printed in angle brackets so
they cannot be mistaken for literals. And `math.nextafter` is used for float
neighbours, so `clamp` gets `9` and `11` for an integer boundary while a float
boundary such as `0.5` would get `0.49999999999999994` — the genuinely adjacent
representable value.

`--candidates` turns each action into a single-action case and states plainly
what it does *not* supply:

```console
$ edgehunter scan ./example --candidates
...
Candidates

  One action per case. Values for other parameters are never invented.

  account.py:2
    Account.__init__(self, balance: float) -> None

      [complete] balance = 0.0                set_zero
      [complete] balance = float("nan")       use_nan
      ...

  account.py:28
    dedupe(seen: list, values: dict) -> list

      [symbolic] seen = <values>              at_boundary values
        missing: values
      [partial ] seen = []                    empty_collection
        missing: values
      [partial ] seen = [None]                singleton_collection
        missing: values
      [partial ] values = {}                  empty_collection
        missing: seen
```

`complete` means the action materialized concretely and every required parameter
is covered. `partial` means the value is concrete but other required parameters
are not — filling those in would mean inventing a baseline input, so EdgeHunter
names them under `missing:` instead. `symbolic` means the value still depends on
a name.

`--annotations` shows every parameter's rendered annotation beside the shape
EdgeHunter interpreted it as. This is the cheapest way to understand why a later
plan has the shape it has:

```console
$ edgehunter scan ./example --annotations
...
Annotations

  Static annotation interpretation is syntactic, not type resolution.

  module_under_test.py:6  process
    count: int -> INT
    ratio: float -> FLOAT
    name: str -> STR
    tags: list[int] -> LIST[INT]
    user: User -> UNKNOWN
    limit: int | None -> OPTIONAL[INT]
```

`--plans` lifts each candidate case into a complete argument list, labelling
every value with where it came from:

```console
$ edgehunter scan ./example --plans
...
Invocation plans

  One adversarial action per plan. Filler values record their origin.

  module_under_test.py:6  process(count: int, ratio: float, name: str, tags: list[int], limit: int | None = ...)

    count <- set_zero
      count = 0  [adversarial_action, positional]
      ratio = 0.0  [static_type_baseline, positional]
      name = ""  [static_type_baseline, positional]
      tags = []  [static_type_baseline, positional]
      limit = (declared default)  [declared_default, omitted]
      status: complete_function
```

One assignment per plan is adversarial; the rest are fillers. That distinction is
the point of the section: it is what lets a later failure be attributed to the
`0` in `count` rather than to the `""` in `name`. When an argument cannot be
filled, the plan says so rather than guessing:

```console
    count <- set_zero
      count = 0  [adversarial_action, positional]
      missing: user
      status: partial_arguments
```

`--strategies` prints the deterministic description of how each parameter could
be generated. It never prints a sampled value:

```console
$ edgehunter scan ./example --strategies
...
Strategy plans

  Deterministic descriptions, not generated samples.

  module_under_test.py:6  process(count: int, ratio: float, name: str, tags: list[int], limit: int | None = ...)

    count: INT  [sampled_from]
      zero                         0
      one                          1
      negative_one                 -1
      just_below.10                9
      at_boundary.10               10
      just_above.10                11

    ratio: FLOAT  [sampled_from]
      zero                         0.0
      negative_zero                -0.0
      nan                          float("nan")
      positive_infinity            float("inf")
      negative_infinity            float("-inf")
      just_below.1.0               0.9999999999999999
      just_above.1.0               1.0000000000000002

    name: STR  [sampled_from]
      empty                        ""
      space                        " "
      unicode                      "é"
      nul                          "\x00"
      long                         "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

    tags: LIST[INT]  [sampled_from]
      empty                        []
      singleton                    [0]
      repeated_element             [0, 0]

    user: UNKNOWN  [unsupported]

    limit: OPTIONAL[INT]  [sampled_from]
      zero                         0
      one                          1
      negative_one                 -1
      none                         None
```

Note `user: UNKNOWN [unsupported]`: an unrecognized type yields no generation
plan at all. And note `just_below.1.0` — the float neighbours are the genuinely
adjacent representable values, labelled with the boundary they came from. A
`list[CustomType]` yields only `empty`: never a fabricated `[None]`.

Note also that this is a *description*, not a sample. Nothing here is generated at
scan time, and no value is ever passed to a target function, because `scan` does
not execute anything.

Exit codes:

```text
0  scan completed and every discovered Python file parsed
1  scan completed but one or more files could not be read or parsed
2  invalid input, such as a missing path or an unsupported file target
```

Invalid input fails cleanly with no traceback:

```console
$ edgehunter scan ./does-not-exist
error: path does not exist: does-not-exist
```

When a file cannot be parsed, the scan reports it and continues with the rest:

```console
$ edgehunter scan ./example
...
Syntax issues

  broken.py:1:11
    '(' was never closed
```

### Executing a target

`scan` never executes. Execution is a separate, explicit command:

```console
$ edgehunter run ./example --target divide --allow-execution
```

Both a target and `--allow-execution` are required. Without the flag nothing is
read and nothing runs:

```console
$ edgehunter run ./example --target divide
error: execution requires explicit authorization; pass --allow-execution
```

If a name is defined in more than one file, EdgeHunter refuses to guess:

```console
$ edgehunter run ./example --target process --allow-execution
error: 'process' is defined in more than one file (a.py, b.py); select it with --target 'path::process'
```

A run reports what happened:

```console
$ edgehunter run ./example --target divide --allow-execution --max-cases 3
EdgeHunter execution

  Sandbox: docker (ready)
  trusted worker image available
  Oracle evaluation: not performed
  Finding: none (this phase only observes)

  Target: math_ops.py::divide
  Action: divide:a:set_negative_one:
    Arguments:
      a = -1.0  [adversarial_action]
      b = 0.0  [static_type_baseline]
  Outcome: RAISED
  Exception: ZeroDivisionError
  Message:   float division by zero
  Duration:  0.043s
```

`--max-cases` (default 5, hard maximum 20) bounds how many eligible candidates are
run for the selected target, and `--timeout` (default 3 s, clamped to 0.5–60 s) is
the per-invocation wall-clock budget. A target that exceeds it is killed and
reported as `TIMEOUT`.

Exit codes for `run`:

```text
0  at least one invocation ran and was observed
2  invalid input, such as an unknown or ambiguous target
3  refused or unavailable: no opt-in, no eligible plan, or no usable Docker backend
```

Exit code `1` is deliberately unused by `run`, so a script can tell "the scan
found something" from "the run did not happen". Oracle evaluation does not change
these codes: a contradiction is reported in the output, never as an exit status.

### Evaluating an observation against the repository's own expectations

`--evaluate` compares each observation against the explicit expectations the
repository itself records for that *exact* invocation:

```console
$ edgehunter run ./example --target 'calculator.py::add' --allow-execution --evaluate --max-cases 10
EdgeHunter execution

  Sandbox: docker (ready)
  trusted worker image available
  Oracle evaluation: requested; reported below
  Finding: reported by the oracle evaluation below

  Target: calculator.py::add
  Action: add:a:set_zero:
    Arguments:
      a = 0  [adversarial_action]
      b = 0  [existing_test_baseline]
  Outcome: RETURNED
  Returned:  1
  Duration:  0.040s

EdgeHunter — Oracle Evaluation

  Target: calculator.py::add
  Execution: RETURNED
  Source snapshot: 9494df6065f3 (verified)
  Invoked: calculator.py::add(a=integer:0, b=integer:0)
  Observed: 1 (integer)
  Oracle: test assertion
  Expected: add(0, 0) == 0
  Evidence: test_calculator.py:5
  Applicability: EXACT_MATCH
  Evaluation: MISMATCH
  Reproduction: Not rechecked
  Finding: Observed Expectation Mismatch
    id: ehf-1ef04beafc33
    oracle: test_calculator.py:5
    expected: add(0, 0) == 0
    observed: 1 (integer)
    action: add:a:set_zero:
      a=0 [adversarial_action]
      b=0 [existing_test_baseline]
    reproduce: edgehunter run . --target 'calculator.py::add' --allow-execution --evaluate --recheck 3
    detail: expected add(0, 0) == 0; observed integer:1

  Bug severity: not assessed
  (an expectation mismatch is evidence of disagreement with one explicit
   expectation; it is not by itself a confirmed software defect)
```

Given this deliberately wrong target:

```python
# calculator.py
def add(a: int, b: int) -> int:
    return a + b + 1
```

```python
# test_calculator.py
def test_add():
    assert add(0, 0) == 0
```

only `add(0, 0)` is contradicted. Every other candidate is `NOT_APPLICABLE`:

```console
  Applicability: ARGUMENTS_DIFFER
  Evaluation: NOT_APPLICABLE
  Finding: None
```

**An expectation applies to one recorded example and nothing else.**
`assert add(1, 2) == 3` is never treated as `add(a, b) == a + b`, and
`assert square(2) == 4` implies nothing about `square(3)`.

An expected exception is verified the same way, and a raise that *satisfies* the
test produces no finding at all:

```console
  Target: calculator.py::validate
  Execution: RAISED
  Invoked: calculator.py::validate(x=integer:-1)
  Observed: raised ValueError
  Oracle: test raises
  Expected: validate(-1) raises ValueError
  Evidence: test_validate.py:12
  Applicability: EXACT_MATCH
  Evaluation: MATCH
  Finding: None
```

`Execution: RAISED` with `Evaluation: MATCH` is a normal, expected outcome. An
exception is only ever a finding when it contradicts an applicable expectation.

Executing an interesting case without knowing whether the behaviour is correct is
also a supported result:

```console
  Target: math_ops.py::divide
  Execution: RAISED
  Applicable oracle: none
  Evaluation: INCONCLUSIVE
  Finding: None
```

Two tests disagreeing about one invocation is a fact about the repository, not
about the code:

```console
  Evaluation: CONFLICTING_EVIDENCE
  Finding: Inconclusive
```

#### Oracle statuses

| Status | Meaning |
|---|---|
| `MATCH` | An applicable oracle was evaluated and the observation satisfied it. |
| `MISMATCH` | An applicable oracle was evaluated and the observation contradicted it. |
| `NOT_APPLICABLE` | The oracle does not cover this target, input, or invocation. |
| `INCONCLUSIVE` | Not enough evidence: a timeout, an import failure, an unavailable sandbox, an ambiguous expectation, or an unverified source snapshot. |
| `UNSUPPORTED` | The comparison or runtime feature is outside what this phase supports. Never reported as a mismatch. |
| `CONFLICTING_EVIDENCE` | Applicable expectations disagree with each other. Both locations are retained and no defect is claimed. |

#### Reproducing a contradiction

`--recheck N` re-executes each candidate up to `N` extra times (hard maximum 5),
inside the same Docker backend, against the same staged source snapshot. Nothing
retries automatically and no flag combination bypasses `--allow-execution`:

```console
  Evaluation: MISMATCH
  Reproduction: 3 / 3 equivalent mismatches
  Finding: Reproduced Expectation Mismatch
```

Attempts that disagree are reported as flakiness rather than promoted to a stable
bug, and an attempt that could not run is never counted as confirmation:

```console
  Reproduction: FLAKY (2 of 3 attempts agreed; the behaviour is not stable
  and is not reported as a defect)

  Reproduction: RECHECK_FAILED (1 of 2 attempts could not be evaluated;
  an environment failure is not confirmation)
```

Two observations are compared on outcome kind and typed value (or exception class),
never on duration, execution id, container id, or temporary path. `NaN` and
`-0.0` get stable canonical descriptors so deduplication stays predictable.

#### What is deliberately not inferred

- **Production assertions are not universal contracts.** `assert balance >= 0` in
  a public function states a precondition. A raised `AssertionError` is recorded
  as `ASSERTION_OBSERVED` and asks for review; it is never reported as a
  confirmed defect.
- **Validation guard reachability is not inferred.** `if amount < 0: raise` exists
  in the source, which says nothing about whether that branch ran for this input.
  Guards are retained as explanatory context and never evaluated.
- **Returning normally proves nothing about assertions inside the function.**
- **Ambiguous or unresolved test callees are never attached to a target.**
- **Non-literal test arguments are never evaluated.** A fixture, a constant, or a
  constructor call makes exact matching impossible, and EdgeHunter does not run the
  test to find out.
- **Omitted defaults are not evaluated.** `f(1)` is not assumed equivalent to
  `f(1, 10)` even where the signature makes the value knowable in principle.
- **Identity comparisons are limited to `None`.** `x is y` between arbitrary values
  is not something the evaluator can observe, and `==` is not a substitute.
- **Custom exception identity is not guessed.** A bare `PaymentError` in a test and
  a bare `PaymentError` at runtime are reported `INCONCLUSIVE`, because two modules
  may define the same class name. This still holds when the runtime supplies a
  fully qualified name that happens to match the expectation's text: the
  expectation's class is resolved by the test module's own imports, which are not
  resolved.
- **A bare builtin name is only evidence when the test module does not shadow it.**
  A test file that declares `class ValueError(Exception)` and then writes
  `pytest.raises(ValueError)` means its own class, so that expectation is
  `INCONCLUSIVE` rather than a match. Only module-scope bindings are detected; a
  function-local import of the same name beside a `pytest.raises` is a documented
  gap, not a resolved case.
- **A name difference is never a mismatch by itself.** A `MISMATCH` for an exception
  requires a real inheritance fact — either both classes are standard-library
  exceptions, or the sandbox walked the raised class's MRO and the expected builtin
  is provably absent from it.
- **Custom return objects are not compared.** When the protocol cannot represent a
  return value, the result is `UNSUPPORTED`, never `MISMATCH`.
- **An observation with no identity evidence decides nothing.** A result document
  that records only an exception's bare name cannot establish whether it is a
  standard-library class, so the expected exception verdict is `INCONCLUSIVE` —
  in *both* directions. A name-only `MATCH` would hide a real mismatch (a target
  may define its own `class ValueError(Exception)`), and a name-only `MISMATCH`
  would hide a real match (a target's own `class KeyError(ValueError)` satisfies
  `pytest.raises(ValueError)`). Such documents still decode; they simply support no
  exception verdict.
- **No counterexample is generalized or minimized beyond what the evidence covers.**
  An exact test expectation is never applied to a different input, a production
  guard is never promoted to a reusable contract, and a contract is never rebound
  to changed source. A successful reduction preserves the target, the source
  snapshot, the contract, the verdict, the failure character, and a reproduced
  result — and is reported as the smallest input a bounded search verified, never
  as a proven minimum.
- **An unverified source snapshot yields no verdict.** Oracle evidence describes a
  specific revision; if the executed bytes cannot be proven to be those bytes, the
  status is `INCONCLUSIVE`.
- **No counterexample is minimised.** There is no shrinker, no adaptive search, and
  no learned policy. An exact expectation applies to one exact input: `assert
  f(100) == 200` says nothing about `f(10)`, however much smaller `10` is.

#### What this does not claim

EdgeHunter does not attempt complete Python correctness verification. It does not
treat every exception as a bug, infer general semantic properties, turn production
assertions into universal contracts, support arbitrary test frameworks, or
eliminate all false positives. A mismatch is evidence that one explicit,
applicable expectation and one observation disagree — nothing more. Bug severity
and impact assessment are not performed in this phase.

It also does not claim support for arbitrary type constructors, complex fixtures,
custom object serialization, general Python import resolution, all possible Python
exception relationships, or automatic bug detection from every exception.

## Shrinking one integer counterexample

`edgehunter scan` is static and `edgehunter run` observes. A third command,
`edgehunter shrink`, minimizes **one** integer input against **one**
user-declared contract.

### Why an exact test expectation cannot be shrunk

```python
def double(x: int) -> int:
    return x * 2 + 1


# the repository's test
assert double(100) == 200
```

Execution shows `double(100) → 201`, a genuine `MISMATCH` against that
expectation. But the expectation describes **one** invocation. It says nothing
about `double(10)`, so a shrinker that tried `double(10)` and compared the result
with `200` would be reporting a verdict against an oracle that does not apply.
EdgeHunter refuses instead — and refuses *before* executing anything, with no
flag that bypasses it.

### The difference between an expectation and a contract

| | Existing test expectation | User-declared contract |
|---|---|---|
| Source | mined from `assert` / `pytest.raises` in the repository | a JSON file you name explicitly |
| Applies to | one exact recorded invocation | every input inside a declared domain |
| Proven? | it is the repository's own claim | **no** — it is a claim you assert |
| Can it authorize shrinking? | **no** | yes |

A contract is an **expectation the user asserts, not a fact proven from the code**.
Nothing is inferred: a production `if x < 0: raise ValueError` is not converted
into a contract, and an `assert f(1) == 2` is never turned into a formula.

### The contract format

One contract family only, `version` exactly `1`:

```json
{
  "version": 1,
  "contracts": [
    {
      "id": "negative-age-rejection",
      "target": "validators.py::validate_age",
      "source_sha256": "<64 lowercase hex characters>",
      "parameter": "age",
      "domain": { "operator": "lt", "integer": "0" },
      "expectation": { "kind": "raises_builtin", "exception": "ValueError" }
    }
  ]
}
```

`operator` is one of `lt`, `le`, `gt`, `ge`; `integer` is a decimal **string**
bounded to signed 64-bit, so a JSON boolean, a float, or an arbitrary-precision
number cannot pass; `exception` must be a real class from `builtins`, checked by
identity rather than by spelling. Unknown keys, duplicate JSON keys, duplicate
ids, missing fields, an unsupported version, an oversized file, and any
`../`/absolute/Windows-drive path are all refused during parsing — before a
container could start. `source_sha256` binds the contract to one revision of the
target file: if the file has changed since the contract was written, the operation
is `REFUSED` and the contract is never silently rebound.

There is deliberately no general property language: no arbitrary predicates, no
Python expressions, no `AND`/`OR` trees, no custom comparators, no string/list/
float or multi-parameter shrinking.

### A tested example

With `validators.py` containing `def validate_age(age: int) -> int: return age`
and the contract above:

```console
$ edgehunter shrink ./example --target validators.py::validate_age \
    --contract-file ./example/contracts.json \
    --contract-id negative-age-rejection \
    --start-int=-100 --allow-execution
EdgeHunter shrink

  Bounded minimization of one integer against one user-declared contract.
  A contract is an expectation the user asserts, not a proven fact.

  Target:          validators.py::validate_age
  Contract:        negative-age-rejection
  Oracle source:   user_declared_contract
  Parameter:       age
  Seed:            age = -100 [user_supplied_seed]
  Source snapshot: verified

  Shrink proposals

    age = -1       abs=1        accepted
        abs(-1) is smaller than abs(-100) and the same failure returned normally reproduced 2/2 times against the same contract
        failure signature: returned normally
    age = -51      abs=51       not_simpler
        abs(-51) is not smaller than the current best abs(-1)
    ...

  Proposals executed: 1
  Proposal list exhausted: yes
  Containers used:   4

  Reduced: age = -1
  Failure signature:  returned normally

  Result: shrunk
  Reason: reduced age from -100 to -1 (abs 1), every accepted value verified against the same contract on the same source snapshot

  Meaning:
    Reproduced mismatch against the same user-declared contract, on one
    source snapshot, within a bounded deterministic search.
    Not a universal proof of a software defect, and not a statement that
    this input is the smallest possible counterexample.
```

That output is the real result of the real command against a real Docker
daemon: four containers, the seed reproduced twice, one accepted reduction, and
seven proposals rejected without spending a container.

### Requirements before anything runs

1. an explicitly selected contract file and contract id;
2. `--allow-execution` (a contract never authorizes execution by existing);
3. the contract's `source_sha256` matching the analyzed file;
4. a top-level synchronous function whose only parameter is the contract's,
   annotated `int`, with no default, no decorators, and no receiver;
5. a seed inside the declared domain;
6. no conflicting exact-input expectation from the repository.

Only then does the seed run — **twice** — and both attempts must contradict the
contract with one agreed failure. A single mismatch is not enough, because a flaky
input is not a counterexample.

### A smaller input must preserve the failure

If the original call *returned* where a `ValueError` was expected, then a trial
that raises `TypeError` also contradicts the contract — but it is a different
observed failure. EdgeHunter rejects it. The signature is the outcome category
plus the observed exception's qualified identity; messages, durations, execution
ids, and container ids are never part of it, and a timeout has no signature at
all.

### Budgets and wording

| Bound | Default | Hard cap |
|---|---|---|
| shrink proposals | 8 | 20 |
| total containers | 32 | 64 |
| attempts per verified value | 2 | 3 |

The search is bounded and deterministic: the domain's closest-to-zero value is
proposed first, then integer midpoints walking from the seed toward it. It is a
heuristic, not an exhaustive proof, and it never assumes the target's behaviour is
monotone.

A result says *reduced counterexample against a user-declared contract* and
*smallest verified input found during this bounded search*. It never says
*globally minimal counterexample*, *automatically proved bug*, or *critical
vulnerability*, and there is no severity and no confidence score.

With `--artifact-id`, a versioned JSON record is written to
`.edgehunter/runs/<id>/shrink.json` (Git-ignored) containing the target identity,
the source fingerprint, the contract id and file fingerprint, the user-declared
provenance, the seed and its origin, the reduced value, the budget, and every
attempt with its rejection reason. Captured target output and exception messages
are deliberately excluded.

### Not supported in this version

Only **one integer parameter**, **one declared inequality**, **one expected
builtin exception**, **one source snapshot**, and **one failure signature**.
Not: shrinking existing-test expectations, custom objects, strings, lists,
dictionaries, floats, multiple parameters, stateful sequences, symbolic
constraints, framework fixtures, or custom-exception expectation resolution. A
correct narrow shrinker is preferable to a broad unsound one.

## Architecture

Only the components below exist today:

```text
src/edgehunter/
├── __init__.py        package metadata (version)
├── __main__.py        `python -m edgehunter` entry point
├── cli.py             Typer application: argument parsing and output only
├── pipeline.py        composition root: one read + parse per file
├── scanner/           structural layer: what exists
│   ├── __init__.py    public interface
│   ├── repository.py  discovery + RepositoryAnalysis
│   ├── ast_parser.py  safe read/parse (SourceFile) + structural facts
│   ├── targets.py     callables + lexical scope helpers
│   ├── annotations.py conservative static annotation shapes
│   ├── models.py      immutable structural models
│   └── rendering.py   render_source(), source_lineno()
├── analysis/          semantic layer: what the code cares about
│   ├── __init__.py    public interface
│   ├── repository.py  per-file analysis + RepositorySemantics
│   ├── semantics.py   AST -> predicates, comparisons, constants, arithmetic
│   ├── boundaries.py  facts + parameters -> boundary candidates
│   ├── risk.py        explainable structural risk scoring
│   └── models.py      immutable semantic, boundary, and risk models
├── oracle/            oracle layer: what the code says should happen
│   ├── __init__.py    public interface
│   ├── models.py      oracle/property models and strengths
│   ├── production.py  asserts + if/raise validation guards
│   ├── test_expectations.py  test-file expectations + target resolution
│   └── repository.py  dedup, resolution, per-target support
└── generation/        action + planning layers: what to try, and how to call it
    ├── __init__.py    public interface
    ├── models.py      closed action vocabulary, statuses, completeness, cases
    ├── materialize.py actions -> concrete / symbolic / unsupported values
    ├── actions.py     applicability: boundary kinds + annotation shapes
    ├── baselines.py   trustworthy filler values + provenance
    ├── invocation.py  invocation plans: placement, origins, status
    ├── strategy.py    framework-independent strategy plans
    ├── planning.py    repository aggregation + annotation report
    └── repository.py  per-target aggregation + candidate cases
├── execution/         host side of the isolation boundary: plans -> observations
│   ├── __init__.py    public interface
│   ├── codec.py       strict typed-value protocol + AST-whitelist literal reader
│   ├── models.py      outcomes, eligibility, requests, observations, reports
│   ├── assembly.py    arguments -> a real call, with the omitted-default rule
│   ├── eligibility.py deterministic refusal gate (status, kind, path, values)
│   ├── staging.py     read-only source copy with containment and size limits
│   ├── docker.py      the only module that starts a host process (the docker CLI)
│   └── repository.py  selection, staging, one container per plan, decoding
├── evaluation/        oracle layer: did the observation disagree with an expectation
│   ├── __init__.py    public interface
│   ├── models.py      statuses, findings, reproduction, provenance models
│   ├── applicability.py  exact target + exact argument binding
│   ├── compare.py     Python's own operator semantics over decoded values
│   ├── exceptions.py  expected-vs-observed exception identity
│   ├── evaluator.py   applicability first, then the verdict (pure, no Docker)
│   ├── findings.py    evidence-backed classification + deterministic dedup
│   ├── repository.py  snapshot verification, reproduction, JSON artifact
│   ├── recheck.py     attempt sequence bookkeeping
│   └── values.py      materialization + canonical identity (NaN, -0.0 safe)
├── shrink/            minimization: can an oracle survive an input change at all?
│   ├── __init__.py    public interface
│   ├── models.py      shrinkability kinds, statuses, policy, attempts, result
│   ├── shrinkability.py  which evidence is reusable, and which is refused
│   ├── contract.py    versioned user-declared JSON contract, strictly validated
│   ├── domain.py      pure integer domain (lt/le/gt/ge) and closest-to-zero
│   ├── proposals.py   deterministic bounded proposal generation (abs(x) metric)
│   ├── signature.py   the observed failure character a reduction must preserve
│   └── repository.py  preflight, snapshot-fixed search, acceptance, artifact
└── runtime/           the trusted worker: the ONLY code that loads target code
    ├── __init__.py    package marker
    ├── worker.py      in-container loader/invoker, runs only inside the sandbox
    └── Dockerfile     digest-pinned, non-root, no-network base (package data)
```

Pipeline implemented today:

```text
repository path
    ↓
scanner/repository.py    discovery (deterministic, excludes venvs/caches/build)
    ↓
scanner/ast_parser.py     tokenize.open → ast.parse   (once per file)
    ↓
          ┌──────────────┴───────────────┐
          ▼                              ▼
scanner/targets.py                 analysis/semantics.py
structure: targets, parameters      facts: predicates, comparisons,
                                     constants, arithmetic
          │                              │
          │                              ▼
          │                    analysis/boundaries.py  boundary candidates
          │                              │
          ├──────────────────────────────┴──────────────────────────┐
          ▼                                                         ▼
oracle/production.py                                    analysis/risk.py
asserts, if/raise guards                                structural risk
          │                                                         │
          ▼                                                         │
oracle/test_expectations.py                                           │
test expectations (+ resolution)                                      │
          │                                                         │
          └──────────────────────────────┬──────────────────────────┘
                                         ▼
                              oracle/repository.py  dedup + per-target support
                                         │
                                         ▼
                    generation/actions.py      applicable semantic actions
                                         │      (boundary kinds + annotation shapes)
                                         ▼
                    generation/materialize.py concrete / symbolic / unsupported
                                         │
                                         ▼
                    generation/repository.py  single-action candidate cases
                                         │
                                         ▼
                    generation/baselines.py  safe filler values + provenance
                                         │
                                         ▼
                    generation/invocation.py invocation plans (placement, status)
                                         │
                                         ├──────────────┐
                                         ▼              ▼
                    generation/strategy.py   strategy plans (descriptions only)
                    strategy plans
                                         │
                                         ▼
                          pipeline.py     RepositoryScan
                                           (semantics, oracles, risk,
                                            generation, planning, annotations)
                                         ▼
                          cli.py          formatted output
```

Future subsystems (counterexample search, failure shrinking, a search policy) will be
added as new sibling packages, each with typed interfaces, once the features they
support actually exist. In particular, the strategy plans are deliberately *not*
tied to a generator library: a later phase may translate them into Hypothesis
strategies, or into something else, without changing anything above.

## Development

```bash
pip install -e ".[dev]"     # install with development tools
python -m pytest            # run the test suite
ruff check .                # lint
ruff format --check .       # formatting check (use `ruff format .` to apply)
mypy                        # strict type checking (configured in pyproject.toml)
```

## Roadmap

- [x] engineering foundation (packaging, CLI, tests, docs)
- [x] repository discovery
- [x] safe AST extraction with structured syntax/read issues
- [x] structural target extraction (functions, classes, methods, parameters)
- [x] predicate and comparison extraction (`if`, `while`, `assert`)
- [x] simple arithmetic extraction (maximal expressions, operators, context)
- [x] deterministic boundary mining (symbolic comparison and generic numeric)
- [x] structural risk ranking (explainable, capped, deterministic)
- [x] explicit oracle/property mining (asserts, validation guards)
- [x] basic existing-test expectation mining (`assert call == value`, `pytest.raises`)
- [x] semantic action engine (constrained transformations)
- [x] test-input materialization (symbolic boundaries → concrete candidates)
- [x] static annotation shapes (modern generics, nullable annotations)
- [x] safe baseline synthesis (no fabricated placeholders)
- [x] invocation planning (single adversarial action + labelled fillers)
- [x] strategy planning (framework-independent descriptions)
- [x] isolated execution (hardened Docker backend, explicit opt-in, no host fallback)
- [x] runtime oracle evaluation (exact applicability, safe comparison, expected
      exceptions, optional bounded reproduction, conservative findings)
- [x] action coverage repair (`bool` and `str` parameters produce plans)
- [x] qualified exception identity (module, qualname, builtin lineage from the sandbox)
- [x] shrinkability classification (an exact oracle refuses input-changing shrink)
- [x] bounded counterexample minimization (one integer, one user-declared contract)
- [ ] property-based test generation
- [ ] counterexample search
- [ ] failure shrinking beyond one integer
- [ ] adaptive neighborhood search
- [ ] learned search policy

## Documentation

- `decisions.md` — why the project is built the way it is (append-only log)
- `flow.md` — how data moves through the implemented code
- `handoff.md` — current state for the next development session
