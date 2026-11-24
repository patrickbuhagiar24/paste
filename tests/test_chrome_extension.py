"""Pastex for Chrome (2.x): manifest, sources, CRX packaging, apt registration.

The shipped product is extension/, packed into a signed .crx and registered
for auto-detection by Chrome/Chromium through external-install JSON files.
"""

import json
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
EXT = ROOT / "extension"


def _manifest():
    return json.loads((EXT / "manifest.json").read_text(encoding="utf-8"))


# --- manifest -------------------------------------------------------------


def test_manifest_is_valid_mv3_with_matching_version():
    manifest = _manifest()
    assert manifest["manifest_version"] == 3
    assert re.fullmatch(r"\d+\.\d+\.\d+", manifest["version"]), manifest["version"]
    assert manifest["name"] == "Pastex"
    assert "minimum_chrome_version" in manifest


def test_manifest_permissions_are_minimal_and_documented():
    manifest = _manifest()
    assert set(manifest["permissions"]) == {
        "sidePanel",
        "alarms",
        "idle",
        "scripting",
        "activeTab",
        "clipboardWrite",
        "storage",
        "fileSystemAccess",
        # chrome.identity: the sign-in window that issues the Drive token.
        "identity",
    }
    # Broad host permissions enable dynamic registration for user-chosen sites;
    # content scripts themselves are registered only for the allowlist.
    assert manifest["host_permissions"] == ["http://*/*", "https://*/*"]


def test_manifest_entry_points_exist():
    manifest = _manifest()
    assert (EXT / manifest["background"]["service_worker"]).is_file()
    assert (EXT / manifest["side_panel"]["default_path"]).is_file()
    assert "options_page" not in manifest
    assert "content_scripts" not in manifest
    assert (EXT / "sites.js").is_file()
    assert "commands" not in manifest
    assert manifest["action"]["default_title"]


def test_picker_has_no_shortcut_escape_handler_or_context_menu():
    manifest = _manifest()
    assert "commands" not in manifest
    assert "contextMenus" not in manifest["permissions"]
    for name in ("background.js", "content.js", "sidepanel.js"):
        source = (EXT / name).read_text(encoding="utf-8")
        assert '"Escape"' not in source
        assert "Ctrl+Shift+V" not in source
        assert "pastex-toggle-picker" not in source
    assert "pastex-open-panel" in (EXT / "content.js").read_text(encoding="utf-8")


def test_snippet_cache_is_derived_and_mutations_stay_in_drive():
    manifest = _manifest()
    assert "storage" in manifest["permissions"]
    background = (EXT / "background.js").read_text(encoding="utf-8")
    cache = (EXT / "cache.js").read_text(encoding="utf-8")
    assert "chrome.storage.session" in background
    assert "chrome.storage.local" in background
    assert "storage.sync" not in background + cache
    assert "libraryCache.mutate" in background
    assert "PastexDrive.uploadNew" in background and "PastexDrive.updateText" in background
    assert "PastexDrive.trashFile" in background and "PastexDrive.writeIndexFile" in background
    panel = (EXT / "sidepanel.html").read_text(encoding="utf-8")
    assert 'id="btn-insert"' not in panel
    assert 'id="selection-actions"' in panel


def test_bodies_are_cached_by_their_content_hash():
    # The body cache is addressed by the SHA-256 of the body, so an
    # unchanged snippet is never downloaded again.
    assert (EXT / "hash.js").is_file()
    hashing = (EXT / "hash.js").read_text(encoding="utf-8")
    assert "sha256Hex" in hashing
    library = (EXT / "library.js").read_text(encoding="utf-8")
    cache = (EXT / "cache.js").read_text(encoding="utf-8")
    assert "PastexHash" in library or "require(\"./hash.js\")" in library
    assert "sha256Checksum" in library, "Drive reports the checksum of a body"
    assert "hash" in cache and "texts" in cache
    # Every node keeps the signature of the metadata Drive reported for it.
    assert "signatures" in library and "signatures" in cache


