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

**Early development / engineering foundation. The counterexample search engine
is not implemented yet.**

What exists today:

- an installable package with a working CLI (`edgehunter --help`,
  `edgehunter --version`)
- a `scan` command that performs safe filesystem repository discovery: it
  counts Python files and skips virtual environments, caches, and build
  directories; it never imports or executes the target code
- a test suite, lint/format configuration, and strict type checking

What does **not** exist yet: AST analysis, property inference, hypothesis
generation, execution, oracles, minimization, and reporting. `edgehunter scan`
prints an explicit note saying so.

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
  python files discovered: 7
  excluded directories skipped: 9

Repository analysis is not implemented yet (Phase 0 foundation).
```

`scan` accepts a directory or a single `.py` file. Invalid input fails cleanly
with exit code 2 and no traceback:

```console
$ edgehunter scan ./does-not-exist
error: path does not exist: does-not-exist
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
    └── repository.py  filesystem discovery (no AST analysis, no execution)
```

The CLI is a thin presentation layer; library modules do not print. Future
subsystems (analysis, oracle, search, generators, execution, findings,
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

- [x] project foundation (packaging, CLI, repository discovery, tests, docs)
- [ ] repository scanner + AST extraction
- [ ] boundary mining
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
