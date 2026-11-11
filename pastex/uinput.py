"""Detect and configure /dev/uinput permissions for automatic paste (ydotool).

ydotool sends Ctrl+V by writing events to ``/dev/uinput``. The device is owned
by ``root:input`` with mode 0660, so regular users normally cannot access it.
This is the most common cause of automatic-paste failures.

Rather than adding users to the ``input`` group (which grants access to every
input device and can enable keylogging), Pastex uses a dedicated ``pastex``
group and a udev rule scoped to ``/dev/uinput`` only.

System group membership (``/etc/group``) differs from the groups active in a
running session. If an account is added to the group but the session has not
been restarted, processes still have the old group list. ``session_groups()``
checks the active process groups.
"""
from __future__ import annotations

import os
import pwd
import shutil
import stat
from dataclasses import dataclass, field
from typing import Callable

GROUP = "pastex"
HELPER = "/usr/lib/pastex/pastex-uinput-group"
UDEV_RULE = "/usr/lib/udev/rules.d/70-pastex-uinput.rules"
DEVICE = "/dev/uinput"

# ydotool >= 1.0 communicates with ydotoold through a Unix socket.
# Its standard location is $XDG_RUNTIME_DIR (usually /run/user/<uid>/ydotool.sock).
SOCKET_PATHS = ("/run/ydotoold.sock", "/tmp/ydotool.sock")

MANUAL_COMMAND = (
    "sudo groupadd --system pastex\n"
    "echo 'KERNEL==\"uinput\", SUBSYSTEM==\"misc\", GROUP=\"pastex\", "
    "MODE=\"0660\", OPTIONS+=\"static_node=uinput\"' \\\n"
    "  | sudo tee /usr/lib/udev/rules.d/70-pastex-uinput.rules\n"
    "sudo usermod -aG pastex $USER\n"
    "sudo udevadm control --reload-rules && sudo udevadm trigger --sysname-match=uinput\n"
    "# then log out and back in"
)

# Since 1.5.0, permissions are managed from the CLI rather than a GUI button.
# This text is shown by --permissions, --check, and paste-failure dialogs so users
# know which command to run without searching the documentation.
CLI_HINT = (
    "Run in a terminal:\n"
    "  pastex --permissions            # show permission status\n"
    "  pastex --enable-permissions   # enable (administrator password required)"
)


def current_user() -> str:
    """Return the name of the non-root user running the app."""
    try:
        return pwd.getpwuid(os.getuid()).pw_name
    except KeyError:  # pragma: no cover - sangat jarang
        return os.environ.get("USER") or os.environ.get("LOGNAME") or "unknown"


def user_groups(user: str | None = None, *, grouplist=None) -> set[str]:
    """Return all groups recorded for ``user`` by the system (NSS/LDAP)."""
    import grp

    name = user or current_user()
    try:
        entry = pwd.getpwnam(name)
    except KeyError:
        return set()
    if grouplist is None:
        grouplist = os.getgrouplist
    try:
        gids = grouplist(name, entry.pw_gid)
    except KeyError:
        # The NSS group database is unavailable (for example, while offline).
        gids = [entry.pw_gid]
    names = set()
    for gid in gids:
        try:
            names.add(grp.getgrgid(gid).gr_name)
        except KeyError:
            continue
    return names


def _proc_groups(*, path: str = "/proc/self/status") -> set[str] | None:
    """Return groups for the current process, read from /proc."""
    import grp

    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if not line.startswith("Groups:"):
                    continue
                gids = [int(part) for part in line.split()[1:]]
                names = set()
                for gid in gids:
                    try:
                        names.add(grp.getgrgid(gid).gr_name)
                    except KeyError:
                        continue
                return names
    except (OSError, ValueError):
        return None
    return None


def session_groups() -> set[str] | None:
    """Return groups actually active in this process, not only in /etc/group."""
    return _proc_groups()


def device_writable(*, access=None) -> bool:
    """Return whether this process can write to /dev/uinput."""
    if access is None:
        access = os.access
    try:
        return bool(access(DEVICE, os.W_OK))
    except OSError:  # pragma: no cover
        return False


def socket_candidates() -> list[str]:
    """Return all possible ydotoold socket locations."""
    uid = os.getuid()
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{uid}"
    return [f"{runtime}/ydotool.sock",
            f"{runtime}/.ydotool_socket",
            *SOCKET_PATHS]


