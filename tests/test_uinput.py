"""/dev/uinput permission logic (pastex group) — without touching the system."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pastex import uinput


class _FakeGrpEntry:
    def __init__(self, name):
        self.gr_name = name


def test_user_groups_reads_nss(monkeypatch):
    monkeypatch.setattr(
        uinput.pwd, "getpwnam", lambda name: type("P", (), {"pw_gid": 1000})()
    )
    monkeypatch.setattr(uinput.os, "getgrouplist", lambda name, gid: [1000, 999])
    import grp

    monkeypatch.setattr(
        grp,
        "getgrgid",
        lambda gid: _FakeGrpEntry({1000: "tester", 999: "pastex"}.get(gid, "x")),
    )
    assert uinput.user_groups("tester") == {"tester", "pastex"}


def test_user_groups_unknown_user(monkeypatch):
    monkeypatch.setattr(
        uinput.pwd, "getpwnam", lambda name: (_ for _ in ()).throw(KeyError(name))
    )
    assert uinput.user_groups("ghost") == set()


def test_device_writable_uses_access():
    assert uinput.device_writable(access=lambda p, mode: True) is True
    assert uinput.device_writable(access=lambda p, mode: False) is False


def _helper_check(monkeypatch, path, mode, uid=0):
    monkeypatch.setattr(uinput, "HELPER", str(path))
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(mode)
    real_stat = os.stat

    def fake_stat(target, *a, **k):
        st = real_stat(target, *a, **k)
        if os.fspath(target) == os.fspath(path):

            class S:
                st_uid = uid
                st_mode = st.st_mode

            return S()
        return st

    monkeypatch.setattr(uinput.os, "stat", fake_stat)
    return uinput.helper_available()


def test_helper_requires_root_owner(tmp_path, monkeypatch):
    helper = tmp_path / "helper"
    assert _helper_check(monkeypatch, helper, 0o755, uid=0) is True
    assert _helper_check(monkeypatch, helper, 0o755, uid=1000) is False


def test_helper_rejects_world_writable(tmp_path, monkeypatch):
    helper = tmp_path / "helper"
    assert _helper_check(monkeypatch, helper, 0o777) is False
    assert _helper_check(monkeypatch, helper, 0o775) is False


def test_helper_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(uinput, "HELPER", str(tmp_path / "absent"))
    assert uinput.helper_available() is False


def _status(**kw):
    base = {
        "user": "tester",
        "group_exists": True,
        "in_group": False,
        "device_writable": False,
        "helper": True,
        "pkexec": True,
        "ydotoold_binary": False,
        "socket": None,
        "groups": {"tester"},
        "session": {"tester"},
        "daemon": False,
        "rule": True,
    }
    base.update(kw)
    return uinput.UinputStatus(**base)


def test_ready_when_device_writable():
    assert _status(device_writable=True, in_group=True).ready is True


def test_ready_via_ydotoold_socket():
    assert _status(ydotoold_binary=True, socket="/run/ydotoold.sock").ready is True
    assert _status(ydotoold_binary=True, socket=None).ready is False


def test_needs_relogin():
    assert _status(in_group=True).needs_relogin is True
    assert _status(in_group=False).needs_relogin is False


def test_can_self_enable_requires_helper_and_pkexec(monkeypatch):
    monkeypatch.setattr(uinput, "status", lambda user=None: _status())
    assert uinput.can_self_enable() is True
    monkeypatch.setattr(uinput, "status", lambda user=None: _status(helper=False))
    assert uinput.can_self_enable() is False
    monkeypatch.setattr(
        uinput, "status", lambda user=None: _status(device_writable=True)
    )
    assert uinput.can_self_enable() is False


def test_explain_mentions_group_and_relogin():
    assert "pastex" in uinput.explain(_status())
    assert "Log out and back in" in uinput.explain(_status(in_group=True))
    assert "ydotoold" in uinput.explain(_status(ydotoold_binary=True, socket=None))
    assert "ready" in uinput.explain(_status(device_writable=True)).lower()


def test_summary_marks_missing_permission():
    rows = _status().summary()
    labels = " ".join(label for _m, label, _d in rows)
    assert "pastex" in labels
    # Not a member and the device is not writable => both ❌.
    assert all(mark != "✅" for mark, _l, _d in rows)

    ok = _status(in_group=True, device_writable=True).summary()
    assert all(mark == "✅" for mark, _l, _d in ok)


def test_summary_reports_stale_session_after_group_add():
    """Real case: a member on disk, but the running session has not loaded the group."""
    st = _status(in_group=True, session={"tester"})
    assert st.needs_relogin is True
    assert st.in_session is False
    row = next(r for r in st.summary() if "group" in r[1])
    assert "session has not loaded" in row[2]
    assert row[0] == "❌"


def test_session_groups_active_when_loaded():
    st = _status(in_group=True, session={"tester", "pastex"}, device_writable=True)
    assert st.in_session is True
    assert st.needs_relogin is False


def test_ready_requires_daemon_when_ydotool_installed():
    # The device is writable but ydotool needs ydotoold → not ready yet.
    assert _status(device_writable=True, ydotoold_binary=True).ready is False
    assert (
        _status(device_writable=True, ydotoold_binary=True, daemon=True).ready is True
    )
    # Without ydotool (X11 + xdotool, say) the device permission is enough.
    assert _status(device_writable=True).ready is True


def test_daemon_missing_flag():
    assert _status(ydotoold_binary=True).daemon_missing is True
    assert _status(ydotoold_binary=True, daemon=True).daemon_missing is False
    assert _status().daemon_missing is False


def test_explain_orders_relogin_before_daemon():
    text = uinput.explain(
        _status(in_group=True, session={"tester"}, ydotoold_binary=True)
    )
    assert "Log out and back in" in text
    assert "ydotoold" in text
    assert text.index("Log out and back in") < text.index("ydotoold")


def test_explain_mentions_udev_when_rule_missing():
    text = uinput.explain(
        _status(in_group=True, session={"tester", "pastex"}, rule=False)
    )
    # The rule ships as a template, so activation is `--enable-permissions`,
    # never "reinstall the package".
    assert "--enable-permissions" in text
    assert "reinstall" not in text


def test_x11_session_does_not_need_uinput_permissions():
    """Linux Mint's default X11 session types through xdotool XTEST."""
    st = _status(session_kind="x11")
    assert st.needed is False
    assert st.ready is True
    rows = st.summary()
    assert len(rows) == 1
    assert rows[0][0] == "✅"
    assert "not needed" in rows[0][2]
    assert uinput.GROUP not in rows[0][1]


