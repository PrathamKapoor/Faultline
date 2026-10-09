"""Content fingerprints for source-snapshot consistency.

Oracle evidence describes a specific revision of a file. Execution happens on a
staged copy taken later, so the revision actually executed must be provably the
same one the oracle was mined from, or the comparison is meaningless. A SHA-256
fingerprint of the file's raw bytes is the identity used for that check.

Raw bytes are hashed deliberately, not decoded text: a file's bytes are what the
interpreter executes, and hashing them avoids any dependence on encoding
normalization. The scanner reads bytes once and derives both the decoded text and
the digest from that single read, so no second file read is introduced.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Final

CHUNK_SIZE: Final[int] = 64 * 1024


def fingerprint_bytes(data: bytes) -> str:
    """Fingerprint raw source bytes."""
    return hashlib.sha256(data).hexdigest()


def fingerprint_file(path: Path) -> str:
    """Fingerprint a file by streaming its bytes in fixed-size chunks.

    Streamed rather than read whole so a large file cannot be doubled in memory
    just to be hashed. The caller is responsible for the path being a regular
    file; staging verifies that before calling and the scanner has already read
    it safely.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()
