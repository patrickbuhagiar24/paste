"""Tests for the signed, standard-layout APT archive published on Pages.

The layout mirrors the repository used by ``patrickbuhagiar24/youtube`` so APT
reports ``Hit: … https://patrickbuhagiar24.github.io/paste stonking InRelease``
instead of fetching a flat ``./ Packages`` index from a trusted host.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
PACKAGING = ROOT / "packaging"
WORKFLOW = ROOT / ".github" / "workflows" / "build-deb.yml"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_sources_file_uses_the_stonking_suite_and_keyring() -> None:
    sources = _text(PACKAGING / "pastex.sources")

    assert "Types: deb" in sources
    assert "URIs: https://patrickbuhagiar24.github.io/paste/" in sources
    assert "Suites: stonking" in sources
    assert "Components: main" in sources
    assert "Architectures: all" in sources
    assert "Signed-By: /etc/apt/keyrings/pastex.gpg" in sources
    assert "Trusted: yes" not in sources


def test_unsigned_sources_file_marks_the_archive_trusted() -> None:
    sources = _text(PACKAGING / "pastex-unsigned.sources")

    assert "Suites: stonking" in sources
    assert "Trusted: yes" in sources
    assert "Signed-By" not in sources


def test_builder_defaults_to_the_stonking_suite_and_pool_layout() -> None:
    script = _text(SCRIPTS / "build-apt-repository.sh")

    assert 'suite=${3:-stonking}' in script
    assert "pool/main/p/pastex" in script
    assert 'dists/$suite/main/binary-all/Packages' in script
    assert "dpkg-scanpackages --multiversion" in script
    assert 'touch "$staging/.nojekyll"' in script
    assert "apt-index.html" in script
    assert "write-apt-release.py" in script
    assert "PASSTEX_SIGNED" in script


def test_signer_publishes_inrelease_and_keyrings() -> None:
    script = _text(SCRIPTS / "sign-apt-repository.sh")

    assert "APT_SIGNING_KEY_ID" in script
    assert "--clearsign" in script
    assert "--detach-sign" in script
    assert "archive-keyring.gpg" in script
    assert "archive-keyring.asc" in script
    assert "archive-keyring.fingerprint.txt" in script
    assert "gpgv" in script
    assert "pastex.sources" in script
    assert "\nset -x" not in script


def test_release_writer_emits_suite_checksums(tmp_path: Path) -> None:
    distribution = tmp_path / "dists" / "stonking"
    index = distribution / "main" / "binary-all"
    index.mkdir(parents=True)
    (index / "Packages").write_text("Package: pastex\n", encoding="utf-8")
    (index / "Packages.gz").write_bytes(b"\x1f\x8b\x08\x00")

    subprocess.run(
        [sys.executable, str(SCRIPTS / "write-apt-release.py"), str(distribution), "stonking"],
        check=True,
    )

    release = _text(distribution / "Release")
    assert "Suite: stonking" in release
    assert "Codename: stonking" in release
    assert "Architectures: all" in release
    assert "Components: main" in release
    for label in ("MD5Sum:", "SHA1:", "SHA256:", "SHA512:"):
        assert label in release
    assert "main/binary-all/Packages" in release
    assert "main/binary-all/Packages.gz" in release


def test_release_writer_rejects_an_invalid_suite(tmp_path: Path) -> None:
    distribution = tmp_path / "dist"
    distribution.mkdir()

    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "write-apt-release.py"), str(distribution), "Not A Suite"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "Invalid APT suite name" in result.stderr


NEEDS_DPKG = pytest.mark.skipif(
    shutil.which("dpkg-scanpackages") is None or shutil.which("dpkg-deb") is None,
    reason="dpkg-dev is required to build a package index",
)


def _build_test_package(tmp_path: Path) -> Path:
    stage = tmp_path / "deb" / "DEBIAN"
    stage.mkdir(parents=True)
    (stage / "control").write_text(
        "Package: pastex\n"
        "Version: 1.6.8-1\n"
        "Section: utils\n"
        "Priority: optional\n"
        "Architecture: all\n"
        "Maintainer: Test <test@example.com>\n"
        "Description: Test package\n",
        encoding="utf-8",
    )
    packages = tmp_path / "deb-out"
    packages.mkdir()
    subprocess.run(
        [
            "dpkg-deb",
            "--build",
            "--root-owner-group",
            str(tmp_path / "deb"),
            str(packages / "pastex_1.6.8-1_all.deb"),
        ],
        check=True,
        capture_output=True,
    )
    return packages


def _run_builder(packages: Path, site: Path, *, signed: bool) -> None:
    subprocess.run(
        [str(SCRIPTS / "build-apt-repository.sh"), str(packages), str(site), "stonking"],
        check=True,
        capture_output=True,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "PASSTEX_SIGNED": "1" if signed else "0",
        },
    )


@NEEDS_DPKG
def test_builder_produces_a_standard_archive(tmp_path: Path) -> None:
    packages = _build_test_package(tmp_path)
    site = tmp_path / "site"

    _run_builder(packages, site, signed=False)

    assert (site / "pool" / "main" / "p" / "pastex" / "pastex_1.6.8-1_all.deb").is_file()
    index = site / "dists" / "stonking" / "main" / "binary-all"
    packages_index = _text(index / "Packages")
    assert "Package: pastex" in packages_index
    # APT resolves the pool path from the index, so it must exist as published.
    filename = next(
        line.split(": ", 1)[1]
        for line in packages_index.splitlines()
        if line.startswith("Filename: ")
    )
    assert filename == "pool/main/p/pastex/pastex_1.6.8-1_all.deb"
    assert (site / filename).is_file()
    assert (index / "Packages.gz").is_file()
    assert "Suite: stonking" in _text(site / "dists" / "stonking" / "Release")
    assert (site / ".nojekyll").is_file()
    assert (site / "index.html").is_file()
    assert not (site / "override").exists()


@NEEDS_DPKG
def test_unsigned_archive_links_only_files_it_publishes(tmp_path: Path) -> None:
    packages = _build_test_package(tmp_path)
    site = tmp_path / "site"

    _run_builder(packages, site, signed=False)

    page = _text(site / "index.html")
    assert (site / "pastex-unsigned.sources").is_file()
    assert not (site / "pastex.sources").exists()
    assert "pastex-unsigned.sources" in page
    # Nothing signed is advertised while it does not exist.
    assert "archive-keyring.gpg" not in page
    assert "InRelease" not in page
    assert "sgn:" not in page
    assert "uns:" not in page


@NEEDS_DPKG
def test_signed_archive_links_the_signed_sources_only(tmp_path: Path) -> None:
    packages = _build_test_package(tmp_path)
    site = tmp_path / "site"

    _run_builder(packages, site, signed=True)

    page = _text(site / "index.html")
    assert (site / "pastex.sources").is_file()
    assert not (site / "pastex-unsigned.sources").exists()
    assert "archive-keyring.gpg" in page
    assert "pastex.sources" in page
    assert "dists/stonking/InRelease" in page
    # The unsigned fallback must not be linked when it is not published.
    assert "pastex-unsigned.sources" not in page
    assert "uns:" not in page
    assert "sgn:" not in page


def test_index_template_keeps_both_notice_blocks() -> None:
    template = _text(PACKAGING / "apt-index.html")

    assert "<!--sgn:start-->" in template
    assert "<!--sgn:end-->" in template
    assert "<!--uns:start-->" in template
    assert "<!--uns:end-->" in template
    # Marker names must not contain each other, or sed ranges would overlap.
    assert "unsigned-notice" not in template
    assert "signed-notice" not in template
    assert template.index("<!--sgn:end-->") < template.index("<!--uns:start-->")


def test_workflow_signs_and_verifies_before_deploying() -> None:
    workflow = _text(WORKFLOW)

    assert "build-apt-repository.sh dist site" in workflow
    assert "sign-apt-repository.sh site" in workflow
    assert "include-hidden-files: true" in workflow
    assert "archive-keyring" in workflow or "gpg --batch --import" in workflow
    assert "apt-repo/dist" not in workflow
    assert "git push origin gh-pages" not in workflow
    assert workflow.index("sign-apt-repository.sh") < workflow.index("deploy-pages")


def test_documentation_uses_the_signed_deb822_source() -> None:
    readme = _text(ROOT / "README.md")
    apt_readme = _text(ROOT / "apt-repo" / "README.md")

    for document in (readme, apt_readme):
        assert "/etc/apt/keyrings/pastex.gpg" in document
        assert "pastex.sources" in document
        assert "stonking" in document
        assert "/paste/apt/" not in document.split("Migrating")[0]

    assert "Hit:6 https://patrickbuhagiar24.github.io/paste stonking InRelease" in apt_readme


FAKE_KEY_ID = "A" * 40


def _fake_gpg_tools(tmp_path: Path) -> tuple[Path, Path]:
    """Create gpg/gpgv stand-ins so the signer can be tested without GnuPG."""
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    call_log = tmp_path / "gpg-calls.log"

    (bin_dir / "gpg").write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$FAKE_GPG_LOG"
args="$*"
if [[ "$args" == *--list-secret-keys* ]]; then
  if [[ "$args" == *--with-colons* ]]; then
    echo 'sec:u:255:22:ABCDEF0123456789:1700000000:::Pastex <t@example.com>:::scESCA:::'
    echo "fpr:::::::::$FAKE_GPG_FINGERPRINT:"
  fi
  exit 0
fi
if [[ "$args" == *--clearsign* || "$args" == *--detach-sign* ]]; then
  if [[ "${FAKE_GPG_FAIL:-0}" == "1" ]]; then
    echo 'gpg: signing failed: Bad passphrase' >&2
    exit 2
  fi
  while [[ $# -gt 0 ]]; do
    if [[ "$1" == "--output" ]]; then
      printf 'FAKE SIGNATURE\\n' > "$2"
      shift 2
      continue
    fi
    shift
  done
  exit 0
fi
if [[ "$args" == *--export* ]]; then
  if [[ "$args" == *--armor* ]]; then
    echo '-----BEGIN PGP PUBLIC KEY BLOCK-----'
    echo 'FAKEKEY'
    echo '-----END PGP PUBLIC KEY BLOCK-----'
  else
    echo 'FAKE-BINARY-KEYRING'
  fi
fi
exit 0
""",
        encoding="utf-8",
    )
    (bin_dir / "gpgv").write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf 'gpgv %s\\n' "$*" >> "$FAKE_GPG_LOG"
