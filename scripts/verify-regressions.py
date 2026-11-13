"""verify-regressions harness: restore known bugs and ensure the relevant tests fail.

Each entry is (name, file, old text, new text, tests that must fail).
The script backs up a file, applies a mutation, runs the relevant pytest cases,
then restores the original file.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYTHON = sys.executable

MUTATIONS = [
    (
        "build-deb bundles sample data again",
        "scripts/build-deb.sh",
        'install -m 644 usr/share/applications/pastex.desktop "$STAGE/usr/share/applications/"',
        (
            'cp -r data/snippets "$STAGE/usr/share/pastex/"\n'
            'install -m 644 usr/share/applications/pastex.desktop "$STAGE/usr/share/applications/"'
        ),
        ["tests/test_v150_samples.py::test_build_deb_does_not_copy_samples"],
    ),
    (
        "debian/rules bundles sample data again",
        "debian/rules",
        "\tcp -r pastex debian/pastex/usr/share/pastex/",
        (
            "\tcp -r pastex debian/pastex/usr/share/pastex/\n"
            "\tcp -r data/snippets debian/pastex/usr/share/pastex/"
        ),
        ["tests/test_v150_samples.py::test_debian_rules_does_not_copy_samples"],
    ),
    (
        "export-data overwrites user files",
        "pastex/app.py",
        "            if target.exists():\n                skipped.append(target)\n                continue",
        "            if False:\n                skipped.append(target)\n                continue",
        [
            "tests/test_v150_samples.py::test_export_data_creates_samples_once",
            "tests/test_v150_samples.py::test_export_data_keeps_folders_and_never_overwrites",
        ],
    ),
    (
        "permission button returns to the GUI",
        "pastex/app.py",
        '        self.source_button.pack(side="right")',
        (
            '        ttk.Button(opts_dummy, text="🔐 Izin Paste Otomatis",\n'
            "                   command=self.show_permission_info).pack()\n"
            '        self.source_button.pack(side="right")'
        ),
        [
            "tests/test_v150_permissions.py::test_permission_button_removed_from_gui_source"
        ],
    ),
    (
        "--permissions is missing from the CLI",
        "pastex/app.py",
        "    if args.permissions:\n        return run_permission_info()",
        "    if False:\n        return run_permission_info()",
        [
            "tests/test_v150_permissions.py::test_izin_flag_reports_status",
            "tests/test_v150_permissions.py::test_izin_flag_points_to_activation_command",
        ],
    ),
    (
        "--enable-permissions is missing from the CLI",
        "pastex/app.py",
        "    if args.enable_permissions:\n        return run_enable_permission()",
        "    if False:\n        return run_enable_permission()",
        [
            "tests/test_v150_permissions.py::test_aktifkan_izin_calls_helper",
            "tests/test_v150_permissions.py::test_aktifkan_izin_without_pkexec_prints_manual_command",
        ],
    ),
    (
        "CLI hint is missing from the failure dialog",
        "pastex/app.py",
        "            + uinput.CLI_HINT",
        '            + ""',
        ["tests/test_v150_permissions.py::test_paste_failure_dialog_mentions_cli"],
    ),
    (
        "CLI hint is missing from --check",
        "pastex/app.py",
        (
            "            # Keep permission information in --check even though the GUI button was removed.\n"
            '            print(f"\\n{uinput.CLI_HINT}")'
        ),
        (
            "            # Keep permission information in --check even though the GUI button was removed.\n"
            "            pass"
        ),
        [
            "tests/test_v150_permissions.py::test_check_still_reports_permission_and_cli_hint"
        ],
    ),
    (
        "refresh_tree ignores collapsed folders",
        "pastex/app.py",
        "        is_open = node.is_dir and str(node.path) not in collapsed",
        "        is_open = node.is_dir",
        [
            "tests/test_v150_collapsed.py::test_refresh_tree_restores_collapsed_state",
            "tests/test_v150_collapsed.py::test_collapsed_survives_new_session",
        ],
    ),
    (
        "collapsed state is not saved",
        "pastex/app.py",
        "        self._save_collapsed(collapsed)\n        self.collapsed = collapsed",
        "        self.collapsed = collapsed",
        [
            "tests/test_v150_collapsed.py::test_open_treeview_event_marks_folder_collapsed"
        ],
    ),
    (
        "Treeview event is not connected",
        "pastex/app.py",
        r"""        self.tree.bind(
            "<<TreeviewOpen>>", lambda e: self._toggle_collapsed(self._event_iid(e))
        )""",
        "        pass  # binding hilang",
        ["tests/test_v150_collapsed.py::test_treeview_open_close_bound_to_handler"],
    ),
    (
        "search clears collapsed state",
        "pastex/app.py",
        "        self.collapsed = set() if query else self._collapsed_paths()",
        (
            "        self.collapsed = self._collapsed_paths()\n"
            "        if query:\n"
            '            self.settings["collapsed"] = []\n'
            "            self.collapsed = set()"
        ),
        [
            "tests/test_v150_collapsed.py::test_search_expands_everything_and_does_not_overwrite_state"
        ],
    ),
    (
        "source folder is ignored during scanning",
        "pastex/app.py",
        "            if path.is_dir():\n                return [path]",
        "            if False:\n                return [path]",
        ["tests/test_v150_source_dir.py::test_source_dir_replaces_default_roots"],
    ),
    (
        "--dir no longer takes precedence",
        "pastex/app.py",
        '        if getattr(self, "transient_roots", False) and roots:',
        "        if False and roots:",
        ["tests/test_v150_source_dir.py::test_dir_argument_wins_over_saved_source"],
    ),
    (
        "run_cli does not mark the --dir session",
        "pastex/app.py",
        "app = PastexApp(roots=roots, transient_roots=bool(args.dir))",
        "app = PastexApp(roots=roots)",
        ["tests/test_v150_source_dir.py::test_dir_argument_transient_flag_from_cli"],
    ),
    (
        "writes escape the source folder",
        "pastex/app.py",
        (
            '        source = self.settings.get("source_dir")\n'
            "        if source and Path(source).is_dir():\n"
            "            source_path = Path(source)"
        ),
        (
            '        source = self.settings.get("source_dir")\n'
            "        if False and Path(source or '/').is_dir():\n"
            "            source_path = Path(source)"
        ),
        [
            "tests/test_v150_source_dir.py::test_target_dir_is_source_root_when_nothing_selected",
            "tests/test_v150_source_dir.py::test_target_dir_never_escapes_source",
        ],
    ),
    (
        "guided empty state is not shown",
        "pastex/app.py",
        "        self._show_empty_state(not nodes)",
        "        self._show_empty_state(False)",
        [
            "tests/test_v150_source_dir.py::test_empty_state_shown_when_no_source_selected"
        ],
    ),
    (
        "window icon is no longer applied",
        "pastex/app.py",
        "        self.icon_files_used = apply_window_icon(self)",
        "        self.icon_files_used = []",
        ["tests/test_v150_icon.py::test_constructor_actually_applies_window_icon"],
    ),
    (
        "WM_CLASS falls back to the Tk default",
        "pastex/app.py",
        "            super().__init__(className=TK_CLASS_NAME)",
        "            super().__init__()",
        ["tests/test_v150_icon.py::test_window_class_passed_to_tk"],
    ),
    (
        "StartupWMClass does not match the app",
        "usr/share/applications/pastex.desktop",
        "StartupWMClass=Pastex",
        "StartupWMClass=Tk",
        ["tests/test_v150_icon.py::test_constants_agree_with_desktop_file"],
    ),
    (
        "window icon PNG is missing",
        "usr/share/icons/hicolor/64x64/apps/pastex.png",
        None,
        None,
        ["tests/test_v150_icon.py::test_png_exists_for_each_size"],
    ),
    (
        "icon PNG is stale (not generated by the script)",
        "usr/share/icons/hicolor/64x64/apps/pastex.png",
        None,
        None,
        ["tests/test_v150_icon.py::test_png_is_what_the_generator_produces"],
    ),
    (
        "--check does not report the icon",
        "pastex/app.py",
        "    print(f\"Application icon: {ICON_NAME} (expected WM_CLASS '{WM_CLASS}')\")",
        '    print("Application icon: (not reported)")',
        ["tests/test_v150_icon.py::test_check_reports_icon_status"],
    ),
    (
        "--list is silent when no text is found",
        "pastex/app.py",
        "        if not flatten_texts(nodes):",
        "        if False:",
        ["tests/test_v150_source_dir.py::test_cli_list_explains_when_nothing_found"],
    ),
    (
        "source folder is not saved",
        "pastex/app.py",
        (
            '        self.settings["source_dir"] = _norm_path(path)\n'
            "        self.writable = path\n"
            "        save_settings(self.settings)"
        ),
        "        self.writable = path",
        ["tests/test_v150_source_dir.py::test_choose_source_dir_saves_and_refreshes"],
    ),
]


def run(tests):
    proc = subprocess.run(
        [PYTHON, "-m", "pytest", "-q", "--no-header", *tests],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode, proc.stdout + proc.stderr


def main() -> int:
    failures = []
    for name, rel, old, new, tests in MUTATIONS:
        path = ROOT / rel
        backup = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, backup)
        try:
            if old is None:
                # Binary mutation: "missing" deletes the file; "stale" replaces its contents.
                if name == "window icon PNG is missing":
                    path.unlink()
                else:
                    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
            else:
                text = path.read_text(encoding="utf-8")
                if old not in text:
                    failures.append(f"[CANNOT APPLY] {name}: old text was not found")
                    continue
                path.write_text(text.replace(old, new, 1), encoding="utf-8")
            code, out = run(tests)
            if code == 0:
                failures.append(f"[BUG NOT CAUGHT] {name}: tests {tests} still pass")
                print(f"PASS    {name}")
            else:
                failed = [ln for ln in out.splitlines() if ln.startswith("FAILED")]
                print(f"FAIL    {name} -> {len(failed)} failing tests")
                for ln in failed[:4]:
                    print(f"          {ln}")
        finally:
            shutil.copy2(backup, path)
            backup.unlink()
    print()
    if failures:
        print("ISSUES:")
        for item in failures:
            print(f" - {item}")
        return 1
    print(f"All {len(MUTATIONS)} mutations were caught by tests.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
