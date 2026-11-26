#!/usr/bin/env python3
"""Verify a published APT site before it is uploaded to Pages.

A deploy that "succeeds" can still publish the wrong thing: an archive with no
index, a package the index does not name, checksums that no longer match, or a
page that links files the site does not have. Every one of those has happened
to projects that trusted the exit code of a build.

The check runs on the directory that will be uploaded, so it is the last thing
between a mistake and the public archive.

Usage: verify-site.py SITE_DIRECTORY SUITE [--signed|--unsigned]
       Without a flag the site is checked as an unsigned archive.
"""

from __future__ import annotations

import gzip
import hashlib
import re
import subprocess
import sys
from pathlib import Path


def fail(message: str) -> None:
    print(f"verify-site: {message}", file=sys.stderr)
    raise SystemExit(1)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path: Path) -> str:
    if not path.is_file():
        fail(f"{path} is missing")
    return path.read_text(encoding="utf-8", errors="replace")


def check_index(site: Path, suite: str) -> str:
    """The Packages index must exist, be current, and name a real file."""
    index_dir = site / "dists" / suite / "main" / "binary-all"
    packages = read(index_dir / "Packages")
    if "Package: pastex" not in packages:
        fail(f"{index_dir}/Packages does not list the package")
    filename = next(
        (line.split(": ", 1)[1] for line in packages.splitlines() if line.startswith("Filename: ")),
        "",
    )
    if not filename:
        fail("the index has no Filename field, so apt cannot find the package")
    package = site / filename
    if not package.is_file():
        fail(f"the index names {filename}, which the site does not contain")

    # Packages.gz must describe the same index, or a client that fetches the
    # compressed copy sees a different archive than one that does not.
    compressed = index_dir / "Packages.gz"
    if not compressed.is_file():
        fail(f"{compressed} is missing")
    with gzip.open(compressed, "rb") as handle:
        if handle.read().decode("utf-8") != packages:
            fail("Packages.gz does not match Packages")

    # One version in the pool, and only the one the index names.
    debs = sorted((site / "pool").rglob("*.deb"))
    if len(debs) != 1:
        fail(f"the pool holds {len(debs)} packages; it must publish exactly one")
    if debs[0].resolve() != package.resolve():
        fail(f"the pool holds {debs[0].name} but the index names {filename}")
    return filename


def check_release(site: Path, suite: str) -> None:
    release = read(site / "dists" / suite / "Release")
    for field in ("Suite", "Codename", "Date", "Valid-Until", "Architectures", "SHA256"):
        if not re.search(rf"^{field}:", release, re.MULTILINE):
            fail(f"Release has no {field} field")
    if f"Suite: {suite}" not in release:
        fail(f"Release does not name the suite {suite}")
    # The digest lines must match the files that are actually shipped.
    for line in release.splitlines():
        match = re.match(r"^ ([0-9a-f]{64}) +(\d+) +(\S+)$", line)
        if not match:
            continue
        digest, size, relative = match.groups()
        path = site / "dists" / suite / relative
        if not path.is_file():
            fail(f"Release lists {relative}, which is not in the archive")
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            fail(f"Release checksum for {relative} does not match the file")


def check_no_stale_suite(site: Path, suite: str) -> None:
    """A retired suite must not linger: apt would keep reading it."""
    dists = site / "dists"
    present = sorted(p.name for p in dists.iterdir() if p.is_dir())
    if present != [suite]:
        fail(f"dists/ holds {present}; only {suite!r} may be published")


def check_signature(site: Path, suite: str) -> None:
    inrelease = site / "dists" / suite / "InRelease"
    keyring = site / "archive-keyring.gpg"
    if not inrelease.is_file() or not keyring.is_file():
        fail("a signed archive needs dists/<suite>/InRelease and archive-keyring.gpg")
    if not (site / "dists" / suite / "Release.gpg").is_file():
        fail("a signed archive needs dists/<suite>/Release.gpg")
    if not (site / "pastex.sources").is_file():
        fail("a signed archive must publish pastex.sources")
    try:
        result = subprocess.run(
            ["gpgv", "--keyring", str(keyring), str(inrelease)],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        fail("gpgv is required to verify the signature of a signed archive")
    if result.returncode != 0:
        fail(f"gpgv rejects InRelease: {(result.stderr or result.stdout).strip()}")
    fingerprint = (site / "archive-keyring.fingerprint.txt").read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"[A-F0-9]{40}", fingerprint):
        fail("archive-keyring.fingerprint.txt does not hold a 40-character fingerprint")


def check_checksums(site: Path, filename: str) -> None:
    """checksums.txt is what a user compares against a rebuild."""
    text = read(site / "checksums.txt")
    lines = [line for line in text.splitlines() if line.strip()]
    if not any(line.endswith(f"  {filename}") or line.endswith(f" *{filename}") for line in lines):
        fail(f"checksums.txt does not mention {filename}")
    for line in lines:
        digest, _, relative = line.partition("  ")
        if relative and (site / relative).is_file():
            if sha256(site / relative) != digest:
                fail(f"checksums.txt is stale for {relative}")
    if not (site / "extension-id.txt").is_file():
        fail("extension-id.txt is missing, so a user cannot compare the extension id")
    extension_id = (site / "extension-id.txt").read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"[a-p]{32}", extension_id):
        fail(f"extension-id.txt does not hold an extension id: {extension_id!r}")


def check_page(site: Path) -> None:
    """Every local link on the downloaded page must exist on the site."""
    page = read(site / "index.html")
    if not (site / ".nojekyll").is_file():
        fail(".nojekyll is missing; Pages would run the files through Jekyll")
    for target in re.findall(r'href="([^"#:]+)"', page):
        if target.startswith("//") or target.startswith("mailto:"):
            continue
        if not (site / target.lstrip("/")).exists():
            fail(f"index.html links {target}, which the site does not publish")


def main(argv: list[str]) -> int:
    if len(argv) not in (3, 4):
        print(__doc__.strip().splitlines()[-2], file=sys.stderr)
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    site = Path(argv[1])
    suite = argv[2]
    signed = len(argv) == 4 and argv[3] == "--signed"
    if len(argv) == 4 and argv[3] not in ("--signed", "--unsigned"):
        print("Use --signed or --unsigned.", file=sys.stderr)
        return 2

    if not site.is_dir():
        fail(f"{site} does not exist")
    filename = check_index(site, suite)
    check_release(site, suite)
    check_no_stale_suite(site, suite)
    check_checksums(site, filename)
    check_page(site)
    if signed:
        check_signature(site, suite)
    print(f"verify-site: ok ({suite}, {filename}{', signed' if signed else ''})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
