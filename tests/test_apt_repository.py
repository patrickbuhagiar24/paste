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


@pytest.mark.skipif(
    shutil.which("dpkg-scanpackages") is None or shutil.which("dpkg-deb") is None,
    reason="dpkg-dev is required to build a package index",
)
def test_builder_produces_a_standard_archive(tmp_path: Path) -> None:
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

    site = tmp_path / "site"
    subprocess.run(
        [str(SCRIPTS / "build-apt-repository.sh"), str(packages), str(site), "stonking"],
        check=True,
        capture_output=True,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "PASSTEX_SIGNED": "0"},
    )

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
    # Unsigned builds publish the trusted source file, never the keyring one.
    assert (site / "pastex-unsigned.sources").is_file()
    assert not (site / "pastex.sources").exists()


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
