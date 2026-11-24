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


def test_both_packaging_paths_agree_on_relationship_fields():
    """debian/control and build-deb.sh build the same package, so the fields
    that describe it must not drift (the description is checked separately)."""
    control = (ROOT / "debian" / "control").read_text()
    script = (ROOT / "scripts" / "build-deb.sh").read_text()
    body = script.split("<<'EOF'\n", 1)[1].split("\nEOF\n", 1)[0]
    for field in ("Multi-Arch", "Recommends"):
        from_control = _control_field(control, field)
        from_script = _control_field(body, field)
        assert from_control == from_script, (
            f"{field}: control={from_control} build-deb={from_script}"
        )
    assert _control_field(control, "Multi-Arch") == ["foreign"]
    # Pastex names no other package at all: no alias, no conflict, no takeover.
    # The package is self-contained, so there is nothing to relate to.
    for field in ("Provides", "Conflicts", "Replaces", "Breaks"):
        assert _control_field(control, field) == [], f"debian/control still sets {field}"
        assert _control_field(body, field) == [], f"build-deb.sh still sets {field}"


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
    """The one build path must ship the unpacked extension, the signed .crx,
    and the external-install JSON for Chrome and Chromium (2.x payload)."""
    build = (ROOT / "scripts" / "build-deb.sh").read_text(encoding="utf-8")
    for required in (
        "cp -r extension",
        "scripts/pack-crx.py",
        "external_crx",
        "/usr/share/google-chrome/extensions",
        "/usr/share/chromium/extensions",
        "extension/README.md",
    ):
        assert required in build, f"build-deb.sh misses {required!r}"
    for legacy in ("usr/bin/pastex", "pastex.desktop", "pastex-uinput-group"):
        assert legacy not in build, f"build-deb.sh still ships desktop file {legacy}"


def test_there_is_exactly_one_build_path():
    """DEVELOPMENT.md: one name, one path. A debhelper build would be a second
    way to produce the same package, so it stays gone."""
    assert not (ROOT / "debian" / "rules").exists(), (
        "debian/rules is back; scripts/build-deb.sh is the only package build"
    )
    assert not (ROOT / "debian" / "source").exists(), (
        "debian/source/ describes a source package the project does not build"
    )
    control = (ROOT / "debian" / "control").read_text(encoding="utf-8")
    assert "debhelper" not in control, "the control file names a build tool the project does not use"


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
    assert "Google OAuth" in description
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


def test_deploy_runs_on_main_or_manual_dispatch():
    """Pages is unrestricted: main pushes and manual branch dispatch deploy."""
    text = (ROOT / ".github" / "workflows" / "build-deb.yml").read_text(encoding="utf-8")
    deploy = text.split("  deploy:", 1)[1]
    head = deploy.split("steps:", 1)[0]
    assert "github.event_name == 'workflow_dispatch' || github.ref == 'refs/heads/main'" in head


def test_manifest_and_changelog_versions_match():
    """The CI version guard must not be defeated by stale metadata."""
    import json
    import re

    manifest = json.loads((ROOT / "extension" / "manifest.json").read_text())
    changelog = (ROOT / "debian" / "changelog").read_text()
    first = re.search(r"^pastex \(([^)]+)\)", changelog, re.MULTILINE)
    assert first, "debian/changelog has no pastex version header"
    assert first.group(1) == manifest["version"] + "-1"


def test_runtime_package_has_no_python_dependency():
    """The native host is gone, so neither control file may require python3."""
    control = (ROOT / "debian" / "control").read_text()
    assert "Depends: ${misc:Depends}\n" in control
    assert "python3" not in control.split("\n\n", 1)[1]

    script = (ROOT / "scripts" / "build-deb.sh").read_text()
    assert "Depends: python3" not in script


def test_postrm_does_not_delete_system_groups():
    """Cleanup must be limited to files this package created."""
    text = (ROOT / "debian" / "postrm").read_text()
    for forbidden in ("groupdel", "getent", "pastex group", "GROUP="):
        assert forbidden not in text


def test_install_script_is_marked_development_only():
    text = (ROOT / "scripts" / "install.sh").read_text()
    assert "DEVELOPMENT ONLY" in text
    assert "development-only local install" in text


def test_ci_lints_the_package_and_pins_the_build_timestamp():
    """lintian catches the packaging mistakes a hand-rolled build misses, and
    SOURCE_DATE_EPOCH keeps the published checksums reproducible."""
    workflow = (ROOT / ".github" / "workflows" / "build-deb.yml").read_text()
    assert "lintian --fail-on error" in workflow
    lint = workflow.split("- name: Lint the package", 1)
    assert len(lint) == 2, "the workflow does not lint the .deb"
    # It must run before publishing, and must not swallow errors.
    assert lint[1].index("lintian") < workflow.index("Publish verification artifacts")
    assert "exit 1" in lint[1]
    # The build timestamp is pinned by the script, never "now", and CI must not
    # override it with an invented constant.
    script = (ROOT / "scripts" / "build-deb.sh").read_text()
    assert "SOURCE_DATE_EPOCH" in script
    build = workflow.split("- name: Build the Debian package", 1)[1]
    assert "SOURCE_DATE_EPOCH:" not in build.split("run:", 1)[0]


