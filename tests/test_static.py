"""Packaging and deployment checks for the browser extension."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


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


def test_browser_is_recommended_and_desktop_tools_are_gone():
    """2.x ships a Chrome extension: `apt install pastex` suggests a browser
    and no longer pulls the desktop clipboard stack."""
    control = (ROOT / "debian" / "control").read_text()
    recommends = _control_recommends(control)
    assert any("google-chrome" in entry or "chromium" in entry for entry in recommends)
    for legacy in ("xclip", "xsel", "xdotool", "wl-clipboard", "ydotool", "python3-tk", "python3-dbus"):
        assert legacy not in control, f"control still mentions {legacy}"


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
    """2.x only registers browser files, so installing the package must not
    add accounts, reload udev or enable services."""
    text = (ROOT / "debian" / "postinst").read_text(encoding="utf-8")
    code = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )
    for forbidden in ("usermod", "groupadd", "udevadm", "systemctl", "chgrp", "chmod"):
        assert forbidden not in code, f"postinst still runs {forbidden}"
    assert "README" in text  # it points at the setup docs instead


def test_extension_payload_is_registered_for_autodetection():
    """debian/rules must ship the unpacked extension, the signed .crx, and
    the external-install JSON for Chrome and Chromium (2.x payload)."""
    rules = (ROOT / "debian" / "rules").read_text(encoding="utf-8")
    for required in (
        "cp -r extension debian/pastex/usr/share/pastex/extension",
        "scripts/pack-crx.py",
        "external_crx",
        "/usr/share/google-chrome/extensions",
        "/usr/share/chromium/extensions",
        "extension/README.md",
    ):
        assert required in rules, f"rules misses {required!r}"
    for legacy in ("usr/bin/pastex", "pastex.desktop", "pastex-uinput-group"):
        assert legacy not in rules, f"rules still ships desktop file {legacy}"


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
        .replace("@VERSION@", "2.0.0")
        .replace("@ARCH@", "all")
    )
    assert "Package: pastex" in rendered
    assert "Version: 2.0.0-1" in rendered
    description = rendered.split("Description:", 1)[1]
    assert "service account" in description
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
