"""Uji CLI Paste Teks (tanpa GUI/display)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from paste_teks.app import run_cli


def test_version(capsys):
    assert run_cli(["--version"]) == 0
    assert "paste-teks" in capsys.readouterr().out


def test_check(capsys):
    assert run_cli(["--check"]) == 0
    out = capsys.readouterr().out
    assert "Python:" in out
    assert "tkinter:" in out
    assert "Folder snippets:" in out


def test_list(tmp_path, capsys):
    (tmp_path / "Umum").mkdir()
    (tmp_path / "Umum" / "Sapaan.txt").write_text("halo", encoding="utf-8")
    assert run_cli(["--dir", str(tmp_path), "--list"]) == 0
    out = capsys.readouterr().out
    assert "Umum" in out and "Sapaan" in out