def test_the_order_and_the_collapse_state_live_in_one_label():
    drive = (EXT / "drive.js").read_text(encoding="utf-8")
    library = (EXT / "library.js").read_text(encoding="utf-8")
    background = (EXT / "background.js").read_text(encoding="utf-8")
    # The index is one file named by the configuration link, so it is found by
    # its id and never by listing the folder.
    assert "readIndexFile" in drive and "indexId" in drive
    assert "indexFileId" in library and "parseIndex" in library
    assert "PastexDrive.writeIndexFile" in background
    assert "PastexDrive.setOrder" not in background, "the order is not written per item"


def test_the_background_interval_grows_while_nothing_changes():
    background = (EXT / "background.js").read_text(encoding="utf-8")
    assert "IDLE_MAX_PERIOD_MINUTES" in background
    assert "adaptBackoff" in background
    assert "syncPeriodMinutes" in background
    # The idle gate and the alarm stay the only background triggers.
    assert "chrome.idle.queryState" in background
    assert "chrome.alarms.onAlarm.addListener" in background


def test_the_panel_has_one_status_line_and_keeps_the_sync_button():
    panel = (EXT / "sidepanel.html").read_text(encoding="utf-8")
    script = (EXT / "sidepanel.js").read_text(encoding="utf-8")
    # One status line: the permanent connection label and the "checked" line
    # were removed on request, because two more truncated labels pushed the
    # message that matters out of view.
    assert 'id="status-bar"' in panel
    assert 'id="status" aria-live="polite"' in panel
    for gone in ('id="drive-bar"', 'id="drive-dot"', 'id="drive-status"', 'id="last-checked"'):
        assert gone not in panel, gone + " must stay removed"
    assert "lastCheckedAt" not in script
    assert "renderDriveBar" not in script
    assert 'id="btn-sync"' in panel, "the manual sync stays the fallback"
    assert "↻ Sync" in panel, "the button is labelled Sync"
    assert "Refresh" not in panel, "the old label is gone"
    # The create buttons moved to the end of the toolbar.
    toolbar = panel.split('<div class="toolbar"')[1].split("</div>")[0]
    order = re.findall(r'<button id="(btn-[a-z-]+)"', toolbar)
    assert order == ["btn-collapse-all", "btn-select", "btn-sync", "btn-config", "btn-text", "btn-folder"]


def test_background_sync_only_runs_while_the_user_is_away():
    manifest = _manifest()
    assert "alarms" in manifest["permissions"]
    assert "idle" in manifest["permissions"]
    background = (EXT / "background.js").read_text(encoding="utf-8")
    cache = (EXT / "cache.js").read_text(encoding="utf-8")
    # The alarm and idle listeners are registered at the top level so a
    # terminated MV3 worker is woken up again.
    assert "chrome.alarms.onAlarm.addListener" in background
    assert "chrome.idle.onStateChanged.addListener" in background
    assert "chrome.idle.queryState" in background
    # An active user never triggers a background fetch.
    assert 'state === "active"' in background
    # The cache is only rewritten when Drive really reports a difference.
    assert "onlyIfChanged" in cache
    assert "sameLibrary" in cache


def test_manifest_icons_exist_and_match_sizes():
    manifest = _manifest()
    for size, rel in manifest["icons"].items():
        data = (EXT / rel).read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n", rel
        width, height = struct.unpack(">II", data[16:24])
        assert (width, height) == (int(size), int(size)), rel


def test_html_pages_reference_only_shipped_scripts():
    for page in ("sidepanel.html",):
        text = (EXT / page).read_text(encoding="utf-8")
        for src in re.findall(r'<script\s+src="([^"]+)"', text):
            assert "://" not in src, f"{page} loads remote code: {src}"
            assert (EXT / src).is_file(), f"{page} references missing {src}"
        for href in re.findall(r'<link[^>]+href="([^"]+)"', text):
            assert "://" not in href, f"{page} loads remote CSS: {href}"
            assert (EXT / href).is_file(), f"{page} references missing {href}"


