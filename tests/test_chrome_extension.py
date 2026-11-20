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
        "offscreen",
        "storage",
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
    assert "PastexDrive.trashFile" in background and "PastexDrive.setOrder" in background
    panel = (EXT / "sidepanel.html").read_text(encoding="utf-8")
    assert 'id="btn-insert"' not in panel
    assert 'id="selection-actions"' in panel


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
    for page in ("sidepanel.html", "config-reader.html"):
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
    # Drive links are configuration inputs; the links themselves are validated
    # to this host in config-manifest.js before any request is made.
    allowed = {"www.googleapis.com", "oauth2.googleapis.com", "drive.google.com"}
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


def test_debian_rules_registers_the_crx_for_autodetection():
    rules = (ROOT / "debian" / "rules").read_text(encoding="utf-8")
    assert "scripts/pack-crx.py" in rules
    assert "external_crx" in rules
    assert "/usr/share/google-chrome/extensions" in rules
    assert "/usr/share/chromium/extensions" in rules


def test_control_recommends_a_browser():
    control = (ROOT / "debian" / "control").read_text(encoding="utf-8")
    assert "google-chrome-stable" in control
    assert "chromium" in control
    assert "python3-tk" not in control


def test_control_describes_the_extension():
    control = (ROOT / "debian" / "control").read_text(encoding="utf-8")
    assert "Chrome extension" in control
    assert "service account" in control
    assert "offers to enable" in control


def test_postinst_points_at_the_browser_enable_step():
    postinst = (ROOT / "debian" / "postinst").read_text(encoding="utf-8")
    assert "enable Pastex" in postinst
    assert "README" in postinst


def test_extension_readme_documents_setup_and_signing():
    readme = (EXT / "README.md").read_text(encoding="utf-8")
    for required in (
        "service account",
        "Drive API",
        "client_email",
        "PASTEX_CRX_KEY",
        "external_crx",
        "/usr/share/google-chrome/extensions",
        "ExtensionInstallForcelist",
        "IndexedDB",
        "pastex-google-drive.json",
        "service-account.json",
        "The file picker opens directly",
        "only the three download URLs",
    ):
        assert required in readme, f"README misses {required!r}"


def test_packaging_never_touches_home_directories():
    """A .deb runs as root and must not create per-user files; only comments
    and postinst notes may mention ~/.config (creation happens on login)."""
    for name in ("scripts/build-deb.sh", "debian/rules", "debian/postinst", "debian/postrm"):
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


def test_configuration_comes_only_from_a_user_selected_manifest():
    for path in list(EXT.glob("*.js")) + [ROOT / "scripts/build-deb.sh", ROOT / "debian/rules", ROOT / "debian/postinst"]:
        text = path.read_text(encoding="utf-8")
        assert "~/.config/pastex" not in text
        assert "pastex-native-host" not in text
    html = (EXT / "sidepanel.html").read_text()
    assert 'id="btn-config"' in html
    assert 'id="config-overlay"' not in html and 'id="choose-manifest"' not in html
    assert 'id="choose-folder"' not in html
    script = (EXT / "sidepanel.js").read_text()
    assert '$("btn-config").addEventListener' in script
    assert "showOpenFilePicker({" in script
    assert "showDirectoryPicker" not in (EXT / "sidepanel.js").read_text()
    assert "config-manifest.js" in html
    assert "config-manifest.js" in (EXT / "config-reader.html").read_text()
    assert "PastexManifest" in (EXT / "config-reader.js").read_text()
    assert 'id="preview-pane"' not in html and 'id="btn-insert"' not in html
    assert "indexedDB" in (EXT / "handle-store.js").read_text()
    assert "nativeMessaging" not in _manifest()["permissions"]


def test_sidepanel_has_collapse_all_and_verbatim_insert():
    html = (EXT / "sidepanel.html").read_text(encoding="utf-8")
    script = (EXT / "sidepanel.js").read_text(encoding="utf-8")
    content = (EXT / "content.js").read_text(encoding="utf-8")
    assert 'id="btn-collapse-all"' in html
    assert "<footer" not in html
    assert 'id="status"' in html and html.index('id="status"') < html.index("</header>")
    assert "btn-collapse-all" in script
    assert "Google Drive • session cache · " in script
    assert "Session cache: " not in script
    assert "Snippets loaded from session cache." not in script
    assert "pastex_collapsed" in (EXT / "library.js").read_text(encoding="utf-8")
    assert "pastex_collapsed" in (EXT / "drive.js").read_text(encoding="utf-8")
    # Textarea/input insertion must use setRangeText, which keeps newlines.
    assert "target.setRangeText(text, start, end, \"end\")" in content
    assert 'typeof node.text === "string" ? node.text : ""' in script