def test_x11_explain_points_at_xdotool_not_permissions():
    text = uinput.explain(_status(session_kind="x11"))
    assert "X11" in text and "xdotool" in text
    assert "--enable-permissions" not in text


def test_wayland_session_still_needs_uinput_permissions():
    st = _status(session_kind="wayland")
    assert st.needed is True
    labels = " ".join(label for _m, label, _d in st.summary())
    assert uinput.GROUP in labels
    assert uinput.DEVICE in labels


def test_unknown_session_keeps_the_wayland_behaviour():
    """No display at all (CI, ssh): keep reporting the permission rows."""
    st = _status(session_kind="unknown")
    assert st.needed is True
    assert len(st.summary()) >= 2


def _proc_file(tmp_path, groups_line):
    f = tmp_path / "status"
    f.write_text(f"Name:\ttester\nGroups:\t{groups_line}\n", encoding="utf-8")
    return str(f)


def test_proc_groups_parsed(tmp_path):
    import grp as real_grp

    gid = real_grp.getgrnam("root").gr_gid
    path = _proc_file(tmp_path, f"{gid} 0")
    assert uinput._proc_groups(path=path) == {"root"}


def test_proc_groups_missing(tmp_path):
    assert uinput._proc_groups(path=str(tmp_path / "nope")) is None


