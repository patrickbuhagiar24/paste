"""Harness verifikasi-balik: kembalikan bug, pastikan uji yang relevan GAGAL.

Setiap entri: (nama, berkas, potongan lama, potongan baru, uji yang WAJIB gagal).
Skrip menyalin berkas, menerapkan mutasi, menjalankan pytest untuk uji terkait,
lalu mengembalikan berkas seperti semula.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYTEST = "/tmp/venv/bin/python"

MUTATIONS = [
    (
        "build-deb menyalin contoh lagi",
        "scripts/build-deb.sh",
        'install -m 644 usr/share/applications/paste-teks.desktop "$STAGE/usr/share/applications/"',
        'cp -r data/snippets "$STAGE/usr/share/paste-teks/"\n'
        'install -m 644 usr/share/applications/paste-teks.desktop "$STAGE/usr/share/applications/"',
        ["tests/test_v150_samples.py::test_build_deb_does_not_copy_samples"],
    ),
    (
        "debian/rules menyalin contoh lagi",
        "debian/rules",
        "\tcp -r paste_teks debian/paste-teks/usr/share/paste-teks/",
        "\tcp -r paste_teks debian/paste-teks/usr/share/paste-teks/\n"
        "\tcp -r data/snippets debian/paste-teks/usr/share/paste-teks/",
        ["tests/test_v150_samples.py::test_debian_rules_does_not_copy_samples"],
    ),
    (
        "ekspor-data menimpa berkas pengguna",
        "paste_teks/app.py",
        "            if target.exists():\n                skipped.append(target)\n                continue",
        "            if False:\n                skipped.append(target)\n                continue",
        ["tests/test_v150_samples.py::test_export_data_creates_samples_once",
         "tests/test_v150_samples.py::test_export_data_keeps_categories_and_never_overwrites"],
    ),
    (
        "tombol izin kembali ke GUI",
        "paste_teks/app.py",
        '        self.source_button.pack(side="right")',
        '        ttk.Button(opts_dummy, text="🔐 Izin Paste Otomatis",\n'
        '                   command=self.show_permission_info).pack()\n'
        '        self.source_button.pack(side="right")',
        ["tests/test_v150_permissions.py::test_permission_button_removed_from_gui_source"],
    ),
    (
        "--izin hilang dari CLI",
        "paste_teks/app.py",
        "    if args.izin:\n        return run_permission_info()",
        "    if False:\n        return run_permission_info()",
        ["tests/test_v150_permissions.py::test_izin_flag_reports_status",
         "tests/test_v150_permissions.py::test_izin_flag_points_to_activation_command"],
    ),
    (
        "--aktifkan-izin hilang dari CLI",
        "paste_teks/app.py",
        "    if args.aktifkan_izin:\n        return run_enable_permission()",
        "    if False:\n        return run_enable_permission()",
        ["tests/test_v150_permissions.py::test_aktifkan_izin_calls_helper",
         "tests/test_v150_permissions.py::test_aktifkan_izin_without_pkexec_prints_manual_command"],
    ),
    (
        "petunjuk CLI hilang dari dialog kegagalan",
        "paste_teks/app.py",
        '                 + "\\n\\n" + uinput.explain(st)\n                 + "\\n\\n" + uinput.CLI_HINT)',
        '                 + "\\n\\n" + uinput.explain(st))',
        ["tests/test_v150_permissions.py::test_paste_failure_dialog_mentions_cli"],
    ),
    (
        "petunjuk CLI hilang dari --check",
        "paste_teks/app.py",
        "        if not st.ready:\n            # Info izin tetap ada di --check walau tombolnya sudah dihapus.\n            print(f\"\\n{uinput.CLI_HINT}\")",
        "        if False:\n            print(f\"\\n{uinput.CLI_HINT}\")",
        ["tests/test_v150_permissions.py::test_check_still_reports_permission_and_cli_hint"],
    ),
    (
        "refresh_tree mengabaikan kategori ciut",
        "paste_teks/app.py",
        "        is_open = node.is_dir and str(node.path) not in collapsed",
        "        is_open = node.is_dir",
        ["tests/test_v150_collapsed.py::test_refresh_tree_restores_collapsed_state",
         "tests/test_v150_collapsed.py::test_collapsed_survives_new_session"],
    ),
    (
        "status ciut tidak disimpan",
        "paste_teks/app.py",
        "        self._save_collapsed(collapsed)\n        self.collapsed = collapsed",
        "        self.collapsed = collapsed",
        ["tests/test_v150_collapsed.py::test_open_treeview_event_marks_category_collapsed"],
    ),
    (
        "event Treeview tidak tersambung",
        "paste_teks/app.py",
        '        self.tree.bind("<<TreeviewOpen>>",\n'
        '                       lambda e: self._toggle_collapsed(self._event_iid(e)))',
        '        pass  # binding hilang',
        ["tests/test_v150_collapsed.py::test_treeview_open_close_bound_to_handler"],
    ),
    (
        "pencarian menghapus status ciut",
        "paste_teks/app.py",
        "        self.collapsed = set() if query else self._collapsed_paths()",
        "        self.collapsed = self._collapsed_paths()\n"
        "        if query:\n"
        "            self.settings[\"collapsed\"] = []\n"
        "            self.collapsed = set()",
        ["tests/test_v150_collapsed.py::test_search_expands_everything_and_does_not_overwrite_state"],
    ),
    (
        "folder sumber diabaikan saat memindai",
        "paste_teks/app.py",
        "            if path.is_dir():\n                return [path]",
        "            if False:\n                return [path]",
        ["tests/test_v150_source_dir.py::test_source_dir_replaces_default_roots"],
    ),
    (
        "--dir tidak lagi menang",
        "paste_teks/app.py",
        "        if getattr(self, \"transient_roots\", False) and roots:",
        "        if False and roots:",
        ["tests/test_v150_source_dir.py::test_dir_argument_wins_over_saved_source"],
    ),
    (
        "run_cli tidak menandai sesi --dir",
        "paste_teks/app.py",
        "app = PasteTeksApp(roots=roots, transient_roots=bool(args.dir))",
        "app = PasteTeksApp(roots=roots)",
        ["tests/test_v150_source_dir.py::test_dir_argument_transient_flag_from_cli"],
    ),
    (
        "penulisan lolos keluar folder sumber",
        "paste_teks/app.py",
        "        source = self.settings.get(\"source_dir\")\n"
        "        if source and Path(source).is_dir():\n"
        "            source_path = Path(source)",
        "        source = self.settings.get(\"source_dir\")\n"
        "        if False and Path(source or '/').is_dir():\n"
        "            source_path = Path(source)",
        ["tests/test_v150_source_dir.py::test_target_dir_is_source_root_when_nothing_selected",
         "tests/test_v150_source_dir.py::test_target_dir_never_escapes_source"],
    ),
    (
        "tampilan kosong berpetunjuk tidak muncul",
        "paste_teks/app.py",
        "        self._show_empty_state(not nodes)",
        "        self._show_empty_state(False)",
        ["tests/test_v150_source_dir.py::test_empty_state_shown_when_no_source_selected"],
    ),
    (
        "ikon jendela tidak dipasang lagi",
        "paste_teks/app.py",
        "        self.icon_files_used = apply_window_icon(self)",
        "        self.icon_files_used = []",
        ["tests/test_v150_icon.py::test_constructor_actually_applies_window_icon"],
    ),
    (
        "WM_CLASS kembali ke bawaan Tk",
        "paste_teks/app.py",
        "            super().__init__(className=WM_CLASS)",
        "            super().__init__()",
        ["tests/test_v150_icon.py::test_window_class_passed_to_tk"],
    ),
    (
        "StartupWMClass menyimpang dari app",
        "usr/share/applications/paste-teks.desktop",
        "StartupWMClass=PasteTeks",
        "StartupWMClass=Tk",
        ["tests/test_v150_icon.py::test_constants_agree_with_desktop_file"],
    ),
    (
        "PNG ikon jendela hilang",
        "usr/share/icons/hicolor/64x64/apps/paste-teks.png",
        None,
        None,
        ["tests/test_v150_icon.py::test_png_exists_for_each_size"],
    ),
    (
        "PNG ikon basi (bukan hasil skrip)",
        "usr/share/icons/hicolor/64x64/apps/paste-teks.png",
        None,
        None,
        ["tests/test_v150_icon.py::test_png_is_what_the_generator_produces"],
    ),
    (
        "--check tidak melaporkan ikon",
        "paste_teks/app.py",
        '    print(f"Ikon aplikasi: {ICON_NAME} (WM_CLASS \'{WM_CLASS}\')")',
        '    print("Ikon aplikasi: (tidak dilaporkan)")',
        ["tests/test_v150_icon.py::test_check_reports_icon_status"],
    ),
    (
        "--list diam saat tidak ada teks",
        "paste_teks/app.py",
        "        if not flatten_texts(nodes):",
        "        if False:",
        ["tests/test_v150_source_dir.py::test_cli_list_explains_when_nothing_found"],
    ),
    (
        "folder sumber tidak disimpan",
        "paste_teks/app.py",
        "        self.settings[\"source_dir\"] = _norm_path(path)\n"
        "        self.writable = path\n"
        "        save_settings(self.settings)",
        "        self.writable = path",
        ["tests/test_v150_source_dir.py::test_choose_source_dir_saves_and_refreshes"],
    ),
]


def run(tests):
    proc = subprocess.run([PYTEST, "-m", "pytest", "-q", "--no-header", *tests],
                          cwd=ROOT, capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr


def main() -> int:
    failures = []
    for name, rel, old, new, tests in MUTATIONS:
        path = ROOT / rel
        backup = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, backup)
        try:
            if old is None:
                # Mutasi berkas biner: "hapus" -> hapus berkas, "basi" -> acak isi.
                if name.startswith("PNG ikon hilang"):
                    path.unlink()
                else:
                    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
            else:
                text = path.read_text(encoding="utf-8")
                if old not in text:
                    failures.append(
                        f"[TIDAK DAPAT DITERAPKAN] {name}: potongan tidak ditemukan")
                    continue
                path.write_text(text.replace(old, new, 1), encoding="utf-8")
            code, out = run(tests)
            if code == 0:
                failures.append(f"[LOLOS PADAHAL BUG] {name}: uji {tests} tetap hijau")
                print(f"LOLOS   {name}")
            else:
                failed = [ln for ln in out.splitlines() if ln.startswith("FAILED")]
                print(f"GAGAL   {name} -> {len(failed)} uji merah")
                for ln in failed[:4]:
                    print(f"          {ln}")
        finally:
            shutil.copy2(backup, path)
            backup.unlink()
    print()
    if failures:
        print("MASALAH:")
        for item in failures:
            print(f" - {item}")
        return 1
    print(f"Semua {len(MUTATIONS)} mutasi tertangkap uji.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