def test_no_remote_code_or_eval_in_extension_sources():
    for path in sorted(EXT.glob("*.js")):
        text = path.read_text(encoding="utf-8")
        assert "eval(" not in text, f"{path.name} uses eval"
        assert "new Function(" not in text, f"{path.name} uses new Function"
        assert "http://" not in text, f"{path.name} uses plain http"
    for path in sorted(EXT.glob("*.html")):
        text = path.read_text(encoding="utf-8")
        assert "http://" not in text, f"{path.name} uses plain http"


def test_https_is_only_used_for_google_apis():
    # Configuration now comes from a local folder; the only Google hosts are
    # the Drive API, its token/revoke endpoint and the sign-in page Chrome opens.
    allowed = {"www.googleapis.com", "oauth2.googleapis.com", "accounts.google.com"}
    for path in sorted(EXT.glob("*.js")):
        for host in re.findall(r"https://([^/'\"\s]+)", path.read_text(encoding="utf-8")):
            assert host in allowed, f"{path.name} talks to {host}"


# --- JavaScript tests (node) ----------------------------------------------

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


@needs_node
def test_js_syntax_is_valid():
    for path in sorted(EXT.glob("*.js")) + sorted((EXT / "test").glob("*.js")):
        proc = subprocess.run([NODE, "--check", str(path)], capture_output=True, text=True)
        assert proc.returncode == 0, f"{path.name}: {proc.stderr.strip()}"


@needs_node
def test_js_unit_tests_pass():
    proc = subprocess.run(
        [NODE, str(EXT / "test" / "run.js")], capture_output=True, text=True, cwd=ROOT
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "tests passed" in proc.stdout


# --- CRX packing (scripts/pack-crx.py) -------------------------------------

OPENSSL = shutil.which("openssl")
needs_openssl = pytest.mark.skipif(OPENSSL is None, reason="openssl is not installed")


def _pack(tmp_path, key_path):
    out = tmp_path / "pastex.crx"
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "pack-crx.py"),
         "--key", str(key_path), "--src", str(EXT), "--out", str(out)],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert proc.returncode == 0, proc.stderr
    return out, proc.stdout.strip()


@needs_openssl
def test_pack_crx_builds_a_verifiable_package(tmp_path):
    key = tmp_path / "key.pem"
    subprocess.run([OPENSSL, "genrsa", "-out", str(key), "2048"],
                   check=True, capture_output=True)
    out, ext_id = _pack(tmp_path, key)
    assert re.fullmatch(r"[a-p]{32}", ext_id), ext_id
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "pack-crx.py"), "--verify", str(out)],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == ext_id


