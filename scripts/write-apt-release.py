#!/usr/bin/env python3
"""Write Release checksums for every index in an APT distribution directory.

Produces a standard flat ``dists/<suite>/Release`` file so APT clients can
verify the archive with ``InRelease``/``Release.gpg`` instead of being told to
trust the host with ``[trusted=yes]``.
"""

from __future__ import annotations

import hashlib
import sys
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path

SUITE_PATTERN = "abcdefghijklmnopqrstuvwxyz0123456789-"


def write_release(distribution: Path, suite: str, *, origin: str = "Pastex") -> None:
    """Write ``distribution/Release`` with digest lines for every index file."""
    files = sorted(
        path
        for path in distribution.rglob("*")
        if path.is_file() and path.name not in ("Release", "Release.gpg", "InRelease")
    )
    if not files:
        raise ValueError("Cannot publish an archive without package indices.")
    fields = [
        f"Origin: {origin}",
        f"Label: {origin}",
        f"Suite: {suite}",
        f"Codename: {suite}",
        f"Date: {format_datetime(datetime.now(timezone.utc), usegmt=True)}",
        "Architectures: all",
        "Components: main",
        f"Description: {origin} packages for Linux Mint ({suite})",
        "Acquire-By-Hash: no",
    ]
    for label, algorithm in (
        ("MD5Sum", "md5"),
        ("SHA1", "sha1"),
        ("SHA256", "sha256"),
        ("SHA512", "sha512"),
    ):
        fields.append(f"{label}:")
        for path in files:
            data = path.read_bytes()
            digest = hashlib.new(algorithm, data).hexdigest()
            relative = path.relative_to(distribution).as_posix()
            fields.append(f" {digest} {len(data):16d} {relative}")
    (distribution / "Release").write_text("\n".join(fields) + "\n", encoding="utf-8")


def validate_suite(suite: str) -> str:
    """Return ``suite`` after checking it looks like an APT codename."""
    if not suite or not suite[0].isalpha() or suite[0].isupper():
        raise ValueError("Invalid APT suite name.")
    if any(character not in SUITE_PATTERN for character in suite):
        raise ValueError("Invalid APT suite name.")
    return suite


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(f"Usage: {argv[0]} DISTRIBUTION_DIRECTORY SUITE", file=sys.stderr)
        return 2
    try:
        suite = validate_suite(argv[2])
        write_release(Path(argv[1]), suite)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
