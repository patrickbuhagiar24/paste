from __future__ import annotations

import json
from pathlib import Path


def test_legacy_settings_are_copied_to_pastex_config(tmp_path, monkeypatch):
    from pastex import app

    config_home = tmp_path / "config"
    new_file = config_home / "pastex" / "settings.json"
    legacy_file = config_home / "paste-teks" / "settings.json"
    legacy_file.parent.mkdir(parents=True)
    legacy_file.write_text(json.dumps({"always_on_top": True}), encoding="utf-8")
    monkeypatch.setattr(app, "SETTINGS_FILE", new_file)

    settings = app.load_settings()

    assert settings["always_on_top"] is True
    assert json.loads(new_file.read_text(encoding="utf-8"))["always_on_top"] is True
    assert legacy_file.exists()


def test_legacy_user_snippets_are_migrated(tmp_path, monkeypatch):
    from pastex import snippets

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    old_file = tmp_path / "paste-teks" / "snippets" / "General" / "Greeting.txt"
    old_file.parent.mkdir(parents=True)
    old_file.write_text("hello", encoding="utf-8")

    new_root = snippets.user_root()

    assert new_root == tmp_path / "pastex" / "snippets"
    assert (new_root / "General" / "Greeting.txt").read_text(encoding="utf-8") == "hello"
    assert old_file.exists()