def test_workflow_pins_every_action_to_a_commit():
    """Floating action tags are a supply-chain risk (YouTube pins all of them)."""
    import re

    workflow = (ROOT / ".github" / "workflows" / "build-deb.yml").read_text()
    uses = re.findall(r"^\s*-?\s*uses:\s*([^\s]+)", workflow, re.MULTILINE)
    assert uses, "no actions found in the workflow"
    for action in uses:
        assert "@" in action and re.search(r"@[0-9a-f]{40}$", action), action


def test_tag_build_creates_a_release_with_verification_data():
    """Tags provide an immutable second channel for fingerprint verification."""
    workflow = (ROOT / ".github" / "workflows" / "build-deb.yml").read_text()
    assert "tags: ['v*']" in workflow
    assert "Check version alignment" in workflow
    assert 'tag="${GITHUB_REF#refs/tags/v}"' in workflow
    assert "gh release create" in workflow
    assert "site/archive-keyring.fingerprint.txt site/checksums.txt site/extension-id.txt" in workflow
    assert "site/extension-id.txt" in workflow
    assert "site/checksums.txt" in workflow


# --- deploying a test build to a real computer ----------------------------


def test_test_deploy_script_offers_both_real_situations() -> None:
    """A tester either has the checkout on their machine, or installs from it."""
    text = (ROOT / "scripts" / "test-deploy.sh").read_text(encoding="utf-8")
    for flag in ("--local", "--serve", "--uninstall", "--port", "--output", "--key"):
        assert flag in text, f"test-deploy.sh has no {flag} option"
    assert "http.server" in text, "serve mode does not actually serve anything"
    assert "--bind 0.0.0.0" in text, "serve mode would only answer on loopback"


def test_test_deploy_uses_a_stable_test_key_and_says_so() -> None:
    """A fresh key per build changes the extension id, so Chrome treats every
    test build as a different extension."""
    text = (ROOT / "scripts" / "test-deploy.sh").read_text(encoding="utf-8")
    assert "debian/pastex-test-crx-key.pem" in text
    assert "PASTEX_CRX_KEY=" in text
    assert "not the release key" in text
    assert "*-crx-key.pem" in (ROOT / ".gitignore").read_text(encoding="utf-8")


def test_generated_installer_verifies_the_download_and_cleans_up() -> None:
    """The installer a tester runs must fail closed and leave no apt source."""
    text = (ROOT / "scripts" / "test-deploy.sh").read_text(encoding="utf-8")
    assert "SHA256SUMS" in text
    assert "sha256sum -c" in text
    assert "Checksum mismatch" in text
    assert "Dir::Etc::sourcelist" in text, "install must not depend on other sources"
    assert "cleanup() { rm -f" in text, "the temporary apt source must be removed"


def test_test_deploy_never_claims_to_be_a_release() -> None:
    text = (ROOT / "scripts" / "test-deploy.sh").read_text(encoding="utf-8")
    assert "test path only" in text or "TEST build" in text
    assert "signed release archive" in text


# --- the changelog rules lintian enforces in CI ---------------------------

def _changelog_entries() -> list[tuple[str, str]]:
    """(version, trailer date) for every entry, newest first."""
    import re

    text = (ROOT / "debian" / "changelog").read_text(encoding="utf-8")
    entries = re.findall(
        r"^pastex \(([^)]+)\).*?\n(?:.*?\n)*?^ -- .+?  (.+)$", text, re.MULTILINE
    )
    assert entries, "debian/changelog has no entries"
    return entries


def test_changelog_is_strictly_newest_first() -> None:
    """lintian's latest-changelog-entry-without-new-date fails the CI build.

    Its rule is `first_timestamp - second_timestamp > 0`, so an entry dated the
    same as, or later than, the one above it stops the archive from being
    published. That is exactly what happened when every entry carried
    00:00:00 on the same day.
    """
    import email.utils

    previous = None
    for version, date in _changelog_entries():
        stamp = email.utils.parsedate_to_datetime(date.strip()).timestamp()
        if previous is not None:
            assert previous - stamp > 0, (
                f"changelog entry {version} is not older than the entry above it "
                f"({date.strip()}); lintian --fail-on error would reject the package"
            )
        previous = stamp


def test_changelog_weekdays_match_their_dates() -> None:
    """lintian's debian-changelog-has-wrong-weekday, checked in every entry."""
    import email.utils

    wrong = []
    for version, date in _changelog_entries():
        declared = date.split(",", 1)[0].strip()
        actual = email.utils.parsedate_to_datetime(date.strip()).strftime("%a")
        if declared != actual:
            wrong.append(f"{version}: says {declared}, {date.strip()} is a {actual}")
    assert not wrong, "changelog weekday does not match the date:\n  " + "\n  ".join(wrong)