@needs_openssl
def test_crx3_header_and_signature_cover_zip(tmp_path):
    import hashlib

    key = tmp_path / "key.pem"
    subprocess.run([OPENSSL, "genrsa", "-out", str(key), "2048"], check=True, capture_output=True)
    out, ext_id = _pack(tmp_path, key)
    data = out.read_bytes()
    header_size = struct.unpack("<I", data[8:12])[0]
    header = data[12:12 + header_size]
    payload = data[12 + header_size:]

    def read_varint(buf, pos):
        value, shift = 0, 0
        while True:
            b = buf[pos]
            pos += 1
            value |= (b & 127) << shift
            if b < 128:
                return value, pos
            shift += 7

    assert header[0] == 0x12  # sha256_with_rsa = field 2
    length, pos = read_varint(header, 1)
    proof = header[pos:pos + length]
    pos += length
    tag, pos = read_varint(header, pos)
    assert tag == (10000 << 3) | 2
    length, pos = read_varint(header, pos)
    signed_header = header[pos:pos + length]
    assert proof[0] == 0x0a
    length, pos = read_varint(proof, 1)
    public_key = proof[pos:pos + length]
    pos += length
    assert proof[pos] == 0x12
    length, pos = read_varint(proof, pos + 1)
    signature = proof[pos:pos + length]
    digest = hashlib.sha256(public_key).digest()[:16]
    assert signed_header == b'\x0a\x10' + digest
    assert ext_id == ''.join(chr(ord('a') + int(c, 16)) for c in digest.hex())
    public = tmp_path / 'pub.der'
    sig = tmp_path / 'sig'
    signed = tmp_path / 'signed'
    public.write_bytes(public_key)
    sig.write_bytes(signature)
    signed.write_bytes(b'CRX3 SignedData\0' + struct.pack('<I', len(signed_header)) + signed_header + payload)
    pem = tmp_path / 'pub.pem'
    subprocess.run([OPENSSL, 'pkey', '-pubin', '-inform', 'DER', '-in', str(public), '-out', str(pem)], check=True, capture_output=True)
    subprocess.run([OPENSSL, 'dgst', '-sha256', '-verify', str(pem), '-signature', str(sig), str(signed)], check=True, capture_output=True)
    tampered = bytearray(data)
    tampered[-1] ^= 1
    out.write_bytes(tampered)
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/pack-crx.py'), '--verify', str(out)], capture_output=True)
    assert result.returncode != 0


@needs_openssl
def test_pack_crx_is_deterministic_per_key(tmp_path):
    key = tmp_path / "key.pem"
    subprocess.run([OPENSSL, "genrsa", "-out", str(key), "2048"],
                   check=True, capture_output=True)
    first, _ = _pack(tmp_path, key)
    second = tmp_path / "second.crx"
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "pack-crx.py"),
         "--key", str(key), "--src", str(EXT), "--out", str(second)],
        check=True, capture_output=True, text=True, cwd=ROOT,
    )
    assert first.read_bytes() == second.read_bytes()


@needs_openssl
def test_pack_crx_detects_tampering(tmp_path):
    key = tmp_path / "key.pem"
    subprocess.run([OPENSSL, "genrsa", "-out", str(key), "2048"],
                   check=True, capture_output=True)
    out, _ = _pack(tmp_path, key)
    data = bytearray(out.read_bytes())
    data[100] ^= 0xFF
    out.write_bytes(bytes(data))
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "pack-crx.py"), "--verify", str(out)],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert proc.returncode != 0


@needs_openssl
def test_pack_crx_excludes_tests_and_docs(tmp_path):
    import zipfile

    key = tmp_path / "key.pem"
    subprocess.run([OPENSSL, "genrsa", "-out", str(key), "2048"],
                   check=True, capture_output=True)
    out, _ = _pack(tmp_path, key)
    data = out.read_bytes()
    header_size = struct.unpack("<I", data[8:12])[0]
    payload = tmp_path / "payload.zip"
    payload.write_bytes(data[12 + header_size:])
    names = zipfile.ZipFile(payload).namelist()
    assert "manifest.json" in names
    assert not any(name.startswith("test/") for name in names)
    assert "README.md" not in names


# --- apt registration (auto-detection by Chrome/Chromium) ------------------


def test_build_deb_takes_the_version_from_the_manifest():
    script = (ROOT / "scripts" / "build-deb.sh").read_text(encoding="utf-8")
    assert "extension/manifest.json" in script
    assert "pastex import __version__" not in script


def test_build_deb_registers_the_crx_for_autodetection():
    script = (ROOT / "scripts" / "build-deb.sh").read_text(encoding="utf-8")
    assert "scripts/pack-crx.py --key" in script
    assert "scripts/pack-crx.py --verify" in script
    assert "PASTEX_CRX_KEY" in script
    assert '"external_crx": "/usr/share/pastex/pastex.crx"' in script
    assert "/usr/share/google-chrome/extensions" in script
    assert "/usr/share/chromium/extensions" in script
    assert '"external_version"' in script


