"""Uji logika izin /dev/uinput (grup paste-teks) — tanpa menyentuh sistem."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from paste_teks import uinput


class _FakeGrpEntry:
    def __init__(self, name):
        self.gr_name = name


def test_user_groups_reads_nss(monkeypatch):
    monkeypatch.setattr(uinput.pwd, "getpwnam",
                        lambda name: type("P", (), {"pw_gid": 1000})())
    monkeypatch.setattr(uinput.os, "getgrouplist",
                        lambda name, gid: [1000, 999])
    import grp
    monkeypatch.setattr(
        grp, "getgrgid",
        lambda gid: _FakeGrpEntry({1000: "budi", 999: "paste-teks"}.get(gid, "x")))
    assert uinput.user_groups("budi") == {"budi", "paste-teks"}


def test_user_groups_unknown_user(monkeypatch):
    monkeypatch.setattr(uinput.pwd, "getpwnam",
                        lambda name: (_ for _ in ()).throw(KeyError(name)))
    assert uinput.user_groups("hantu") == set()


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
    base = dict(
        user="budi", group_exists=True, in_group=False,
        device_writable=False, helper=True, pkexec=True,
        ydotoold_binary=False, socket=None, groups={"budi"},
    )
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
    monkeypatch.setattr(uinput, "status",
                        lambda user=None: _status(helper=False))
    assert uinput.can_self_enable() is False
    monkeypatch.setattr(uinput, "status",
                        lambda user=None: _status(device_writable=True))
    assert uinput.can_self_enable() is False


def test_explain_mentions_group_and_relogin():
    assert "paste-teks" in uinput.explain(_status())
    assert "login ulang" in uinput.explain(_status(in_group=True))
    assert "ydotoold" in uinput.explain(
        _status(ydotoold_binary=True, socket=None))
    assert "siap" in uinput.explain(_status(device_writable=True)).lower()


def test_summary_marks_missing_permission():
    lines = _status().summary()
    assert any("paste-teks" in line for line in lines)
    # Belum jadi anggota & perangkat tak bisa ditulis => keduanya ❌.
    assert all(not line.startswith("✅") for line in lines)

    ok = _status(in_group=True, device_writable=True).summary()
    assert all(line.startswith("✅") for line in ok)


def test_manual_command_is_complete():
    cmd = uinput.MANUAL_COMMAND
    assert "groupadd" in cmd
    assert "uinput" in cmd
    assert "usermod -aG paste-teks" in cmd


def test_enable_command_builds_pkexec_call():
    assert uinput.enable_command("budi") == ["pkexec", uinput.HELPER, "budi"]


class _Proc:
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def test_enable_for_user_success():
    seen = []

    def runner(cmd, **kw):
        seen.append(cmd)
        return _Proc(0, "Akun 'budi' ditambahkan ke grup 'paste-teks'.", "")

    code, out, err = uinput.enable_for_user("budi", runner=runner)
    assert code == 0 and "ditambahkan" in out and err == ""
    assert seen == [["pkexec", uinput.HELPER, "budi"]]


def test_enable_for_user_cancelled():
    code, out, err = uinput.enable_for_user(
        "budi", runner=lambda cmd, **kw: _Proc(126, "", "dismissed"))
    assert code == 126 and out == ""


def test_enable_for_user_handles_exception():
    def boom(cmd, **kw):
        raise OSError("pkexec tidak ditemukan")

    code, _out, err = uinput.enable_for_user("budi", runner=boom)
    assert code == -1 and "pkexec" in err
