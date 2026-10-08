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
invocation planning, strategy planning, and an isolated Docker execution backend
are implemented; the counterexample search engine is not.**

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
  `SET_NONE`, `EMPTY_COLLECTION`, `SINGLETON_COLLECTION`, and
  `DUPLICATE_ELEMENT_COLLECTION`
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
docstrings or metamorphic relations, executable test generation, oracle
evaluation, counterexamples, failure shrinking, and any model-based search.
Three things are deliberately *not* claimed here: a boundary candidate
is not a bug, mined oracle evidence is not a defect — it is an explicit
expectation a later execution stage can check — and neither a candidate case nor
an invocation plan is a reproducing input until something actually runs it. A
baseline value is not a validity claim: `0` is the simplest statically
constructible integer, not a "valid" one.

**A recorded outcome is not a finding.** `edgehunter run` reports `RETURNED`,
`RAISED`, `TIMEOUT`, `IMPORT_FAILURE`, `TARGET_NOT_FOUND`, `PROTOCOL_ERROR`, and
`WORKER_FAILURE`. Phase 6A performs no oracle evaluation, so an exception
observed during a run is a fact about that run, not a defect claim. The report
states this explicitly.

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
- A `RAISED` outcome is an observation. Phase 6A does not compare the result
  against any oracle, so it cannot and does not report a bug.

## Installation

Requires Python 3.10 or newer.

```bash
git clone <repository-url>
cd edgehunter
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
  target (directory): C:\Projects\edgehunter

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
found something" from "the run did not happen".

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

Future subsystems (execution, oracle evaluation, findings, search policy) will be
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
- [ ] property-based test generation
- [ ] isolated execution
- [ ] runtime oracle evaluation
- [ ] failure shrinking
- [ ] adaptive neighborhood search
- [ ] learned search policy

## Documentation

- `decisions.md` — why the project is built the way it is (append-only log)
- `flow.md` — how data moves through the implemented code
- `handoff.md` — current state for the next development session