def test_daemon_command_and_start(tmp_path):
    assert uinput.daemon_command() == ["systemctl", "--user", "start", "ydotoold"]
    seen = []

    def runner(cmd, **kw):
        seen.append(cmd)
        return _Proc(0, "", "")

    code, out, err = uinput.start_daemon(runner=runner)
    assert (code, out, err) == (0, "", "")
    assert seen == [["systemctl", "--user", "start", "ydotoold"]]


def test_start_daemon_handles_failure():
    code, _o, err = uinput.start_daemon(
        runner=lambda cmd, **kw: _Proc(5, "", "Unit ydotoold.service not found")
    )
    assert code == 5 and "not found" in err


def test_manual_command_is_complete():
    cmd = uinput.MANUAL_COMMAND
    assert "groupadd" in cmd
    assert "uinput" in cmd
    assert "usermod -aG pastex" in cmd


def test_enable_command_builds_pkexec_call():
    assert uinput.enable_command("tester") == ["pkexec", uinput.HELPER, "tester"]


class _Proc:
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def test_enable_for_user_success():
    seen = []

    def runner(cmd, **kw):
        seen.append(cmd)
        return _Proc(0, "Account 'tester' was added to group 'pastex'.", "")

    code, out, err = uinput.enable_for_user("tester", runner=runner)
    assert code == 0 and "was added" in out and err == ""
    assert seen == [["pkexec", uinput.HELPER, "tester"]]


def test_enable_for_user_cancelled():
    code, out, _err = uinput.enable_for_user(
        "tester", runner=lambda cmd, **kw: _Proc(126, "", "dismissed")
    )
    assert code == 126 and out == ""


def test_enable_for_user_handles_exception():
    def boom(cmd, **kw):
        raise OSError("pkexec not found")

    code, _out, err = uinput.enable_for_user("tester", runner=boom)
    assert code == -1 and "pkexec" in err


def test_socket_candidates_prefers_xdg_runtime(monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")
    cands = uinput.socket_candidates()
    assert cands[0] == "/run/user/1000/ydotool.sock"
    assert "/run/ydotoold.sock" in cands


def test_socket_candidates_without_xdg(monkeypatch):
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    cands = uinput.socket_candidates()
    assert cands[0] == f"/run/user/{os.getuid()}/ydotool.sock"


def test_acl_granted_parses_getfacl(monkeypatch):
    import subprocess as sp

    uid = os.getuid()

    def fake_run(cmd, **kw):
        return sp.CompletedProcess(
            cmd,
            0,
            stdout=(
                "# file: dev/uinput\n"
                "user::rw-\n"
                f"user:{uid}:rw-\t\t# effective: rw-\n"
                "group::---\n"
            ),
        )

    monkeypatch.setattr(sp, "run", fake_run)
    assert uinput.acl_granted() is True


def test_acl_granted_read_only(monkeypatch):
    import subprocess as sp

    uid = os.getuid()
    monkeypatch.setattr(
        sp,
        "run",
        lambda cmd, **kw: sp.CompletedProcess(
            cmd, 0, stdout=f"user::rw-\nuser:{uid}:r--\n"
        ),
    )
    assert uinput.acl_granted() is False


def test_acl_granted_without_getfacl(monkeypatch):
    def boom(cmd, **kw):
        raise OSError("no getfacl")

    import subprocess as sp

    monkeypatch.setattr(sp, "run", boom)
    assert uinput.acl_granted() is False


def test_summary_reports_acl_without_group():
    st = uinput.UinputStatus(
        user="tester",
        group_exists=True,
        in_group=False,
        device_writable=True,
        helper=True,
        pkexec=True,
        ydotoold_binary=False,
        socket=None,
        groups={"tester"},
        session={"tester"},
        daemon=False,
        rule=True,
        acl=True,
    )
    assert st.acl_granted is True
    labels = " ".join(f"{m} {l} {d}" for m, l, d in st.summary())
    assert "ACL" in labels
    assert "no group membership" in labels