def test_the_build_registers_the_crx_for_autodetection():
    build = (ROOT / "scripts" / "build-deb.sh").read_text(encoding="utf-8")
    assert "scripts/pack-crx.py" in build
    assert "external_crx" in build
    assert "/usr/share/google-chrome/extensions" in build
    assert "/usr/share/chromium/extensions" in build


def test_control_recommends_a_browser():
    control = (ROOT / "debian" / "control").read_text(encoding="utf-8")
    assert "google-chrome-stable" in control
    assert "chromium" in control
    assert "python3-tk" not in control


def test_control_describes_the_extension():
    control = (ROOT / "debian" / "control").read_text(encoding="utf-8")
    assert "Chrome extension" in control
    assert "Google OAuth" in control
    assert "offers to enable" in control


def test_postinst_points_at_the_browser_enable_step():
    postinst = (ROOT / "debian" / "postinst").read_text(encoding="utf-8")
    assert "enable Pastex" in postinst
    assert "README" in postinst


def test_extension_readme_documents_setup_and_signing():
    readme = (EXT / "README.md").read_text(encoding="utf-8")
    for required in (
        "Google OAuth client",
        "Drive API",
        "OAuth consent screen",
        "Authorized redirect URI",
        "chromiumapp.org",
        "client ID",
        "PASTEX_CRX_KEY",
        "external_crx",
        "/usr/share/google-chrome/extensions",
        "ExtensionInstallForcelist",
        "pastex-config-cache",
        "pastex-folder",
        "pastex-index",
        "A file picker opens directly",
        "Configuration is then **cached**",
    ):
        assert required in readme, f"README misses {required!r}"
    # The guide must be complete enough to create the client without guessing.
    for step in ("Enable the Drive API", "Create the OAuth client", "Web application",
                 "Publish the consent screen", "client_id"):
        assert step in readme, f"OAuth guide misses {step!r}"


def test_packaging_never_touches_home_directories():
    """A .deb runs as root and must not create per-user files; only comments
    and postinst notes may mention ~/.config (creation happens on login)."""
    for name in ("scripts/build-deb.sh", "debian/postinst", "debian/postrm"):
        text = (ROOT / name).read_text(encoding="utf-8")
        code = "\n".join(
            line
            for line in text.splitlines()
            if not line.lstrip().startswith(("#", "say ", "echo "))
        )
        assert "/home/" not in code, f"{name} touches /home"
        assert "~/.config" not in code, f"{name} writes to user config"


def test_release_requires_new_crx_secret():
    workflow = (ROOT / ".github" / "workflows" / "build-deb.yml").read_text()
    assert "secrets.PASTEX_CRX_PRIVATE_KEY" in workflow
    assert "secrets.PASTEX_CRX_KEY" not in workflow
    assert 'elif [[ "$GITHUB_REF" == refs/heads/main || "$GITHUB_REF" == refs/tags/v* ]]' in workflow


