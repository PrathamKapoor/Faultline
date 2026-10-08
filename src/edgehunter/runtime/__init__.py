"""Trusted sandbox runtime shipped inside the package.

This package holds the two artifacts that must exist inside the container image:

* :mod:`edgehunter.runtime.worker` — the worker that loads and invokes untrusted
  target source, and
* ``Dockerfile`` — the recipe for the restricted image.

They live inside the distribution rather than in a top-level directory so that the
host can always locate them, whether EdgeHunter is run from a source checkout or
from an installed wheel. Nothing in this package may be imported by the host's
static-analysis path: the worker executes untrusted code and is only ever run as
a container entry point.
"""
