"""verify-regressions harness: restore known bugs and ensure the relevant tests fail.

Each entry is (name, file, old text, new text, tests that must fail).
The script backs up a file, applies a mutation, runs the relevant pytest cases,
then restores the original file.
"""

from __future__ import annotations

import os
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
        "the empty-folder hint points at the removed --export-data",
        "pastex/app.py",
        '                "Choose a source folder in the GUI or pass --dir FOLDER.",',
        '                "Choose a source folder in the GUI or run --export-data.",',
        ["tests/test_v150_samples.py::test_list_hint_never_points_at_the_removed_option"],
    ),
    (
        "a repaint always inserts new rows instead of reusing them",
        "pastex/app.py",
        "            if self.tree.exists(iid):",
        "            if False:",
        ["tests/test_app.py::test_refresh_reuses_rows_instead_of_rebuilding"],
    ),
    (
        "changed rows are never updated in place",
        "pastex/app.py",
        "        if changes:\n            self.tree.item(iid, **changes)",
        "        if False:\n            self.tree.item(iid, **changes)",
        ["tests/test_app.py::test_refresh_updates_the_open_state_in_place"],
    ),
    (
        "Tk's string options are mistaken for changes on every repaint",
        "pastex/app.py",
        '            "open": self._as_flag(self.tree.item(iid, "open")),',
        '            "open": self.tree.item(iid, "open"),',
        ["tests/test_app.py::test_row_update_accepts_tcl_string_options"],
    ),
    (
        "rows of deleted files stay in the tree",
        "pastex/app.py",
        "            if iid not in keep:\n                self.tree.delete(iid)",
        "            if iid not in keep:\n                pass",
        ["tests/test_app.py::test_refresh_drops_rows_that_left_the_disk"],
    ),
    (
        "the tree is never re-ordered",
        "pastex/app.py",
        "        for index, iid in enumerate(wanted):\n            self.tree.move(iid, parent, index)",
        "        for index, iid in enumerate(wanted):\n            break",
        ["tests/test_app.py::test_refresh_reorders_rows_without_rebuilding"],
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
            "                # Keep permission information in --check even though the GUI\n"
            "                # button was removed.\n"
            '                print(f"\\n{uinput.CLI_HINT}")'
        ),
        (
            "                # Keep permission information in --check even though the GUI\n"
            "                # button was removed.\n"
            "                pass"
        ),
        [
            "tests/test_v150_permissions.py::test_check_still_reports_permission_and_cli_hint"
        ],
    ),
    (
        "refresh_tree ignores collapsed folders",
        "pastex/app.py",
        '            "open": node.is_dir and str(node.path) not in collapsed,',
        '            "open": node.is_dir,',
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
        "        pass  # binding missing",
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
    (
        "left click no longer moves the darker highlight",
        "pastex/app.py",
        "        self.tree.focus(row)\n        self._set_context_highlight(row)\n        if self._select_mode:",
        "        self.tree.focus(row)\n        if self._select_mode:",
        [
            "tests/test_folder_controls.py::test_left_click_moves_context_highlight_between_rows",
            "tests/test_folder_controls.py::test_left_click_in_select_mode_moves_context_highlight",
        ],
    ),
    (
        "select mode right click moves the darker highlight",
        "pastex/app.py",
        "        if not self._select_mode:\n            self._set_context_highlight(row)",
        "        if True:\n            self._set_context_highlight(row)",
        [
            "tests/test_folder_controls.py::test_right_click_in_select_mode_keeps_existing_context_highlight",
        ],
    ),
    (
        "highlight is dropped when the context menu closes",
        "pastex/app.py",
        "    def _context_menu_unmapped(self, _event=None) -> None:\n        self._context_menu_active = False\n        self._cancel_menu_focus_watch()",
        "    def _context_menu_unmapped(self, _event=None) -> None:\n        self._context_menu_active = False\n        self._cancel_menu_focus_watch()\n        self._clear_context_highlight()",
        [
            "tests/test_folder_controls.py::test_right_click_normal_mode_applies_darker_context_tag_and_keeps_it",
        ],
    ),
    (
        "popup menu survives switching to another application",
        "pastex/app.py",
        "        if focused is not None:\n            return\n        self._unpost_context_menu()",
        "        if focused is not None:\n            return\n        return",
        [
            "tests/test_folder_controls.py::test_menu_is_dismissed_when_another_app_takes_focus",
        ],
    ),
    (
        "paste withdraws the window and drops the dock icon",
        "pastex/app.py",
        "        try:\n            self.iconify()\n            self.update_idletasks()",
        "        try:\n            self.withdraw()\n            self.update_idletasks()",
        ["tests/test_app.py::test_hide_for_paste_minimizes_instead_of_unmapping"],
    ),
    (
        "a hidden window is no longer recognized",
        "pastex/app.py",
        '        if state in {"iconic", "withdrawn"}:\n            if not self.__dict__.get("_hide_logged", False):',
        '        if False:\n            if not self.__dict__.get("_hide_logged", False):',
        ["tests/test_app.py::test_the_hide_check_is_polled_until_the_compositor_answers"],
    ),
    (
        "the minimize check is taken once instead of polled",
        "pastex/app.py",
        "        if waited >= MINIMIZE_WAIT_MS or not PastexApp._tk_ready(self):\n            return False",
        "        if True:\n            return False",
        ["tests/test_app.py::test_the_hide_check_is_polled_until_the_compositor_answers"],
    ),
    (
        "Ctrl+V is sent into a window that never hid",
        "pastex/app.py",
        "        if not PastexApp._paste_window_hidden(self):",
        "        if False:",
        ["tests/test_app.py::test_a_window_that_never_hides_keeps_ctrl_v_unsent"],
    ),
    (
        "the ignored minimize request is never repeated",
        "pastex/app.py",
        "            self._rehide_done = True\n            if self.__dict__.get(\"_paste_hide_via_x11\", False):",
        "            self._rehide_done = True\n            if False:",
        ["tests/test_app.py::test_the_rehide_does_not_call_tk_iconify"],
    ),
    (
        "Ctrl+V is armed even when the window stays visible",
        "pastex/app.py",
        (
            "        if hidden:\n"
            "            self._send_paste()\n"
        ),
        "        self._send_paste()\n",
        [
            "tests/test_app.py::test_paste_skips_ctrl_v_when_the_window_cannot_be_minimized"
        ],
    ),
    (
        "a copy that never armed Ctrl+V still reports a paste",
        "pastex/app.py",
        (
            '        if not self.__dict__.get("_paste_pending", False):\n'
            "            # The window could not be minimized, so no Ctrl+V was armed: keep\n"
            "            # the icon and let the user paste manually.\n"
            "            self._pasting = False\n"
            "            self.status_var.set(PASTE_MANUAL_HINT)\n"
            '            log_error("[paste] kept the window visible; Ctrl+V not sent")\n'
            "            return\n"
        ),
        "",
        [
            "tests/test_app.py::test_finished_copy_without_an_armed_send_shows_the_manual_hint"
        ],
    ),
    (
        "search rescans the folders on every keystroke",
        "pastex/app.py",
        '''        self._search_after_id = self.after(
            180, lambda: self.refresh_tree(force=False)
        )
''',
        '        self._search_after_id = self.after(180, self.refresh_tree)',
        [
            "tests/test_app.py::test_search_repaints_from_the_cache_after_the_debounce"
        ],
    ),
    (
        "the first watcher event is swallowed on a freshly booted machine",
        "pastex/watcher.py",
        "        if self._last is not None and now - self._last < self.coalesce:",
        "        if now - (self._last or 0.0) < self.coalesce:",
        ["tests/test_watcher.py::test_first_event_fires_even_on_a_clock_that_just_started"],
    ),
    (
        "every inotify event refreshes the tree (no coalescing)",
        "pastex/watcher.py",
        "        if self._last is not None and now - self._last < self.coalesce:",
        "        if False:",
        [
            "tests/test_watcher.py::test_events_are_coalesced",
            "tests/test_watcher.py::test_coalesce_window_is_measured_from_the_last_callback",
        ],
    ),
    (
        "the tree ignores the in-memory index",
        "pastex/app.py",
        "        nodes = self.index.nodes(force=force)",
        "        nodes = scan_roots(roots)",
        ["tests/test_app.py::test_refresh_tree_reuses_the_cached_index"],
    ),
    (
        "inotify events never reach the tree",
        "pastex/app.py",
        '''        try:
            self.after(0, self._refresh_from_disk)
        except TK_ERRORS:  # pragma: no cover - shutting down
            pass
''',
        "        pass  # watcher diabaikan\n",
        ["tests/test_app.py::test_watcher_callback_marshals_to_the_gui_thread"],
    ),
    (
        "the tree is rebuilt in the middle of a paste",
        "pastex/app.py",
        '''        if getattr(self, "_pasting", False):
            self.after(150, self._refresh_from_disk)
            return
''',
        "",
        ["tests/test_app.py::test_refresh_from_disk_waits_while_pasting"],
    ),
    (
        "the picker minimizes the main window",
        "pastex/app.py",
        "            hidden = self._hide_widget(hide)",
        "            hidden = self._hide_for_paste()",
        ["tests/test_app.py::test_picker_paste_does_not_touch_the_main_window"],
    ),
    (
        "the opt-in clipboard owner is never used for copies",
        "pastex/clipboard.py",
        "            if active_owner.copy(text):",
        "            if False:",
        [
            "tests/test_clipboard.py::test_owner_copies_without_spawning_a_tool",
            "tests/test_clipboard.py::test_module_level_owner_is_used_by_default",
        ],
    ),
    (
        "a broken clipboard owner breaks the copy instead of falling back",
        "pastex/clipboard.py",
        "        except Exception:  # noqa: BLE001 - the tools remain a safe fallback\n            pass",
        "        except TypeError:  # noqa: BLE001\n            pass",
        ["tests/test_clipboard.py::test_owner_exception_never_breaks_the_copy"],
    ),
    (
        "an owner that fails its self-test is trusted anyway",
        "pastex/app.py",
        '            log_error(f"[owner] self-test failed ({detail}); keeping the tools")\n            owner.stop()\n            return False',
        '            log_error(f"[owner] self-test failed ({detail}); keeping the tools")',
        ["tests/test_app.py::test_clipboard_owner_that_fails_its_self_test_is_stopped"],
    ),
    (
        "the clipboard owner is not released when the window closes",
        "pastex/app.py",
        '        owner = self.__dict__.get("_clipboard_owner")\n        if owner is None:\n            return',
        '        owner = self.__dict__.get("_clipboard_owner")\n        if True:\n            return',
        ["tests/test_app.py::test_releasing_the_owner_restores_the_tools"],
    ),
    (
        "the watchdog is never armed for a paste",
        "pastex/app.py",
        "        if hidden:\n            self._send_paste()\n            PastexApp._arm_paste_watchdog(self)",
        "        if hidden:\n            self._send_paste()",
        ["tests/test_app.py::test_a_click_with_a_hidden_window_arms_the_watchdog"],
    ),
    (
        "a stalled paste leaves the window minimized forever",
        "pastex/app.py",
        '        if self.__dict__.get("_paste_finished", True):\n            return\n        self._paste_aborted = True',
        '        if True:\n            return\n        self._paste_aborted = True',
        [
            "tests/test_app.py::test_the_watchdog_restores_the_window_when_the_sender_stalls",
            "tests/test_app.py::test_the_watchdog_does_nothing_after_a_finished_paste",
        ],
    ),
    (
        "the sender ignores the abort flag",
        "pastex/app.py",
        "                    should_stop=lambda: bool(\n                        self.__dict__.get(\"_paste_aborted\", False)\n                    ),",
        "                    should_stop=None,",
        ["tests/test_app.py::test_paste_passes_the_prewarmed_plans"],
    ),
    (
        "a failed paste is forgotten and the next click hides the window again",
        "pastex/app.py",
        '        self._paste_ready = bool(getattr(result, "ok", False))',
        "        self._paste_ready = True",
        [
            "tests/test_app.py::test_a_failed_paste_stops_hiding_the_window_on_the_next_click",
        ],
    ),
    (
        "keystroke tools may block the paste forever",
        "pastex/clipboard.py",
        "            proc = _run_with_timeout(runner, cmd, PASTE_KEY_TIMEOUT)\n            # ydotool needs its daemon",
        "            proc = runner(cmd)\n            # ydotool needs its daemon",
        ["tests/test_clipboard.py::test_simulate_paste_bounds_every_tool_call"],
    ),
    (
        "the portal is opened even after the paste was abandoned",
        "pastex/clipboard.py",
        "    portal = None if stop() else _try_portal()",
        "    portal = _try_portal()",
        ["tests/test_clipboard.py::test_simulate_paste_stops_before_the_portal_when_aborted"],
    ),
    (
        "a missing user unit leaves ydotool without a daemon",
        "pastex/clipboard.py",
        '    binary = shutil.which("ydotoold")\n    if binary is None:',
        "    binary = None\n    if binary is None:",
        [
            "tests/test_clipboard.py::test_the_private_daemon_is_started_with_our_own_socket",
        ],
    ),
    (
        "the CLI leaves the clipboard owner running after the loop ends",
        "pastex/app.py",
        '        release_owner = getattr(app, "_release_clipboard_owner", None)\n        if callable(release_owner):\n            release_owner()',
        '        release_owner = None\n        if callable(release_owner):\n            release_owner()',
        [
            "tests/test_app.py::test_closing_the_main_loop_releases_the_owner",
            "tests/test_app.py::test_a_crashing_main_loop_still_releases_the_owner",
        ],
    ),
    (
        "the clipboard helper starts even when the user did not opt in",
        "pastex/app.py",
        '        if self.__dict__.get("settings", {}).get("clipboard_owner"):\n            self._start_clipboard_owner()',
        '        if True:\n            self._start_clipboard_owner()',
        ["tests/test_app.py::test_prewarm_only_starts_the_owner_when_asked"],
    ),
    (
        "the helper never writes the payload the compositor asks for",
        "pastex/wlserver.py",
        '            data = text.encode("utf-8")',
        '            data = b""',
        ["tests/test_wlserver.py::test_send_event_writes_the_payload_and_closes_the_fd"],
    ),
    (
        "the helper never destroys the replaced source",
        "pastex/wlserver.py",
        '        if previous:\n            self._request(previous, SOURCE_DESTROY, "", [])',
        '        if False:\n            self._request(previous, SOURCE_DESTROY, "", [])',
        [
            "tests/test_wlserver.py::test_copy_offers_every_mime_and_sets_the_new_selection"
        ],
    ),
    (
        "the self-test trusts the owner without reading the selection back",
        "pastex/wlowner.py",
        "    ok = seen == nonce",
        "    ok = True",
        ["tests/test_wlowner.py::test_verify_rejects_an_owner_that_never_serves_it"],
    ),
    (
        "a picker paste steals the focus back",
        "pastex/app.py",
        "        self._paste_restore_window = hide is None",
        "        self._paste_restore_window = True",
        ["tests/test_app.py::test_picker_paste_does_not_touch_the_main_window"],
    ),
    (
        "every launch starts a second window",
        "pastex/app.py",
        '''    if not args.dir:
        answer = send_command("pick" if args.pick else "show")
        if answer is not None and answer.get("ok"):
            return 0
''',
        '''    if False:
        answer = send_command("pick" if args.pick else "show")
        if answer is not None and answer.get("ok"):
            return 0
''',
        ["tests/test_app.py::test_cli_forwards_show_to_a_running_instance"],
    ),
    (
        "the instance lock is not taken",
        "pastex/instance.py",
        "            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)",
        "            pass  # lock diabaikan",
        ["tests/test_instance.py::test_only_one_instance_claims_the_slot"],
    ),
    (
        "the portal is never consulted",
        "pastex/clipboard.py",
        "    portal = None if stop() else _try_portal()",
        "    portal = None",
        ["tests/test_clipboard.py::test_simulate_paste_falls_back_to_the_portal"],
    ),
    (
        "the portal presses the keys in the wrong order",
        "pastex/portal.py",
        '''_sequence = [
    (CTRL, PRESSED),
    (V, PRESSED),
    (V, RELEASED),
    (CTRL, RELEASED),
]''',
        '''_sequence = [
    (CTRL, PRESSED),
    (V, RELEASED),
    (V, PRESSED),
    (CTRL, RELEASED),
]''',
        ["tests/test_portal.py::test_full_sequence_presses_ctrl_v"],
    ),
    (
        "the portal asks for a session on every paste",
        "pastex/portal.py",
        '''    if _session_handle is not None:
        return _session_handle
''',
        "",
        ["tests/test_portal.py::test_session_is_reused_so_the_prompt_appears_once"],
    ),
    (
        "Ctrl+Down reorder shortcut is not bound",
        "pastex/app.py",
        '        self.tree.bind("<Control-Down>", lambda _e: self._on_tree_move_shortcut(1))',
        "        pass",
        ["tests/test_folder_controls.py::test_tree_binds_arrow_navigation_and_ctrl_reorder"],
    ),
    (
        "Up arrow navigation is not bound",
        "pastex/app.py",
        '        self.tree.bind("<Up>", lambda _e: self._on_tree_arrow(-1))',
        "        pass",
        ["tests/test_folder_controls.py::test_tree_binds_arrow_navigation_and_ctrl_reorder"],
    ),
    (
        "paste waits for the old slow restore delay",
        "pastex/app.py",
        '    "restore_delay": 0.03,',
        '    "restore_delay": 0.1,',
        ["tests/test_app.py::test_paste_done_waits_30ms_after_sending_before_restoring"],
    ),
    (
        "Ctrl+V is sent before the clipboard owns the text",
        "pastex/app.py",
        (
            '        if not self.__dict__.get("_paste_clipboard_ready", True):\n'
            "            # The window is already hidden but the clipboard tool has not\n"
            "            # finished yet; _after_copy() calls back here as soon as it has.\n"
            "            return\n"
        ),
        "",
        ["tests/test_app.py::test_ctrl_v_waits_until_the_clipboard_is_ready"],
    ),
    (
        "the window is only hidden after the copy finishes",
        "pastex/app.py",
        (
            "        if hide is None:\n"
            "            hidden = self._hide_for_paste()\n"
            "        else:\n"
            "            hidden = self._hide_widget(hide)\n"
            "        if hidden:\n"
            "            self._send_paste()\n"
            "            PastexApp._arm_paste_watchdog(self)\n"
            "        _run_in_thread(self._paste_worker, text)"
        ),
        "        _run_in_thread(self._paste_worker, text)",
        ["tests/test_app.py::test_paste_text_hides_before_copying"],
    ),
    (
        "startup no longer prewarms the clipboard tools",
        "pastex/app.py",
        (
            "        # Resolve clipboard and paste tools off the click path.\n"
            "        self._prewarm_paste()"
        ),
        "        pass  # prewarm dihilangkan",
        ["tests/test_app.py::test_startup_schedules_the_prewarm"],
    ),
    (
        "paste ignores the prewarmed tool plans",
        "pastex/app.py",
        'copy_text(text, plan=self.__dict__.get("_copy_plan"))',
        "copy_text(text)",
        ["tests/test_app.py::test_paste_passes_the_prewarmed_plans"],
    ),
    (
        "prewarm starts ydotoold outside a Wayland session",
        "pastex/clipboard.py",
        'if session == "wayland" and paste_plan and not _ydotoold_socket_exists():',
        "if paste_plan and not _ydotoold_socket_exists():",
        ["tests/test_clipboard.py::test_prewarm_never_starts_ydotoold_on_x11"],
    ),
    (
        "unmap no longer triggers the paste",
        "pastex/app.py",
        '        self.bind("<Unmap>", self._on_window_unmap, add="+")',
        "        pass",
        ["tests/test_app.py::test_window_unmap_is_bound_to_the_paste_sender"],
    ),
    (
        "signed index links the missing unsigned sources file",
        "scripts/build-apt-repository.sh",
        "  sed -i '/<!--uns:start-->/,/<!--uns:end-->/d' \"$staging/index.html\"",
        "  true",
        ["tests/test_apt_repository.py::test_signed_archive_links_the_signed_sources_only"],
    ),
    (
        "unsigned index links the missing signed keyring",
        "scripts/build-apt-repository.sh",
        "  sed -i '/<!--sgn:start-->/,/<!--sgn:end-->/d' \"$staging/index.html\"",
        "  true",
        ["tests/test_apt_repository.py::test_unsigned_archive_links_only_files_it_publishes"],
    ),
    (
        "workflow forgets to export the signing fingerprint",
        ".github/workflows/build-deb.yml",
        '          export APT_SIGNING_KEY_ID="$fingerprint"',
        '          : "$fingerprint"',
        [
            "tests/test_apt_repository.py::test_workflow_exports_the_fingerprint_before_signing"
        ],
    ),
    (
        "signer always asks gpg for a passphrase",
        "scripts/sign-apt-repository.sh",
        'if [[ -n "$passphrase" ]]; then',
        'if [[ -n "${passphrase:-}" || true ]]; then',
        ["tests/test_apt_repository.py::test_signer_creates_inrelease_keyrings_and_source"],
    ),
    (
        "signer stops verifying the InRelease signature",
        "scripts/sign-apt-repository.sh",
        'gpgv --keyring "$verification_keyring" "$distribution/InRelease"',
        "true",
        ["tests/test_apt_repository.py::test_signer_creates_inrelease_keyrings_and_source"],
    ),
    (
        "APT archive no longer uses the mint suite",
        "scripts/build-apt-repository.sh",
        "suite=${3:-mint}",
        "suite=${3:-stable}",
        [
            "tests/test_apt_repository.py::test_builder_defaults_to_the_mint_suite_and_pool_layout"
        ],
    ),
    (
        "Release metadata no longer declares the suite",
        "scripts/write-apt-release.py",
        '        f"Suite: {suite}",',
        '        "Suite: stable",',
        ["tests/test_apt_repository.py::test_release_writer_emits_suite_checksums"],
    ),
    (
        "workflow publishes without signing the archive",
        ".github/workflows/build-deb.yml",
        './scripts/sign-apt-repository.sh site "$APT_SUITE"',
        "true",
        ["tests/test_apt_repository.py::test_workflow_signs_and_verifies_before_deploying"],
    ),
    (
        "test doubles report a ready Tcl interpreter",
        "pastex/app.py",
        '        return self.__dict__.get("tk") is not None',
        "        return True",
        ["tests/test_folder_controls.py::test_mock_app_is_not_tcl_ready"],
    ),
    (
        "highlight is lost on refresh",
        "pastex/app.py",
        "        if previous_highlight:\n            self._set_context_highlight(previous_highlight)",
        "        if previous_highlight:\n            pass",
        ["tests/test_folder_controls.py::test_context_highlight_survives_refresh"],
    ),
    (
        "X11 sessions demand /dev/uinput permissions again",
        "pastex/uinput.py",
        '        return self.session_kind != "x11"',
        "        return True",
        ["tests/test_uinput.py::test_x11_session_does_not_need_uinput_permissions"],
    ),
    (
        "the X11 report offers the Wayland permission setup",
        "pastex/app.py",
        "    if not st.needed:\n        return X11_PERMISSION_NOTE",
        "    if False:\n        return X11_PERMISSION_NOTE",
        ["tests/test_v150_permissions.py::test_permissions_report_on_x11_says_not_needed"],
    ),
    (
        "postinst configures the group and the ydotool unit again",
        "debian/postinst",
        '            say "nothing to configure: the X11 clipboard and xdotool need no permissions."',
        '            usermod -aG pastex "$(id -un)"\n'
        '            say "nothing to configure: the X11 clipboard and xdotool need no permissions."',
        ["tests/test_static.py::test_postinst_does_not_touch_accounts_or_services"],
    ),
    (
        "the udev rule is installed in the active directory again",
        "debian/rules",
        "\tinstall -m 644 usr/lib/pastex/70-pastex-uinput.rules debian/pastex/usr/lib/pastex/",
        "\tinstall -m 644 usr/lib/pastex/70-pastex-uinput.rules "
        "debian/pastex/usr/lib/udev/rules.d/",
        ["tests/test_static.py::test_wayland_helpers_are_shipped_as_inert_templates"],
    ),
    (
        "Wayland-only tools are recommended on an X11-first package",
        "debian/control",
        "Recommends: xclip, xsel, xdotool",
        "Recommends: xclip, xsel, xdotool, wl-clipboard",
        [
            "tests/test_static.py::test_x11_tools_are_recommended_and_wayland_tools_only_suggested"
        ],
    ),
    (
        "build-deb lets the shell expand the control description",
        "scripts/build-deb.sh",
        'cat > "$STAGE/DEBIAN/control" <<\'EOF\'',
        'cat > "$STAGE/DEBIAN/control" <<EOF',
        ["tests/test_static.py::test_build_deb_control_heredoc_is_quoted"],
    ),
    (
        "the description quotes the command with backticks again",
        "scripts/build-deb.sh",
        " templates under /usr/lib/pastex, and only pastex --enable-permissions",
        " templates under /usr/lib/pastex, and only `pastex --enable-permissions`",
        ["tests/test_static.py::test_rendered_control_keeps_the_description_text"],
    ),
    (
        "the give-up path leaves a late-minimized window hidden",
        "pastex/app.py",
        "            # instead of leaving it minimized, as 1.10.x did.\n"
        "            PastexApp._restore_window(self)",
        "            # instead of leaving it minimized, as 1.10.x did.\n"
        "            pass",
        ["tests/test_app.py::test_the_window_is_restored_when_the_hide_check_gives_up"],
    ),
    (
        "the restore request is never repeated",
        "pastex/app.py",
        "        if waited >= RESTORE_ACTIVATE_AT_MS and not self.__dict__.get(",
        "        if False and not self.__dict__.get(",
        ["tests/test_app.py::test_the_window_is_reactivated_when_deiconify_is_dropped"],
    ),
    (
        "a late copy result overwrites a running paste",
        "pastex/app.py",
        '        if self.__dict__.get("_paste_started", False):',
        "        if False:",
        ["tests/test_app.py::test_a_late_copy_result_does_not_overwrite_a_running_paste"],
    ),
    (
        "a picker paste waits for the main window to hide",
        "pastex/app.py",
        '        if self.__dict__.get("_paste_hide_widget") is not None:\n            return True\n',
        "",
        ["tests/test_app.py::test_a_picker_paste_does_not_wait_for_the_hidden_main_window"],
    ),
    (
        "a maximized window is never unzoomed before the paste",
        "pastex/app.py",
        '                log_error("[paste] unmaximizing before the paste; the zoom is restored")\n                self.state("normal")',
        '                log_error("[paste] unmaximizing before the paste; the zoom is restored")',
        ["tests/test_app.py::test_a_zoomed_window_is_unzoomed_before_the_minimize"],
    ),
    (
        "the zoom is not restored with the window",
        "pastex/app.py",
        '        if self.__dict__.pop("_paste_was_zoomed", False):',
        '        if False:',
        ["tests/test_app.py::test_the_zoom_comes_back_with_the_window"],
    ),
    (
        "the window manager is never asked to minimize",
        "pastex/app.py",
        "            PastexApp._force_minimize(self)",
        "            pass",
        ["tests/test_app.py::test_the_window_manager_is_asked_when_iconify_is_ignored"],
    ),
    (
        "the x11 check uses the wrong window id",
        "pastex/app.py",
        "            return hex(int(self.winfo_id()))",
        "            return hex(int(self.winfo_id()) + 1)",
        ["tests/test_app.py::test_the_window_manager_is_asked_when_iconify_is_ignored"],
    ),
    (
        "a window without an x11 id still runs the paste",
        "pastex/app.py",
        "        window_id = PastexApp._window_id(self)\n        if window_id is None:\n            return\n        _run_in_thread(",
        "        window_id = PastexApp._window_id(self)\n        if window_id is None:\n            window_id = '0x0'\n        _run_in_thread(",
        ["tests/test_app.py::test_a_window_without_an_x11_id_still_gives_up_quietly"],
    ),
    (
        "the watcher gives up when inotify is unavailable",
        "pastex/watcher.py",
        "        if self._fd < 0:\n            self.poll_only = True",
        "        if self._fd < 0:\n            return False\n            self.poll_only = True",
        ["tests/test_watcher.py::test_start_falls_back_to_polling_without_inotify"],
    ),
    (
        "polling reports a change on every scan",
        "pastex/watcher.py",
        "        if signature == self._signature:\n            return False",
        "        if False:\n            return False",
        ["tests/test_watcher.py::test_polling_reports_a_change_once"],
    ),
    (
        "polling forgets the baseline and cries wolf on the first scan",
        "pastex/watcher.py",
        "        if self._signature is None:\n            # First scan: record the baseline instead of crying wolf.\n            self._signature = signature\n            return False",
        "        if self._signature is None:\n            self._signature = signature\n            return True",
        ["tests/test_watcher.py::test_polling_ignores_a_missing_root"],
    ),
    (
        "a missing window manager is reported as present",
        "pastex/clipboard.py",
        '    if "not found" in out or "no such atom" in out.lower():\n        return None',
        "    if False:\n        return None",
        ["tests/test_clipboard.py::test_window_manager_reports_none_without_a_wm"],
    ),
    (
        "the x server answer is read without checking the map state",
        "pastex/clipboard.py",
        '    if "Map State: IsUnMapped" in out:\n        return True',
        "    if True:\n        return True",
        ["tests/test_clipboard.py::test_x11_window_unmapped_reads_the_x_server_answer"],
    ),
    (
        "a window that still has the focus counts as hidden",
        "pastex/app.py",
        "        elif not moved:\n            return",
        "        elif False:\n            return",
        ["tests/test_app.py::test_a_focus_that_still_belongs_to_pastex_blocks_the_paste"],
    ),
    (
        "the focus verdict never marks the window hidden",
        "pastex/app.py",
        "        self._window_unmapped = True\n        self._hide_signal = signal",
        "        self._window_unmapped = False\n        self._hide_signal = signal",
        [
            "tests/test_app.py::test_a_focus_that_moved_away_is_enough_to_paste",
            "tests/test_app.py::test_the_wm_state_is_used_when_the_focus_cannot_be_read",
        ],
    ),
    (
        "the wm state fallback never accepts an iconic window",
        "pastex/app.py",
        '            if x11_wm_state(window_id) != "iconic" and not x11_window_unmapped(',
        "            if True and not x11_window_unmapped(",
        ["tests/test_app.py::test_the_wm_state_is_used_when_the_focus_cannot_be_read"],
    ),
    (
        "a decided hide is queried again on every poll",
        "pastex/app.py",
        '        if self.__dict__.get("_window_unmapped", False):\n            return\n        moved = x11_focus_moved',
        "        if False:\n            return\n        moved = x11_focus_moved",
        ["tests/test_app.py::test_a_second_focus_verdict_is_not_repeated"],
    ),
    (
        "the focus is checked once instead of until the window manager answers",
        "pastex/app.py",
        '        checked = self.__dict__.get("_x11_checked_ms")\n        if waited >= MINIMIZE_FOCUS_CHECK_AT_MS and (\n            checked is None or waited - checked >= MINIMIZE_FOCUS_RETRY_MS\n        ):',
        '        if waited >= MINIMIZE_FOCUS_CHECK_AT_MS and not self.__dict__.get("_x11_hide_checked"):',
        ["tests/test_app.py::test_the_window_manager_is_asked_when_iconify_is_ignored"],
    ),
    (
        "our own window is treated as another window",
        "pastex/clipboard.py",
        "    if own is not None and active == own:\n        return False",
        "    if False:\n        return False",
        ["tests/test_clipboard.py::test_x11_focus_moved_compares_the_window_id"],
    ),
    (
        "our own frame is treated as another window",
        "pastex/clipboard.py",
        "    if wm_class and x11_window_class(active, runner) == wm_class:",
        "    if False:",
        ["tests/test_clipboard.py::test_x11_focus_moved_recognises_our_own_frame"],
    ),
    (
        "a viewable client window counts as a hidden window",
        "pastex/clipboard.py",
        '    if "Map State: IsUnMapped" in out:\n        return True\n    return None',
        '    if "Map State: IsUnMapped" in out:\n        return True\n    return False',
        ["tests/test_clipboard.py::test_x11_window_unmapped_reads_the_x_server_answer"],
    ),
    (
        "the wm state is read without looking at iconic",
        "pastex/clipboard.py",
        '    if "iconic" in out:\n        return "iconic"',
        '    if "iconic" in out:\n        return "normal"',
        ["tests/test_clipboard.py::test_x11_wm_state_reads_the_published_state"],
    ),
    (
        "an empty clipboard is trusted because the tool exited 0",
        "pastex/app.py",
        "            empty = not PastexApp._clipboard_holds(self, text, result)",
        "            empty = False",
        ["tests/test_app.py::test_the_worker_marks_an_empty_clipboard"],
    ),
    (
        "the copy is never verified at all",
        "pastex/app.py",
        "            verdict = verify_clipboard(text)",
        "            verdict = True",
        ["tests/test_app.py::test_an_empty_clipboard_is_reported_even_though_tk_owns_it"],
    ),
    (
        "an empty clipboard is not claimed with tk",
        "pastex/app.py",
        "        if not claimed and PastexApp._claim_the_clipboard(self, text):",
        "        if False:",
        ["tests/test_app.py::test_a_clipboard_that_stays_empty_blocks_the_paste"],
    ),
    (
        "an unreadable clipboard blocks the paste",
        "pastex/app.py",
        "        if verdict is None:\n            log_error(",
        "        if False:\n            log_error(",
        ["tests/test_app.py::test_an_unreadable_clipboard_keeps_the_tool_result"],
    ),
    (
        "an empty clipboard still gets the press-ctrl-v hint",
        "pastex/app.py",
        "            if clipboard_empty:\n                self._copy_failed(result.message)",
        "            if False:\n                self._copy_failed(result.message)",
        ["tests/test_app.py::test_an_empty_clipboard_does_not_send_ctrl_v"],
    ),
    (
        "a clipboard that cannot be read counts as wrong",
        "pastex/clipboard.py",
        "    back = read_text_from_clipboard(runner=runner)\n    if back is None:\n        return None",
        "    back = read_text_from_clipboard(runner=runner)\n    if back is None:\n        return False",
        ["tests/test_clipboard.py::test_verify_copy_reports_when_it_cannot_read"],
    ),
    (
        "the clipboard is never compared with the text",
        "pastex/clipboard.py",
        "    if _same_text(back, text):\n        return True",
        "    if True:\n        return True",
        ["tests/test_clipboard.py::test_verify_copy_rejects_an_empty_clipboard"],
    ),
    (
        "the empty clipboard is never read a second time",
        "pastex/clipboard.py",
        "    time.sleep(VERIFY_RETRY_SLEEP)",
        "    return False",
        ["tests/test_clipboard.py::test_verify_copy_accepts_a_second_read"],
    ),
    (
        "an empty clipboard cancels the paste instead of typing the text",
        "pastex/app.py",
        "            if empty and can_type_text(text):",
        "            if False:",
        ["tests/test_app.py::test_the_text_is_typed_when_the_clipboard_stays_empty"],
    ),
    (
        "the sender presses ctrl+v even though the text must be typed",
        "pastex/app.py",
        "            if typing:\n                typed_at = time.monotonic()",
        "            if False:\n                typed_at = time.monotonic()",
        ["tests/test_app.py::test_the_sender_types_instead_of_pressing_ctrl_v"],
    ),
    (
        "the sender types even when the clipboard was verified",
        "pastex/app.py",
        "        typing = bool(self.__dict__.get(\"_paste_by_typing\", False))",
        "        typing = True",
        ["tests/test_app.py::test_the_sender_uses_ctrl_v_when_the_clipboard_was_verified"],
    ),
    (
        "a click inherits the previous typing decision",
        "pastex/app.py",
        "        self._paste_body = text\n        self._paste_by_typing = False",
        "        self._paste_body = text",
        ["tests/test_app.py::test_a_click_resets_the_typing_decision"],
    ),
    (
        "an untypable text is pasted anyway",
        "pastex/app.py",
        "            elif empty:",
        "            elif False:",
        ["tests/test_app.py::test_nothing_is_pasted_when_the_text_cannot_be_typed_either"],
    ),
    (
        "the first empty reader is believed",
        "pastex/clipboard.py",
        '        out = f"{getattr(proc, \'stdout\', \'\') or \'\'}"\n        if out:\n            return out',
        '        out = f"{getattr(proc, \'stdout\', \'\') or \'\'}"\n        if True:\n            return out',
        ["tests/test_clipboard.py::test_read_text_from_clipboard_believes_only_a_non_empty_answer"],
    ),
    (
        "the typed text is passed as an argument instead of a file",
        "pastex/clipboard.py",
        '                "--file",\n                handle.name,',
        "                text,",
        ["tests/test_clipboard.py::test_type_text_x11_types_through_a_file"],
    ),
    (
        "typing is allowed without xdotool",
        "pastex/clipboard.py",
        '    return bool(text) and len(text) <= TYPE_TEXT_LIMIT and shutil.which("xdotool") is not None',
        "    return bool(text)",
        ["tests/test_clipboard.py::test_can_type_text_limits"],
    ),
    (
        "a failing xdotool type is reported as success",
        "pastex/clipboard.py",
        '    if getattr(proc, "returncode", 1) != 0:\n        detail = ',
        "    if False:\n        detail = ",
        ["tests/test_clipboard.py::test_type_text_x11_reports_a_failing_xdotool"],
    ),
    (
        "the restore stops as soon as the state says normal",
        "pastex/app.py",
        '        if state not in {"iconic", "withdrawn"} and waited > RESTORE_REPEAT_MS:',
        '        if state not in {"iconic", "withdrawn"}:',
        [
            "tests/test_app.py::test_the_restore_keeps_asking_even_when_the_state_says_normal",
        ],
    ),
    (
        "the window manager is never asked to show the window again",
        "pastex/app.py",
        "            PastexApp._activate_the_window(self)",
        "            pass",
        ["tests/test_app.py::test_the_window_is_reactivated_when_deiconify_is_dropped"],
    ),
    (
        "the clipboard is claimed only after a failed read",
        "pastex/app.py",
        "        claimed = PastexApp._claim_the_clipboard(self, text)",
        "        claimed = False",
        ["tests/test_app.py::test_the_clipboard_is_claimed_before_it_is_read"],
    ),
    (
        "the window is raised instead of activated",
        "pastex/clipboard.py",
        '            ["xdotool", "windowactivate", "--sync", str(window_id)],',
        '            ["xdotool", "windowraise", str(window_id)],',
        ["tests/test_clipboard.py::test_activate_window_asks_the_window_manager"],
    ),
    (
        "the clipboard is re-checked on every click after it failed once",
        "pastex/app.py",
        '        if self.__dict__.get("_clipboard_unreliable", False):',
        "        if False:",
        ["tests/test_app.py::test_the_clipboard_is_not_checked_again_once_it_proved_unusable"],
    ),
    (
        "the paste log loses the time since the click",
        "pastex/app.py",
        '        if started is None:\n            return "+? ms"\n        return f"+{(time.monotonic() - started) * 1000:.0f} ms"',
        '        return "+0 ms"',
        ["tests/test_app.py::test_every_paste_log_carries_the_time_since_the_click"],
    ),
    (
        "typing is cut short by the paste deadline",
        "pastex/app.py",
        "            # own timeout, so the paste deadline must not fire mid-sentence.\n            PastexApp._cancel_paste_watchdog(self)",
        "            # own timeout, so the paste deadline must not fire mid-sentence.\n            pass",
        ["tests/test_app.py::test_typing_is_not_cut_short_by_the_paste_deadline"],
    ),
    (
        "a slow tree refresh is not reported",
        "pastex/app.py",
        "            if elapsed_ms >= 300:",
        "            if False:",
        ["tests/test_app.py::test_a_slow_tree_refresh_is_logged"],
    ),
    (
        "the typing timeout does not grow with the text",
        "pastex/clipboard.py",
        "            max(3.0, min(60.0, len(text) * 0.006 + 2)),",
        "            2.0,",
        ["tests/test_clipboard.py::test_the_typing_timeout_grows_with_the_text"],
    ),
    (
        "the deploy job tries to publish from a branch",
        ".github/workflows/build-deb.yml",
        "    if: github.ref == 'refs/heads/main'\n",
        "",
        ["tests/test_static.py::test_the_deploy_job_only_runs_on_main"],
    ),
    (
        "the click minimizes with tk instead of the window manager",
        "pastex/app.py",
        '        if window_id is not None and _xdotool_available():',
        "        if False:",
        ["tests/test_app.py::test_the_window_is_minimized_through_the_window_manager"],
    ),
    (
        "the restore uses deiconify() even though the wm minimized the window",
        "pastex/app.py",
        '        if self.__dict__.get("_paste_hide_via_x11", False):',
        "        if False:",
        ["tests/test_app.py::test_the_window_is_shown_through_the_window_manager"],
    ),
    (
        "the Tk restore is skipped although the wm was never asked",
        "pastex/app.py",
        '        if self.__dict__.get("_paste_hide_via_x11", False):',
        "        if True:",
        ["tests/test_app.py::test_the_tk_restore_is_used_when_the_window_manager_was_not_asked"],
    ),
    (
        "xdotool is assumed present without checking",
        "pastex/app.py",
        "        if window_id is not None and _xdotool_available():",
        "        if window_id is not None:",
        ["tests/test_app.py::test_a_click_without_xdotool_falls_back_to_tk"],
    ),
    (
        "the wm restore is not verified",
        "pastex/clipboard.py",
        "    mapped = _ask_the_window_manager(window_id, \"windowmap\", runner)",
        "    mapped = False",
        ["tests/test_clipboard.py::test_restore_window_asks_the_window_manager"],
    ),
    (
        "the rehide iconifies through tk after the wm minimized the window",
        "pastex/app.py",
        '            if self.__dict__.get("_paste_hide_via_x11", False):\n                # iconify() here cost two seconds',
        '            if False:\n                # iconify() here cost two seconds',
        ["tests/test_app.py::test_the_rehide_does_not_call_tk_iconify"],
    ),
    (
        "the rehide never iconifies without the window manager",
        "pastex/app.py",
        '            else:\n                try:\n                    self.iconify()',
        '            else:\n                try:\n                    pass',
        ["tests/test_app.py::test_the_rehide_still_iconifies_without_the_window_manager"],
    ),
    (
        "the tk restore runs even after the wm showed the window",
        "pastex/app.py",
        "        if ok:\n            return",
        "        if False:\n            return",
        ["tests/test_app.py::test_the_tk_restore_is_skipped_when_the_window_manager_shows_the_window"],
    ),
    (
        "no tk fallback when the window manager refuses to show the window",
        "pastex/app.py",
        "            self.after(0, lambda app=self: PastexApp._restore_window_via_tk(app))",
        "            pass",
        ["tests/test_app.py::test_the_tk_restore_runs_when_the_window_manager_refuses"],
    ),
    (
        "the restore waits for the focus with --sync again",
        "pastex/clipboard.py",
        '            runner, ["xdotool", verb, str(window_id)], RESTORE_TIMEOUT',
        '            runner, ["xdotool", verb, "--sync", str(window_id)], RESTORE_TIMEOUT',
        ["tests/test_clipboard.py::test_restore_window_never_waits_for_the_focus"],
    ),
    (
        "a hidden window is reported as shown",
        "pastex/clipboard.py",
        '    return "_NET_WM_STATE_HIDDEN" not in out',
        "    return True",
        ["tests/test_clipboard.py::test_window_is_shown_reads_the_ewmh_state"],
    ),
    (
        "the restore stops polling before the window is shown",
        "pastex/clipboard.py",
        "        if attempt + 1 < polls:\n            sleep(RESTORE_POLL_S)",
        "        if False:\n            sleep(RESTORE_POLL_S)",
        ["tests/test_clipboard.py::test_restore_window_keeps_polling_a_hidden_window"],
    ),
]


def run(tests):
    # Never reuse bytecode: a cached .pyc whose (mtime, size) header happened to
    # match a mutated file made a real regression look like a passing test.
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    proc = subprocess.run(
        [PYTHON, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider", *tests],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
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
