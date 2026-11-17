"""Contoh bawaan tidak disertakan repo / .deb dan tidak bisa diminta.

Regresi yang dijaga: `data/snippets/` muncul kembali di repo,
`build-deb.sh` / `debian/rules` menyalinnya ke dalam paket, atau opsi
`--export-data` (dihapus di 1.7.2) muncul lagi. Keputusannya tetap sama
sejak 1.5.0: biarkan contoh yang sudah terpasang, jangan bawa contoh baru.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_no_bundled_sample_tree_in_repo():
    """data/snippets/ tidak boleh ada lagi di repo."""
    assert not (ROOT / "data" / "snippets").exists()
    assert not list(ROOT.glob("data/snippets/**/*.txt"))


def test_build_deb_does_not_copy_samples():
    """build-deb.sh tidak boleh menyalin data/snippets ke staging .deb."""
    script = (ROOT / "scripts" / "build-deb.sh").read_text(encoding="utf-8")
    copies = [
        ln
        for ln in script.splitlines()
        if "data/snippets" in ln and not ln.strip().startswith("#")
    ]
    assert copies == [], f"masih menyalin contoh bawaan: {copies}"


def test_debian_rules_does_not_copy_samples():
    rules = (ROOT / "debian" / "rules").read_text(encoding="utf-8")
    copies = [
        ln
        for ln in rules.splitlines()
        if "data/snippets" in ln and not ln.strip().startswith("#")
    ]
    assert copies == [], f"masih menyalin contoh bawaan: {copies}"


def test_samples_never_deleted_from_installed_location():
    """Contoh yang SUDAH terpasang tidak boleh dihapus siapa pun.

    postinst/postrm tidak boleh menyentuh /usr/share/pastex/snippets:
    pengguna lama memakai folder itu sebagai root baca-saja.
    """
    for name in ("debian/postinst", "debian/postrm"):
        text = (ROOT / name).read_text(encoding="utf-8")
        offending = [
            ln
            for ln in text.splitlines()
            if "snippets" in ln
            and any(word in ln for word in ("rm ", "rm -", "unlink"))
        ]
        assert offending == [], f"{name} menghapus contoh: {offending}"


def test_samples_are_gone_from_the_codebase():
    """`--export-data` dihapus di 1.7.2: tidak ada lagi contoh di kode."""
    import pastex.app as app
    import pastex.snippets as snippets

    assert not hasattr(snippets, "DEFAULT_SAMPLES")
    assert not hasattr(app, "export_data")


def test_export_data_option_is_rejected():
    """Argparse harus menolak opsi yang sudah dihapus, bukan diam-diam jalan."""
    import pytest

    from pastex.app import run_cli

    with pytest.raises(SystemExit) as excinfo:
        run_cli(["--export-data"])
    assert excinfo.value.code == 2


def test_list_hint_never_points_at_the_removed_option(tmp_path, capsys):
    from pastex.app import run_cli

    empty = tmp_path / "kosong"
    empty.mkdir()
    assert run_cli(["--dir", str(empty), "--list"]) == 0
    err = capsys.readouterr().err
    assert "--dir" in err
    assert "--export-data" not in err


def test_docs_and_packaging_do_not_promise_the_removed_option():
    for name in ("README.md", "debian/control", "scripts/build-deb.sh"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "--export-data" not in text, f"{name} masih menyebut --export-data"