exit "${FAKE_GPGV_STATUS:-0}"
""",
        encoding="utf-8",
    )
    for tool in ("gpg", "gpgv"):
        (bin_dir / tool).chmod(0o755)
    call_log.write_text("", encoding="utf-8")
    return bin_dir, call_log


def _prepare_archive(tmp_path: Path) -> Path:
    repository = tmp_path / "site"
    index = repository / "dists" / "stonking" / "main" / "binary-all"
    index.mkdir(parents=True)
    (index / "Packages").write_text("Package: pastex\n", encoding="utf-8")
    (repository / "dists" / "stonking" / "Release").write_text(
        "Suite: stonking\n", encoding="utf-8"
    )
    return repository


def _run_signer(tmp_path: Path, repository: Path, **overrides: str):
    bin_dir, call_log = _fake_gpg_tools(tmp_path)
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "FAKE_GPG_LOG": str(call_log),
        "FAKE_GPG_FINGERPRINT": FAKE_KEY_ID,
        "APT_SIGNING_KEY_ID": FAKE_KEY_ID,
    }
    env.update(overrides)
    result = subprocess.run(
        [str(SCRIPTS / "sign-apt-repository.sh"), str(repository), "stonking"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    return result, call_log.read_text(encoding="utf-8")


def test_signer_creates_inrelease_keyrings_and_source(tmp_path: Path) -> None:
    repository = _prepare_archive(tmp_path)

    result, calls = _run_signer(tmp_path, repository)

    assert result.returncode == 0, result.stderr
    assert "successfully" in result.stdout
    distribution = repository / "dists" / "stonking"
    assert (distribution / "InRelease").read_text(encoding="utf-8").startswith("FAKE SIGNATURE")
    assert (distribution / "Release.gpg").is_file()
    assert "FAKE-BINARY-KEYRING" in (repository / "archive-keyring.gpg").read_text(
        encoding="utf-8"
    )
    assert "BEGIN PGP PUBLIC KEY BLOCK" in (repository / "archive-keyring.asc").read_text(
        encoding="utf-8"
    )
    assert (repository / "archive-keyring.fingerprint.txt").read_text(
        encoding="utf-8"
    ).strip() == FAKE_KEY_ID
    assert "Signed-By: /etc/apt/keyrings/pastex.gpg" in (
        repository / "pastex.sources"
    ).read_text(encoding="utf-8")
    # Without a passphrase, gpg is never asked for one.
    assert "--passphrase-fd" not in calls
    assert "--pinentry-mode" not in calls
    # Both signatures are verified with the exported keyring.
    assert calls.count("gpgv") == 2


def test_signer_passes_the_passphrase_when_one_is_configured(tmp_path: Path) -> None:
    repository = _prepare_archive(tmp_path)

    result, calls = _run_signer(tmp_path, repository, GPG_PASSPHRASE="secret")

    assert result.returncode == 0, result.stderr
    assert "--pinentry-mode loopback --passphrase-fd 3" in calls


def test_signer_reports_a_missing_fingerprint(tmp_path: Path) -> None:
    repository = _prepare_archive(tmp_path)
    bin_dir, call_log = _fake_gpg_tools(tmp_path)

    result = subprocess.run(
        [str(SCRIPTS / "sign-apt-repository.sh"), str(repository), "stonking"],
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": f"{bin_dir}:/usr/bin:/bin", "FAKE_GPG_LOG": str(call_log)},
    )

    assert result.returncode == 1
    assert "APT_SIGNING_KEY_ID" in result.stderr


def test_signer_hints_at_the_passphrase_when_signing_fails(tmp_path: Path) -> None:
    repository = _prepare_archive(tmp_path)

    result, _calls = _run_signer(tmp_path, repository, FAKE_GPG_FAIL="1")

    assert result.returncode == 1
    assert "GPG_PASSPHRASE" in result.stderr
    assert not (repository / "dists" / "stonking" / "InRelease").exists()


def test_workflow_exports_the_fingerprint_before_signing() -> None:
    """The signer reads APT_SIGNING_KEY_ID; the workflow must export it."""
    workflow = _text(WORKFLOW)
    signer_call = workflow.index('./scripts/sign-apt-repository.sh site "$APT_SUITE"')

    assert 'export APT_SIGNING_KEY_ID="$fingerprint"' in workflow
    assert workflow.index('export APT_SIGNING_KEY_ID="$fingerprint"') < signer_call
    # Failures must surface as a readable annotation instead of a silent gap.
    assert "::error::APT signing failed" in workflow