def test_configuration_comes_only_from_a_single_picked_file():
    for path in list(EXT.glob("*.js")) + [ROOT / "scripts/build-deb.sh", ROOT / "debian/postinst"]:
        text = path.read_text(encoding="utf-8")
        assert "~/.config/pastex" not in text
        assert "pastex-native-host" not in text
    html = (EXT / "sidepanel.html").read_text()
    assert 'id="btn-config"' in html
    assert 'id="config-overlay"' not in html and 'id="choose-manifest"' not in html
    assert 'id="choose-folder"' not in html
    script = (EXT / "sidepanel.js").read_text()
    assert '$("btn-config").addEventListener' in script
    # Configuration is one file, picked in a single-file picker.
    assert "showOpenFilePicker({" in script
    assert "showDirectoryPicker" not in script
    # Neither the panel nor the source may pin a filename.
    assert "pastex-storage" not in script
    assert "config-manifest.js" not in html
    # There is no offscreen configuration reader any more: the worker reads the
    # cache the panel wrote, so it never needs a file handle.
    assert not (EXT / "config-reader.html").exists()
    assert not (EXT / "config-reader.js").exists()
    assert not (EXT / "config-client.js").exists()
    assert "PastexManifest" not in script
    assert "chooseFolder" not in script
    assert "chooseFile" in script
    worker = (EXT / "background.js").read_text()
    assert "PastexConfigCacheReader" in worker
    assert "PastexConfigClient" not in worker
    cache_reader = (EXT / "config-cache-reader.js").read_text()
    assert "pastex-config-cache" in cache_reader
    source = (EXT / "config-source.js").read_text()
    assert "showDirectoryPicker" not in source
    assert ".values()" not in source
    # No folder is scanned anywhere, and the worker never opens a file.
    assert "showOpenFilePicker" not in worker
    assert "showDirectoryPicker" not in worker
    assert 'id="preview-pane"' not in html and 'id="btn-insert"' not in html
    # The picked file is read once, inside the picker gesture, and its parsed
    # value is cached: no IndexedDB layer exists for a handle any more.
    assert not (EXT / "handle-store.js").exists()
    assert not (EXT / "config-import.js").exists()
    assert "indexedDB" not in script
    assert "PastexHandleStore" not in script
    assert "PastexConfigImport" not in script
    assert "nativeMessaging" not in _manifest()["permissions"]


def test_drive_access_is_oauth_and_the_service_account_is_gone():
    """One way of doing one thing: no service account survives anywhere."""
    drive = (EXT / "drive.js").read_text(encoding="utf-8")
    assert "launchWebAuthFlow" in drive, "the token comes from Chrome's sign-in window"
    assert "accounts.google.com/o/oauth2/v2/auth" in drive
    assert "auth/drive.item" in drive, "only the per-item scope is requested"
    assert "drive.file" not in drive, "the old scope is gone"
    assert "auth/drive\"" not in drive and "auth/drive.readonly" not in drive
    # A public client has no secret, and a service account no longer exists.
    for gone in ("private_key", "client_email", "service_account", "jwt", "JWT",
                 "grant-type:jwt-bearer", "client_secret"):
        assert gone not in drive, f"drive.js still mentions {gone!r}"
    for path in sorted(EXT.glob("*.js")):
        text = path.read_text(encoding="utf-8")
        assert "service-account.json" not in text, f"{path.name} still reads a key file"
        assert "parseServiceAccount" not in text, f"{path.name} still parses a key"
    # The configuration folder holds a public client id, never a credential, and
    # it is one file of Drive links.
    source = (EXT / "config-source.js").read_text(encoding="utf-8")
    # The file name is the user's choice; the four keys are what make a file a
    # configuration, so no filename may be required or refused.
    assert "pastex-storage" not in source, "the configuration file name must be free"
    assert "client_id" in source
    assert "pastex-folder" in source and "pastex-index" in source and "pastex-sites" in source
    # No script in the repository signs in a service account any more.
    for script in sorted((ROOT / "scripts").glob("*.sh")):
        assert "jwt-bearer" not in script.read_text(encoding="utf-8"), script.name
    assert not (ROOT / "scripts" / "check-drive-write.sh").exists()
    assert not (ROOT / "scripts" / "get-pastex-label.sh").exists()


def test_drive_tokens_are_never_written_to_storage():
    """The access token lives in memory; Chrome keeps the refresh token."""
    for name in ("drive.js", "library.js", "cache.js", "background.js"):
        text = (EXT / name).read_text(encoding="utf-8")
        for forbidden in ("access_token:", "set({ token", "storage.local.set({ token"):
            assert forbidden not in text, f"{name} persists a token"


