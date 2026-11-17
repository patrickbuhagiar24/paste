"""Static tests: catch NameError without running the GUI.

v1.0.3 regression: app.py used `threading` without importing it, so the
Copy/Paste buttons raised NameError on a user machine — while every test
passed, because the GUI path is not exercised without a display.
"""

import ast
import builtins
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "pastex"


def _bound_names(tree: ast.AST) -> set[str]:
    """Every name bound in a module: imports, definitions, assignment targets."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Name,)) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.comprehension):
            for sub in ast.walk(node.target):
                if isinstance(sub, ast.Name):
                    names.add(sub.id)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            names.update(node.names)
    return names


def _used_names(tree: ast.AST) -> set[str]:
    return {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }


def test_no_undefined_names_in_package():
    known_builtins = set(dir(builtins)) | {
        "__file__",
        "__name__",
        "__doc__",
        "__package__",
        "self",
        "cls",
        "annotations",
    }
    problems = {}
    for path in sorted(PKG.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        missing = _used_names(tree) - _bound_names(tree) - known_builtins
        if missing:
            problems[path.name] = sorted(missing)
    assert not problems, f"undefined names: {problems}"


def test_app_module_imports_threading():
    """The Copy/Paste buttons use a background thread."""
    from pastex import app

    assert hasattr(app, "threading")


def test_modules_import_cleanly_without_display(monkeypatch):
    """Every module must import without X/Wayland (so the CLI still works)."""
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    import importlib

    import pastex

    for name in ("app", "clipboard", "snippets", "uinput"):
        importlib.import_module(f"pastex.{name}")
    assert pastex.__version__


# --- Konsistensi metadata paket -------------------------------------------
ROOT = PKG.parent


def _control_field(text: str, field: str) -> list[str]:
    for line in text.splitlines():
        if line.startswith(f"{field}:"):
            return [p.strip() for p in line.split(":", 1)[1].split(",") if p.strip()]
    return []


def _control_recommends(text: str) -> list[str]:
    return _control_field(text, "Recommends")


def test_build_deb_recommends_matches_debian_control():
    """The two metadata sources (control & build-deb.sh) must not diverge."""
    for field in ("Recommends", "Suggests"):
        control = _control_field((ROOT / "debian" / "control").read_text(), field)
        script = _control_field((ROOT / "scripts" / "build-deb.sh").read_text(), field)
        assert control == script, f"{field}: control={control} build-deb={script}"


def test_reader_tools_declared_in_package_metadata():
    """Every clipboard tool the code calls must be suggested as a package, so
    users do not hit a dead feature without any hint."""
    from pastex.clipboard import READER_TOOLS

    control = (ROOT / "debian" / "control").read_text()
    declared = set(_control_recommends(control)) | set(_control_field(control, "Suggests"))
    # command name -> Debian package
    package_of = {
        "wl-paste": "wl-clipboard",
        "wl-copy": "wl-clipboard",
        "xclip": "xclip",
        "xsel": "xsel",
        "xdotool": "xdotool",
        "ydotool": "ydotool",
    }
    needed = {package_of[t] for t in READER_TOOLS if t in package_of}
    needed |= {"wl-clipboard", "ydotool"}  # copy + paste on Wayland
    missing = needed - declared
    assert not missing, f"packages missing from Recommends/Suggests: {sorted(missing)}"


def test_x11_tools_are_recommended_and_wayland_tools_only_suggested():
    """Linux Mint's default session is X11: `apt install pastex` must not pull
    the Wayland-only helpers, which are inert until the user opts in."""
    control = (ROOT / "debian" / "control").read_text()
    recommends = set(_control_recommends(control))
    suggests = set(_control_field(control, "Suggests"))
    assert {"xclip", "xsel", "xdotool"} <= recommends
    assert not ({"wl-clipboard", "ydotool", "python3-dbus"} & recommends)
    assert {"wl-clipboard", "ydotool", "python3-dbus"} <= suggests


def test_docs_do_not_promise_removed_tk_clipboard():
    """The Tk clipboard fallback was removed in v1.4.0; docs must not promise it."""
    for name in ("README.md", "debian/control"):
        text = (ROOT / name).read_text().lower()
        assert "fallback clipboard" not in text, (
            f"{name} still promises a clipboard fallback"
        )


def test_package_description_matches_between_sources():
    """build-deb.sh writes its own description; its claims must not diverge
    from debian/control (v1.4.2 regression: an internal clipboard fallback
    was still in the .deb after being dropped from control)."""

    def description_lines(text: str) -> list[str]:
        lines, capture = [], False
        for line in text.splitlines():
            if line.startswith("Description:"):
                capture = True
                continue
            if capture:
                if line.startswith((" ", "\t")) or not line.strip():
                    if line.strip():
                        lines.append(line.strip())
                else:
                    break
        return lines

    control = description_lines((ROOT / "debian" / "control").read_text())
    script = description_lines((ROOT / "scripts" / "build-deb.sh").read_text())
    assert control, "the control description could not be read"
    assert control == script, (
        "the description in the .deb (build-deb.sh) differs from debian/control:\n"
        f"control   = {control}\nbuild-deb = {script}"
    )


# --- the package changes nothing on the system ----------------------------


def test_postinst_does_not_touch_accounts_or_services():
    """Mint's X11 session needs no group, udev rule or daemon, so installing
    the package must not add accounts, reload udev or enable services."""
    text = (ROOT / "debian" / "postinst").read_text(encoding="utf-8")
    code = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )
    for forbidden in ("usermod", "groupadd", "udevadm", "systemctl", "chgrp", "chmod"):
        assert forbidden not in code, f"postinst still runs {forbidden}"
    assert "--enable-permissions" in text  # it points at the opt-in instead


def test_wayland_helpers_are_shipped_as_inert_templates():
    """The udev rule and the ydotool unit must not land in the active
    directories; only `pastex --enable-permissions` copies them there."""
    rules = (ROOT / "debian" / "rules").read_text(encoding="utf-8")
    # Only the comment may mention the active directories.
    code = "\n".join(
        line for line in rules.splitlines() if not line.lstrip().startswith("#")
    )
    assert "/usr/lib/udev" not in code
    assert "/usr/lib/systemd" not in code
    for template in ("70-pastex-uinput.rules", "ydotoold.service"):
        assert (ROOT / "usr" / "lib" / "pastex" / template).is_file()
        assert f"usr/lib/pastex/{template}" in rules


def test_helper_activates_the_templates_only_when_called():
    from pastex import uinput

    helper = (ROOT / "usr" / "lib" / "pastex" / "pastex-uinput-group").read_text(
        encoding="utf-8"
    )
    assert uinput.RULE_TEMPLATE == "/usr/lib/pastex/70-pastex-uinput.rules"
    assert uinput.UNIT_TEMPLATE == "/usr/lib/pastex/ydotoold.service"
    assert helper.count(uinput.RULE_TEMPLATE) >= 1
    assert uinput.UDEV_RULE in helper  # the template is copied to the active path
    assert uinput.GROUP in helper


# --- the generated control file must be shell-proof -----------------------


def _control_heredoc() -> str:
    """Return the control heredoc body from scripts/build-deb.sh."""
    text = (ROOT / "scripts" / "build-deb.sh").read_text(encoding="utf-8")
    start = text.index('cat > "$STAGE/DEBIAN/control"')
    marker = "<<'EOF'\n"
    body = text.index(marker, start) + len(marker)
    return text[body : text.index("\nEOF\n", body)]


def test_build_deb_control_heredoc_is_quoted():
    """An unquoted heredoc ran the description through the shell in 1.10.0."""
    text = (ROOT / "scripts" / "build-deb.sh").read_text(encoding="utf-8")
    assert 'cat > "$STAGE/DEBIAN/control" <<\'EOF\'' in text
    assert 's|@PKG@|$PKG|g' in text
    assert 's|@VERSION@|$VERSION|g' in text
    assert 's|@ARCH@|$ARCH|g' in text


def test_rendered_control_keeps_the_description_text():
    """Render the control file the way the script does and check the text."""
    body = _control_heredoc()
    rendered = (
        body.replace("@PKG@", "pastex")
        .replace("@VERSION@", "1.10.0")
        .replace("@ARCH@", "all")
    )
    assert "Package: pastex" in rendered
    assert "Version: 1.10.0-1" in rendered
    description = rendered.split("Description:", 1)[1]
    assert "pastex --enable-permissions" in description
    assert "`" not in description
    assert "$" not in description


def test_descriptions_avoid_shell_metacharacters():
    """Neither metadata source may reintroduce backticks or "$"."""
    for name, text in (
        ("debian/control", (ROOT / "debian" / "control").read_text(encoding="utf-8")),
        ("scripts/build-deb.sh", _control_heredoc()),
    ):
        description = text.split("Description:", 1)[1]
        assert "`" not in description, f"{name} uses a backtick"
        assert "$" not in description, f"{name} uses a shell variable"


def test_the_deploy_job_only_runs_on_main():
    """The github-pages environment rejects branch deployments; do not try."""
    text = (ROOT / ".github" / "workflows" / "build-deb.yml").read_text(encoding="utf-8")
    deploy = text.split("  deploy:", 1)[1]
    head = deploy.split("steps:", 1)[0]
    assert "github.ref == 'refs/heads/main'" in head
