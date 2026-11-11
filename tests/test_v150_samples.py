"""Uji v1.5.0 #1: contoh bawaan tidak lagi disertakan repo / .deb.

Regresi yang dijaga: `data/snippets/` muncul kembali di repo, atau
`build-deb.sh` / `debian/rules` kembali menyalinnya ke dalam paket. Dua hal
itu bertentangan dengan keputusan 1.5.0 — "biarkan contoh yang sudah
terpasang, jangan bawa contoh baru".
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
    copies = [ln for ln in script.splitlines()
              if "data/snippets" in ln and not ln.strip().startswith("#")]
    assert copies == [], f"masih menyalin contoh bawaan: {copies}"


def test_debian_rules_does_not_copy_samples():
    rules = (ROOT / "debian" / "rules").read_text(encoding="utf-8")
    copies = [ln for ln in rules.splitlines()
              if "data/snippets" in ln and not ln.strip().startswith("#")]
    assert copies == [], f"masih menyalin contoh bawaan: {copies}"


def test_samples_never_deleted_from_installed_location():
    """Contoh yang SUDAH terpasang tidak boleh dihapus siapa pun.

    postinst/postrm tidak boleh menyentuh /usr/share/pastex/snippets:
    pengguna lama memakai folder itu sebagai root baca-saja.
    """
    for name in ("debian/postinst", "debian/postrm"):
        text = (ROOT / name).read_text(encoding="utf-8")
        offending = [ln for ln in text.splitlines()
                     if "snippets" in ln and any(
                         word in ln for word in ("rm ", "rm -", "unlink"))]
        assert offending == [], f"{name} menghapus contoh: {offending}"


def test_export_data_creates_samples_once(tmp_path, capsys):
    """Pengganti contoh bawaan: `--export-data` membuat contoh sekali saja."""
    from pastex.app import run_cli

    assert run_cli(["--export-data", str(tmp_path)]) == 0
    created = sorted(p.name for p in tmp_path.rglob("*.txt"))
    assert created, "no samples were created"
    first = {p: p.read_text(encoding="utf-8") for p in tmp_path.rglob("*.txt")}
    # Pengguna mengedit satu contoh, lalu menjalankan ulang: tidak ditimpa.
    edited = tmp_path / "General" / "Greeting.txt"
    edited.write_text("editanku", encoding="utf-8")
    assert run_cli(["--export-data", str(tmp_path)]) == 0
    assert edited.read_text(encoding="utf-8") == "editanku"
    for path, content in first.items():
        if path != edited:
            assert path.read_text(encoding="utf-8") == content
    assert "0 files created" in capsys.readouterr().out


def test_export_data_keeps_categories_and_never_overwrites(tmp_path):
    """--export-data menambah yang belum ada saja, tidak menghapus/menimpa."""
    from pastex.app import export_data

    manual = tmp_path / "Catatan Saya"
    manual.mkdir()
    (manual / "Penting.txt").write_text("do not remove", encoding="utf-8")

    created = export_data(tmp_path)
    assert created, "no samples were created"
    assert (tmp_path / "General" / "Greeting.txt").read_text(encoding="utf-8")
    assert (manual / "Penting.txt").read_text(encoding="utf-8") == "do not remove"
    assert export_data(tmp_path) == [], "dijalankan kedua kali tetap membuat berkas"