def acl_granted(dev: str = DEVICE, *, uid: int | None = None) -> bool:
    """Return whether an ACL grants this user write access to ``dev``.

    ``TAG+="uaccess"`` makes logind add the ``user:<uid>:rw-`` ACL to the
    device. Access can therefore be granted without group membership.
    """
    try:
        import subprocess

        out = subprocess.run(["getfacl", "--absolute-names", "-p", dev],
                             capture_output=True, text=True, timeout=5).stdout
    except (OSError, Exception):
        return False
    want = f"user:{uid if uid is not None else os.getuid()}:"
    for line in out.splitlines():
        line = line.strip()
        if line.startswith(want) and "w" in line.rsplit(":", 1)[-1]:
            return True
    return False


def ydotoold_socket() -> str | None:
    """Return the ydotoold socket path, if available (ydotool >= 1.0)."""
    for path in socket_candidates():
        if os.path.exists(path):
            return path
    return None


def ydotoold_running() -> bool:
    """Return whether the daemon is running, based on its socket or process."""
    if ydotoold_socket() is not None:
        return True
    for pid in ("/proc",):
        try:
            entries = os.listdir(pid)
        except OSError:  # pragma: no cover
            continue
        for entry in entries:
            if not entry.isdigit():
                continue
            try:
                with open(f"/proc/{entry}/comm", encoding="utf-8",
                          errors="replace") as handle:
                    if handle.read().strip() == "ydotoold":
                        return True
            except OSError:
                continue
    return False


def udev_rule_installed(rule: str = UDEV_RULE) -> bool:
    return os.path.exists(rule)


def helper_available() -> bool:
    """Return whether the root helper can safely be called through pkexec.

    Trust only a root-owned file that other users cannot modify, preventing
    privilege escalation through a writable helper.
    """
    try:
        info = os.stat(HELPER)
    except OSError:
        return False
    if info.st_uid != 0:
        return False
    if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        return False
    return os.access(HELPER, os.X_OK)


@dataclass
class UinputStatus:
    user: str
    group_exists: bool
    in_group: bool
    device_writable: bool
    helper: bool
    pkexec: bool
    ydotoold_binary: bool
    socket: str | None
    groups: set[str] = field(default_factory=set)
    session: set[str] | None = None
    daemon: bool = False
    rule: bool = False
    acl: bool = False

    # --- turunan -------------------------------------------------------
    @property
    def in_session(self) -> bool:
        """Return whether the pastex group is active in this session."""
        if self.session is None:
            return self.device_writable
        return GROUP in self.session

    @property
    def account_ready(self) -> bool:
        """Return whether the account is listed in the group, even if the session has not loaded it."""
        return self.in_group or self.in_session

    @property
    def acl_granted(self) -> bool:
        """Return whether access comes from a uaccess ACL, without group membership or logout."""
        return self.device_writable and self.acl

    @property
    def needs_relogin(self) -> bool:
        """Return whether the system group membership is not yet loaded in this session."""
        return self.account_ready and not self.in_session

    @property
    def daemon_ready(self) -> bool:
        return self.daemon or self.socket is not None

    @property
    def ready(self) -> bool:
        """Return whether everything needed to send Ctrl+V is available."""
        if self.device_writable:
            if not self.ydotoold_binary:
                return True
            return self.daemon_ready
        return self.ydotoold_binary and self.daemon_ready

    @property
    def daemon_missing(self) -> bool:
        return self.ydotoold_binary and not self.daemon_ready

    def summary(self, prefix: str = "") -> list[tuple[str, str, str]]:
        """Return (marker, label, detail) rows for display."""
        def state(ok: bool) -> str:
            return "✅" if ok else "❌"

        rows: list[tuple[str, str, str]] = []

        if self.device_writable and self.account_ready:
            group_detail = "member, active in this session"
        elif self.needs_relogin:
            group_detail = "member on the system; this session has not loaded the group yet"
        elif self.in_group or self.in_session:
            group_detail = "member, active in this session"
        elif self.group_exists:
            group_detail = "not a member yet"
        else:
            group_detail = "group does not exist — is the package installed correctly?"

        if self.acl_granted and not self.account_ready:
            rows.append(("✅", f"{prefix}access to {DEVICE}",
                         "active session ACL (no group membership or logout required)"))
        else:
            # If the device is writable, access is active even if /proc
            # cannot be read (container or custom NSS); do not
            # report a false negative.
            group_ok = self.account_ready and (self.in_session
                                               or self.device_writable)
            rows.append((state(group_ok), f"{prefix}group '{GROUP}'",
                         group_detail))

        if self.device_writable:
            device_detail = f"{DEVICE} is writable"
        elif not self.rule:
            device_detail = f"{DEVICE} is not writable — udev rule is missing"
        elif self.needs_relogin:
            device_detail = (f"{DEVICE} is not writable — a new login is required")
        else:
            device_detail = f"{DEVICE} is not writable"
        rows.append((state(self.device_writable), f"{prefix}write access to {DEVICE}",
                     device_detail))

        if self.ydotoold_binary:
            if self.daemon_ready:
                where = self.socket or "ydotoold process"
                rows.append(("✅", f"{prefix}ydotoold daemon",
                             f"running ({where})"))
            else:
                rows.append(("❌", f"{prefix}ydotoold daemon",
                             "not running"))
        return rows


