"""A versioned, synthetic benchmark corpus for the deterministic search engine.

The question this exists to answer is narrow and worth answering honestly:
**does feedback-guided search reach a reproduced contradiction faster than a
deterministic baseline, and where does it fail?**

Everything about the corpus follows from that:

* It is **synthetic**, with behaviour seeded on purpose and declared in a
  manifest, so a reader can check each fixture against its own documentation.
  Nothing here claims to model a real-world defect rate.
* It is **versioned** (`CORPUS_VERSION`), so a change to it is visible in every
  report and two results are only comparable when the version matches.
* It is **mixed**. It contains cases where the adaptive policy wins, a case
  where the two tie, two cases where the static baseline is better, and a correct
  target where neither may claim anything. A corpus that could only produce a
  favourable result would measure nothing.
* It **executes the real engine**. The harness calls the same `explore_integer`,
  through the same staging, container, and oracle-evaluation route, that the CLI
  calls. There is no second implementation that could disagree.

What it deliberately does **not** produce: coverage, mutation scores, or any
probability that a defect exists. Nothing here instruments those, and a benchmark
that estimated them would be reporting invented numbers.
"""

from edgehunter.benchmark.corpus import CORPUS, CORPUS_VERSION
from edgehunter.benchmark.harness import FIXTURE_ROOT, run_corpus, run_fixture
from edgehunter.benchmark.models import (
    BENCHMARK_SCHEMA,
    BenchmarkFixture,
    CaseKind,
    CaseReport,
    Corpus,
    CorpusReport,
    FixtureVerdict,
    PolicyMeasurement,
    SeededDefect,
    aggregate_probes_to_first_reproduction,
    comparable_fixture_ids,
    head_to_head,
    write_corpus_report,
)

__all__ = [
    "BENCHMARK_SCHEMA",
    "CORPUS",
    "CORPUS_VERSION",
    "FIXTURE_ROOT",
    "BenchmarkFixture",
    "CaseKind",
    "CaseReport",
    "Corpus",
    "CorpusReport",
    "FixtureVerdict",
    "PolicyMeasurement",
    "SeededDefect",
    "aggregate_probes_to_first_reproduction",
    "comparable_fixture_ids",
    "head_to_head",
    "run_corpus",
    "run_fixture",
    "write_corpus_report",
]
