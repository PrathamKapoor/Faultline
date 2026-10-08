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
   policy that proposes constrained actions such as `NEGATE_VALUE`,
   `CROSS_BOUNDARY`, `EMPTY_COLLECTION`, or `DUPLICATE_ELEMENT`, rather than
   free-form generated tests.
4. **Isolated execution** — hypotheses are executed, and an oracle decides
   whether a property holds. A model-generated statement is never proof of a
   bug; execution proves findings.
5. **Counterexample minimization** — shrink a violation to the smallest input
   that still reproduces it, then report it with a reproduction command.

Deterministic logic comes first: ordinary algorithms handle boundaries and
canonical inputs; a model makes semantic decisions, not replacements for
straightforward code.

## Current status

**Early development. Repository discovery, safe static AST extraction, semantic
fact extraction, and deterministic boundary mining are implemented; the
counterexample search engine is not.**

What exists today:

- an installable package with a working CLI (`edgehunter --help`,
  `edgehunter --version`)
- a `scan` command that discovers Python files (skipping virtual
  environments, caches, and build directories) and parses each one with
  Python's `ast` module — each file is read and parsed exactly once
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
- structured reporting of syntax errors and unreadable/undecodable files
  without aborting the scan, plus `--targets` and `--boundaries` detail views
- a test suite, lint/format configuration, and strict type checking

Safety: analyzed code is never imported or executed. The scanner reads text
and parses it; nothing else. Annotations are matched as source text and never
resolved. Regression tests prove module-level side effects in scanned files do
not run and that the package contains no execution primitives.

What does **not** exist yet: risk scoring, property/oracle inference, test
generation, execution, counterexamples, failure shrinking, and any model-based
search. Boundary candidates are **not** bugs: they are regions a future
generator should attack, and nothing here claims a defect exists.

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

  Python files discovered: 22
  Parsed successfully:     22
  Syntax issues:           0
  Read issues:             0

  Classes:                 36
  Functions:               175
  Methods:                 41
  Async targets:           0
  Callable targets:        216
  Excluded directories:    10

  Predicates:              465
  Comparisons:             408
  Arithmetic expressions:  88
  Boundary candidates:     6
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
EdgeHunter's own source yields only 6 candidates because almost nothing is
annotated exactly `int` or `float`; generic numeric candidates require that
literal annotation.

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
├── scanner/           structural layer: what exists
│   ├── __init__.py    public interface
│   ├── repository.py  discovery + RepositoryAnalysis
│   ├── ast_parser.py  safe read/parse (SourceFile) + structural facts
│   ├── targets.py     AST -> functions/classes (scope-aware, qualified names)
│   ├── models.py      immutable structural models
│   └── rendering.py   shared ast.unparse rendering
└── analysis/          semantic layer: what the code cares about
    ├── __init__.py    public interface
    ├── repository.py  analyze_repository_semantics (structure + semantics)
    ├── semantics.py   AST -> predicates, comparisons, constants, arithmetic
    ├── boundaries.py  facts + parameters -> boundary candidates
    └── models.py      immutable semantic and boundary models
```

Pipeline implemented today:

```text
repository path
    ↓
scanner/repository.py    discovery (deterministic, excludes venvs/caches/build)
    ↓
scanner/ast_parser.py     tokenize.open → ast.parse  (once per file)
    ↓
          ┌──────────────┴───────────────┐
          ▼                              ▼
scanner/targets.py                 analysis/semantics.py
structure: targets, parameters      facts: predicates, comparisons,
                                     constants, arithmetic
          └──────────────┬───────────────┘
                         ▼
            analysis/boundaries.py    boundary candidates (symbolic)
                         ↓
            analysis/repository.py    RepositorySemantics (one read/parse per file)
                         ↓
            cli.py                     formatted output
```

Future subsystems (oracle/property mining, risk scoring, search policy,
generation, execution, findings) will be added as new sibling packages, each
with typed interfaces, once the features they support actually exist.

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
- [x] safe AST parsing with structured syntax/read issues
- [x] structural target extraction (functions, classes, methods, parameters)
- [x] predicate and comparison extraction (`if`, `while`, `assert`)
- [x] simple arithmetic extraction (maximal expressions, operators, context)
- [x] deterministic boundary mining (symbolic comparison and generic numeric)
- [ ] risk-based target prioritization
- [ ] property/oracle extraction (assertions, docstrings, test expectations)
- [ ] semantic action engine (constrained transformations)
- [ ] test generation
- [ ] isolated execution
- [ ] failure shrinking
- [ ] learned search policy

## Documentation

- `decisions.md` — why the project is built the way it is (append-only log)
- `flow.md` — how data moves through the implemented code
- `handoff.md` — current state for the next development session