def status(user: str | None = None) -> UinputStatus:
    name = user or current_user()
    groups = user_groups(name)
    try:
        import grp

        grp.getgrnam(GROUP)
        group_exists = True
    except (KeyError, ImportError):
        group_exists = False
    return UinputStatus(
        user=name,
        group_exists=group_exists,
        in_group=GROUP in groups,
        device_writable=device_writable(),
        helper=helper_available(),
        pkexec=shutil.which("pkexec") is not None,
        ydotoold_binary=shutil.which("ydotoold") is not None,
        socket=ydotoold_socket(),
        groups=groups,
        session=session_groups(),
        daemon=ydotoold_running(),
        rule=udev_rule_installed(),
        acl=acl_granted(),
    )


def can_self_enable(st: UinputStatus | None = None) -> bool:
    """Return whether permissions can be enabled through pkexec and the helper."""
    st = st or status()
    return st.helper and st.pkexec and not st.device_writable


def enable_command(user: str | None = None) -> list[str]:
    """Return the pkexec command used to add a user to the group."""
    return ["pkexec", HELPER, user or current_user()]


def enable_for_user(user: str | None = None, *,
                    runner: Callable[..., object] | None = None,
                    timeout: int = 120) -> tuple[int, str, str]:
    """Run the helper through pkexec and return (code, stdout, stderr).

    Exit code 126/127 means the password request was canceled or pkexec is missing.
    """
    if runner is None:
        import subprocess

        def runner(cmd, **kw):
            return subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=timeout, **kw)

    try:
        proc = runner(enable_command(user))
        return (getattr(proc, "returncode", -1),
                (getattr(proc, "stdout", "") or "").strip(),
                (getattr(proc, "stderr", "") or "").strip())
    except (OSError, Exception) as exc:  # SubprocessError & TimeoutExpired
        return -1, "", str(exc)


def daemon_command() -> list[str]:
    return ["systemctl", "--user", "start", "ydotoold"]


def start_daemon(*, runner: Callable[..., object] | None = None,
                 timeout: int = 20) -> tuple[int, str, str]:
    """Start ydotoold through systemd --user and return (code, stdout, stderr)."""
    if runner is None:
        import subprocess

        def runner(cmd, **kw):
            return subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=timeout, **kw)
    try:
        proc = runner(daemon_command())
        return (getattr(proc, "returncode", -1),
                (getattr(proc, "stdout", "") or "").strip(),
                (getattr(proc, "stderr", "") or "").strip())
    except (OSError, Exception) as exc:
        return -1, "", str(exc)


def explain(st: UinputStatus | None = None) -> str:
    """Return the next troubleshooting steps.

    Check session membership first, then the daemon, then group activation.
    If everything is ready, return a positive status message.
    """
    st = st or status()
    if st.ready:
        return "Automatic paste is ready."

    steps: list[str] = []
    if not st.account_ready:
        steps.append(
            f"Account '{st.user}' is not a member of group '{GROUP}'. Run "
            "`pastex --enable-permissions` (or follow the manual steps in the README).")
    if st.needs_relogin:
        steps.append(
            f"Account '{st.user}' is already a member of group '{GROUP}' on the "
            "system, but THIS SESSION has not loaded it. Log out and back in "
            "(or reboot); this step is required.")
    if st.daemon_missing:
        steps.append(
            "ydotoold is not running. Run `systemctl --user start "
            "ydotoold` (or `ydotoold &`) and try again.")
    if st.in_session and not st.device_writable and not st.needs_relogin:
        if not st.rule:
            steps.append(
                "The 'pastex' udev group rule was not found. Reinstall "
                "the package: `sudo apt install --reinstall pastex`.")
        else:
            steps.append(
                f"Permissions for {DEVICE} are not active. Reload the udev rules:\n"
                "sudo udevadm control --reload-rules && "
                "sudo udevadm trigger --sysname-match=uinput")
    if not steps:  # pragma: no cover - jaring pengaman
        steps.append("Run `pastex --check` for details.")
    return "\n".join(f"{i}. {s}" for i, s in enumerate(steps, 1))
