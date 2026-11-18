"""Bundled samples are not shipped in the repo or the .deb, nor on request.

Guarded regressions: `data/snippets/` reappearing in the repo,
`build-deb.sh` / `debian/rules` copying it into the package, or the
`--export-data` option (removed in 1.7.2) coming back. The decision stands
since 1.5.0: leave installed samples alone, ship no new ones.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_no_bundled_sample_tree_in_repo():
    """data/snippets/ must not exist in the repo any more."""
    assert not (ROOT / "data" / "snippets").exists()
    assert not list(ROOT.glob("data/snippets/**/*.txt"))


def test_build_deb_does_not_copy_samples():
    """build-deb.sh must not copy data/snippets into the .deb staging area."""
    script = (ROOT / "scripts" / "build-deb.sh").read_text(encoding="utf-8")
    copies = [
        ln
        for ln in script.splitlines()
        if "data/snippets" in ln and not ln.strip().startswith("#")
    ]
    assert copies == [], f"still copying bundled samples: {copies}"


def test_debian_rules_does_not_copy_samples():
    rules = (ROOT / "debian" / "rules").read_text(encoding="utf-8")
    copies = [
        ln
        for ln in rules.splitlines()
        if "data/snippets" in ln and not ln.strip().startswith("#")
    ]
    assert copies == [], f"still copying bundled samples: {copies}"


def test_samples_never_deleted_from_installed_location():
    """Samples that are ALREADY installed must not be deleted by anyone.

    postinst/postrm must not touch /usr/share/pastex/snippets: existing users
    rely on that folder as a read-only root.
    """
    for name in ("debian/postinst", "debian/postrm"):
        text = (ROOT / name).read_text(encoding="utf-8")
        offending = [
            ln
            for ln in text.splitlines()
            if "snippets" in ln
            and any(word in ln for word in ("rm ", "rm -", "unlink"))
        ]
        assert offending == [], f"{name} deletes samples: {offending}"


def test_samples_are_gone_from_the_codebase():
    """`--export-data` was removed in 1.7.2: no sample handling is left in the code."""
    import pastex.app as app
    import pastex.snippets as snippets

    assert not hasattr(snippets, "DEFAULT_SAMPLES")
    assert not hasattr(app, "export_data")


def test_docs_and_packaging_do_not_promise_the_removed_option():
    for name in ("README.md", "debian/control", "scripts/build-deb.sh"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "--export-data" not in text, f"{name} still mentions --export-data"
