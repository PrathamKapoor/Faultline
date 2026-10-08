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
   rather than free-form generated tests.
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
explicit oracle mining, and a constrained semantic-action engine with input
materialization are implemented; the counterexample search engine is not.**

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
- a closed semantic-action vocabulary (`SemanticActionKind`): each mined
  boundary candidate maps onto exactly one action, and collection annotations
  add `EMPTY_COLLECTION` / `SINGLETON_COLLECTION`
- input materialization that writes values down as Python source text:
  `CONCRETE` for literal references (exact integer neighbours such as
  `99`/`100`/`101`, and adjacent floats via `math.nextafter`), `SYMBOLIC` when
  the value depends on a name, and `UNSUPPORTED` when no value exists
- single-action candidate cases that never invent a value for another
  parameter, and instead report which required parameters they leave uncovered
- structured reporting of syntax errors and unreadable files without aborting
  the scan, plus `--targets`, `--boundaries`, `--risk`, `--properties`,
  `--actions`, and `--candidates`

Safety: analyzed code is never imported or executed, including tests. The
scanner reads text and parses it; nothing else. Annotations are matched as
source text and never resolved. Materialized values are source text produced
for a later phase and are never run here. Regression tests prove module-level
side effects in scanned files do not run, that mined test expectations are not
executed, that materialized values are strings rather than constructed objects,
and that the package contains no execution primitives.

What does **not** exist yet: risk calibration, property inference from
docstrings or metamorphic relations, executable test generation, execution,
oracle evaluation, counterexamples, failure shrinking, and any model-based
search. Three things are deliberately *not* claimed here: a boundary candidate
is not a bug, mined oracle evidence is not a defect — it is an explicit
expectation a later execution stage can check — and a candidate case is not a
reproducing input until something actually runs it.

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

## Usage

```console
$ edgehunter --version
edgehunter 0.1.0

$ edgehunter scan .
EdgeHunter scan
  target (directory): C:\Projects\edgehunter

  Python files discovered: 41
  Parsed successfully:     41
  Syntax issues:           0
  Read issues:             0

  Classes:                 65
  Functions:               381
  Methods:                 66
  Async targets:           0
  Callable targets:        447
  Excluded directories:    12

  Predicates:              954
  Comparisons:             857
  Arithmetic expressions:  150
  Boundary candidates:     19

  Oracle evidence
    Assertions:                   0
    Validation rules:             3
    Resolved test expectations:   70
    Unresolved test expectations: 34
    Ambiguous test expectations:  5
    Property candidates:          112
    Strongly supported targets:   14
    Risk-scored targets:          447

  Semantic actions
    Actions proposed:          19
    Concrete values:           12
    Symbolic values:           7
    Unsupported actions:       0
    Targets with actions:      9
    Candidate cases:           19
    Complete cases:            0
    Partial cases:             12
    Symbolic cases:            7
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
└── generation/        action layer: which inputs are worth trying
    ├── __init__.py    public interface
    ├── models.py      closed action vocabulary, statuses, candidate cases
    ├── materialize.py actions -> concrete / symbolic / unsupported values
    ├── actions.py     applicability: boundary kinds + annotation categories
    └── repository.py  per-target aggregation + candidate-case completeness
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
                                         │      (boundary kinds + annotations)
                                         ▼
                    generation/materialize.py concrete / symbolic / unsupported
                                         │
                                         ▼
                    generation/repository.py  candidate cases + completeness
                                         │
                                         ▼
                          pipeline.py     RepositoryScan
                                           (semantics, oracles, risk, generation)
                                         ↓
                          cli.py          formatted output
```

Future subsystems (execution, oracle evaluation, findings, search policy) will be
added as new sibling packages, each with typed interfaces, once the features they
support actually exist.

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
- [ ] property-based test generation
- [ ] isolated execution
- [ ] oracle evaluation
- [ ] failure shrinking
- [ ] adaptive neighborhood search
- [ ] learned search policy

## Documentation

- `decisions.md` — why the project is built the way it is (append-only log)
- `flow.md` — how data moves through the implemented code
- `handoff.md` — current state for the next development session
