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

**Early development. Repository discovery and safe static AST extraction are
implemented; the counterexample search engine is not.**

What exists today:

- an installable package with a working CLI (`edgehunter --help`,
  `edgehunter --version`)
- a `scan` command that discovers Python files (skipping virtual
  environments, caches, and build directories) and parses each one with
  Python's `ast` module
- structural extraction of modules, classes, functions, methods (including
  async and nested definitions), parameters with kinds/annotations/defaults,
  decorators, bases, and source line spans
- structured reporting of syntax errors and unreadable/undecodable files
  without aborting the scan, plus an optional `--targets` listing
- a test suite, lint/format configuration, and strict type checking

Safety: analyzed code is never imported or executed. The scanner reads text
and parses it; nothing else. The repository contains a regression test that
proves module-level side effects in scanned files do not run.

What does **not** exist yet: branch/control-flow analysis, boundary mining,
property inference, hypothesis generation, execution, oracles, minimization,
and reporting. `edgehunter scan` says so in its help text and nowhere claims
otherwise.

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

  Python files discovered: 13
  Parsed successfully:     13
  Syntax issues:           0
  Read issues:             0

  Classes:                 14
  Functions:               81
  Methods:                 19
  Async targets:           0
  Callable targets:        100
  Excluded directories:    9
```

`scan --targets` lists every callable target with its location and a static
signature (defaults are rendered as `...`; nothing is executed). Excerpt from
a small example project:

```console
$ edgehunter scan ./example --targets
...
Targets

  app/account.py:2
    Account.__init__(self, balance: float = ...) -> None
  app/account.py:5
    Account.withdraw(self, amount: float) -> float
  app/account.py:9
    async Account.refresh(self) -> None
  app/api.py:5
    list_users(limit: int = ..., *, include_inactive: bool = ...) -> list[Any]
  app/api.py:10
    outer.inner() -> int
```

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
└── scanner/
    ├── __init__.py    public scanner interface
    ├── repository.py  filesystem discovery + repository-level aggregation
    ├── models.py      immutable models (targets, parameters, issues)
    ├── targets.py     AST -> classes/callables (scope-aware, qualified names)
    └── ast_parser.py  safe source reading + ast.parse + structured issues
```

Pipeline implemented today:

```text
repository path
    ↓
repository.py        discovery (deterministic, excludes venvs/caches/build dirs)
    ↓
ast_parser.py        tokenize.open → ast.parse → issues on failure
    ↓
targets.py           functions/classes/methods/parameters/annotations/decorators
    ↓
models.py            immutable ModuleAnalysis per file
    ↓
repository.py        RepositoryAnalysis aggregate → cli.py output
```

Future subsystems (analysis, oracle, search, generators, execution, findings,
retrieval, training) will be added as sibling packages to `scanner`, each with
typed interfaces, once the features they support actually exist.

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
- [ ] control-flow / branch analysis
- [ ] boundary mining
- [ ] risk scoring
- [ ] property extraction from assertions, tests, and docstrings
- [ ] semantic action engine (constrained transformations)
- [ ] isolated execution engine
- [ ] oracle evaluation
- [ ] failure minimization
- [ ] search policy model
- [ ] reporting and `reproduce`

## Documentation

- `decisions.md` — why the project is built the way it is (append-only log)
- `flow.md` — how data moves through the implemented code
- `handoff.md` — current state for the next development session