def test_sidepanel_has_collapse_all_and_verbatim_insert():
    html = (EXT / "sidepanel.html").read_text(encoding="utf-8")
    script = (EXT / "sidepanel.js").read_text(encoding="utf-8")
    content = (EXT / "content.js").read_text(encoding="utf-8")
    assert 'id="btn-collapse-all"' in html
    assert "<footer" not in html
    assert 'id="status"' in html and html.index('id="status"') < html.index("</header>")
    assert "btn-collapse-all" in script
    assert "(cache)" in script, "a cached read is still marked in the status"
    assert "Session cache: " not in script
    assert "Snippets loaded from session cache." not in script
    # The central index is the only writer of order and collapse state; the
    # per-item labels are read-only history.
    assert "readIndexFile" in (EXT / "drive.js").read_text(encoding="utf-8")
    assert "PastexDrive.setOrder" not in script
    # Textarea/input insertion must use setRangeText, which keeps newlines.
    assert "target.setRangeText(text, start, end, \"end\")" in content
    assert 'typeof node.text === "string" ? node.text : ""' in script


# --- the built .deb is a well-formed Debian package ------------------------
#
# These tests build the package the same way CI does and inspect the result,
# because the guarantees are about the *archive*, not the script's text:
#   - md5sums makes `dpkg -V` able to notice a corrupted file;
#   - Installed-Size lets apt size the install;
#   - changelog.Debian.gz is what `apt changelog pastex` reads;
#   - SOURCE_DATE_EPOCH makes the archive reproducible, so the published
#     checksums.txt can be verified by rebuilding.

DPKG_DEB = shutil.which("dpkg-deb")
needs_dpkg = pytest.mark.skipif(DPKG_DEB is None, reason="dpkg-deb is not installed")


def _build_deb(tmp_path, key, name="out.deb"):
    """Build the package twice from a copy-free checkout; return the .deb path."""
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "SOURCE_DATE_EPOCH": "1700000000",
        "PASTEX_CRX_KEY": str(key),
    }
    version = _manifest()["version"]
    out = ROOT / "dist" / f"pastex_{version}-1_all.deb"
    subprocess.run(["bash", str(ROOT / "scripts" / "build-deb.sh")], cwd=ROOT, env=env,
                   check=True, capture_output=True)
    target = tmp_path / name
    shutil.copy2(out, target)
    return target


def _control(deb, field):
    return subprocess.run([DPKG_DEB, "-f", str(deb), field],
                          capture_output=True, text=True, check=True).stdout.strip()


def _control_member(deb, member):
    """Read one file out of the control area by name."""
    ctrl = subprocess.run([DPKG_DEB, "--ctrl-tarfile", str(deb)],
                          capture_output=True, check=True).stdout
    tar = subprocess.run(["tar", "-xOf", "-", "./" + member], input=ctrl,
                         capture_output=True, check=True)
    return tar.stdout


@needs_dpkg
@needs_openssl
def test_deb_ships_md5sums_that_match_the_payload(tmp_path):
    key = tmp_path / "key.pem"
    subprocess.run([OPENSSL, "genrsa", "-out", str(key), "2048"], check=True,
                   capture_output=True)
    deb = _build_deb(tmp_path, key)
    sums = _control_member(deb, "md5sums").decode()
    assert sums.strip(), "the package has no md5sums"
    # Every line is "<32 hex>  <path>", with no leading "./" (dpkg's format).
    for line in sums.splitlines():
        digest, path = line.split("  ", 1)
        assert re.fullmatch(r"[0-9a-f]{32}", digest), line
        assert not path.startswith("./"), f"md5sums path is not root-relative: {path}"
        assert not path.startswith("DEBIAN/"), f"md5sums covers the control area: {path}"

    # The digests must actually verify against the unpacked payload.
    extract = tmp_path / "root"
    subprocess.run([DPKG_DEB, "-x", str(deb), str(extract)], check=True)
    check = subprocess.run(["md5sum", "-c", "-"], cwd=extract, input=sums,
                           capture_output=True, text=True)
    assert check.returncode == 0, check.stdout + check.stderr
    assert "FAILED" not in check.stdout


@needs_dpkg
@needs_openssl
def test_deb_declares_installed_size_and_multi_arch(tmp_path):
    key = tmp_path / "key.pem"
    subprocess.run([OPENSSL, "genrsa", "-out", str(key), "2048"], check=True,
                   capture_output=True)
    deb = _build_deb(tmp_path, key)
    size = _control(deb, "Installed-Size")
    assert size.isdigit() and int(size) > 0, f"Installed-Size is {size!r}"
    # The payload really is that large (within a KiB per directory).
    extract = tmp_path / "root"
    subprocess.run([DPKG_DEB, "-x", str(deb), str(extract)], check=True)
    actual = int(subprocess.run(["du", "-sk", str(extract)], capture_output=True,
                                text=True, check=True).stdout.split()[0])
    assert abs(int(size) - actual) <= 8, f"declared {size} KiB, payload is {actual} KiB"
    assert _control(deb, "Multi-Arch") == "foreign"
    # Pastex ships under its own name only and names no other package: no
    # alias (Provides), no conflict, no file takeover, no Breaks.
    for field in ("Provides", "Conflicts", "Replaces", "Breaks"):
        assert _control(deb, field) == "", f"the package still sets {field}"


@needs_dpkg
@needs_openssl
def test_deb_ships_the_changelog_apt_changelog_reads(tmp_path):
    key = tmp_path / "key.pem"
    subprocess.run([OPENSSL, "genrsa", "-out", str(key), "2048"], check=True,
                   capture_output=True)
    deb = _build_deb(tmp_path, key)
    listed = subprocess.run([DPKG_DEB, "-c", str(deb)], capture_output=True,
                            text=True, check=True).stdout
    assert "usr/share/doc/pastex/changelog.Debian.gz" in listed
    # It must be the project changelog, not an empty placeholder.
    text = subprocess.run(["sh", "-c",
                           f"dpkg-deb --fsys-tarfile '{deb}' | tar -xOf - "
                           "./usr/share/doc/pastex/changelog.Debian.gz | gzip -dc"],
                          check=True, capture_output=True).stdout.decode()
    assert text.startswith("pastex ("), text[:80]
    assert "urgency=" in text


@needs_dpkg
@needs_openssl
def test_deb_build_is_reproducible(tmp_path):
    """Same checkout + same key + same SOURCE_DATE_EPOCH = same bytes."""
    key = tmp_path / "key.pem"
    subprocess.run([OPENSSL, "genrsa", "-out", str(key), "2048"], check=True,
                   capture_output=True)
    first = _build_deb(tmp_path, key, "first.deb")
    second = _build_deb(tmp_path, key, "second.deb")
    assert first.read_bytes() == second.read_bytes(), (
        "two builds of the same checkout differ; the published checksums could "
        "not be verified by rebuilding"
    )


def test_build_deb_pins_timestamps_and_builds_the_control_summary():
    script = (ROOT / "scripts" / "build-deb.sh").read_text(encoding="utf-8")
    # Reproducibility: the timestamp comes from the environment, never from now.
    assert "SOURCE_DATE_EPOCH" in script
    assert 'git log -1 --format=%ct' in script
    assert 'touch --no-dereference --date="@$SOURCE_DATE_EPOCH"' in script
    assert "--uniform-compression" in script
    # Both generated members are produced, not assumed.
    assert "DEBIAN/md5sums" in script
    assert "changelog.Debian.gz" in script
    assert 'du -sk --exclude=DEBIAN' in script
    # deb822 has no comment syntax: a "#" line would become a bogus field.
    heredoc = script.split('cat > "$STAGE/DEBIAN/control"', 1)[1]
    body = heredoc.split("<<'EOF'\n", 1)[1].split("\nEOF\n", 1)[0]
    for line in body.splitlines():
        assert not line.startswith("#"), f"comment inside the control file: {line!r}"
