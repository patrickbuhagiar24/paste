#!/usr/bin/env python3
"""Build (and verify) a CRX3 package from extension/ for apt delivery.

Chrome on Linux auto-detects externally installed extensions from
/usr/share/google-chrome/extensions/<id>.json pointing at a local .crx
file. The <id> is derived from the CRX signing key, so one stable key
gives every build the same id:

    openssl genrsa -out crx-key.pem 2048        # once, keep it secret
    python3 scripts/pack-crx.py --key crx-key.pem --src extension --out dist/pastex.crx
    # prints the 32-char extension id (a-p) for the external JSON file

Only the standard library plus the openssl CLI are used, so the script
runs wherever the .deb is built. --verify re-parses the header, checks
the RSA signature, and validates the embedded zip.

Usage:
    pack-crx.py --key KEY.PEM --src extension --out pastex.crx
    pack-crx.py --print-id --key KEY.PEM
    pack-crx.py --verify pastex.crx
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

MAGIC = b"Cr24"
VERSION = 3
SIGNED_DATA_PREFIX = b"CRX3 SignedData\x00"

# Never shipped inside the .crx (docs and tests stay in the repo only).
EXCLUDE_DIRS = {"test"}
EXCLUDE_FILES = {"README.md"}


def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        chunk = value & 0x7F
        value >>= 7
        if value:
            out.append(chunk | 0x80)
        else:
            out.append(chunk)
            return bytes(out)


def _openssl(args: list[str], stdin: bytes | None = None) -> bytes:
    if shutil.which("openssl") is None:
        raise SystemExit("error: the openssl CLI is required")
    proc = subprocess.run(
        ["openssl", *args],
        input=stdin,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"error: openssl {' '.join(args)} failed: {proc.stderr.decode().strip()}")
    return proc.stdout


def public_key_der(key_path: Path) -> bytes:
    """SubjectPublicKeyInfo DER public key (accepts PKCS#1/PKCS#8 keys)."""
    with tempfile.TemporaryDirectory(prefix="pastex-crx-") as tmp:
        out = Path(tmp) / "pub.der"
        _openssl(["rsa", "-in", str(key_path), "-pubout", "-outform", "DER", "-out", str(out)])
        return out.read_bytes()


def extension_id(public_der: bytes) -> str:
    """The 32-char (a-p) id Chrome derives from the public key."""
    digest = hashlib.sha256(public_der).digest()[:16]
    return "".join(chr(ord("a") + int(c, 16)) for c in digest.hex())


def _zip_sources(src: Path) -> bytes:
    """Deterministic zip of the extension directory (sorted, fixed mtime)."""
    names = sorted(
        p.relative_to(src).as_posix()
        for p in src.rglob("*")
        if p.is_file()
        and p.relative_to(src).parts[0] not in EXCLUDE_DIRS
        and p.name not in EXCLUDE_FILES
    )
    if "manifest.json" not in names:
        raise SystemExit(f"error: {src}/manifest.json is missing")
    with tempfile.TemporaryDirectory(prefix="pastex-crx-") as tmp:
        zip_path = Path(tmp) / "ext.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for name in names:
                info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                with open(src / name, "rb") as handle:
                    archive.writestr(info, handle.read())
        return zip_path.read_bytes()


def build_crx(src: Path, key_path: Path) -> tuple[bytes, str]:
    """Return (crx bytes, extension id). Deterministic per key + sources."""
    public_der = public_key_der(key_path)
    ext_id = extension_id(public_der)
    crx_id = hashlib.sha256(public_der).digest()[:16]

    signed_data_inner = b"\x0a\x10" + crx_id  # SignedData{crx_id}
    zip_bytes = _zip_sources(src)
    signed_data = SIGNED_DATA_PREFIX + struct.pack("<I", len(signed_data_inner)) + signed_data_inner + zip_bytes
    signature = _openssl(["dgst", "-sha256", "-sign", str(key_path)], stdin=signed_data)

    proof = b"\x0a" + _varint(len(public_der)) + public_der
    proof += b"\x12" + _varint(len(signature)) + signature
    # CrxFileHeader.sha256_with_rsa (field 2) and signed_header_data (10000).
    header = b"\x12" + _varint(len(proof)) + proof
    header += _varint((10000 << 3) | 2) + _varint(len(signed_data_inner)) + signed_data_inner
    crx = MAGIC + struct.pack("<II", VERSION, len(header)) + header + zip_bytes
    return crx, ext_id


def _read_varint(data: bytes, pos: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while True:
        byte = data[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, pos
        shift += 7


def verify_crx(path: Path) -> str:
    """Verify structure, signature, and zip; return the extension id."""
    data = path.read_bytes()
    if data[:4] != MAGIC:
        raise SystemExit("error: bad magic (not a CRX file)")
    (version, header_size) = struct.unpack("<II", data[4:12])
    if version != VERSION:
        raise SystemExit(f"error: unsupported CRX version {version}")
    header = data[12 : 12 + header_size]
    zip_bytes = data[12 + header_size :]

    # CrxFileHeader{sha256_with_rsa: proof, signed_header_data: SignedData}
    pos = 0
    if not header or header[pos] != 0x12:
        raise SystemExit("error: malformed CRX header")
    proof_len, pos = _read_varint(header, pos + 1)
    proof = header[pos : pos + proof_len]
    pos += proof_len
    tag, pos = _read_varint(header, pos)
    if tag != ((10000 << 3) | 2):
        raise SystemExit("error: missing CRX signed header")
    signed_len, pos = _read_varint(header, pos)
    signed_data_inner = header[pos:pos + signed_len]
    if pos + signed_len != len(header):
        raise SystemExit("error: malformed CRX signed header")
    ppos = 0
    if proof[ppos] != 0x0A:
        raise SystemExit("error: malformed key proof")
    key_len, ppos = _read_varint(proof, ppos + 1)
    public_der = proof[ppos : ppos + key_len]
    ppos += key_len
    if proof[ppos] != 0x12:
        raise SystemExit("error: malformed key proof signature")
    sig_len, ppos = _read_varint(proof, ppos + 1)
    signature = proof[ppos : ppos + sig_len]

    ext_id = extension_id(public_der)
    crx_id = hashlib.sha256(public_der).digest()[:16]
    if signed_data_inner != b"\x0a\x10" + crx_id:
        raise SystemExit("error: CRX id does not match signing key")
    signed_data = SIGNED_DATA_PREFIX + struct.pack("<I", len(signed_data_inner)) + signed_data_inner + zip_bytes

    with tempfile.TemporaryDirectory(prefix="pastex-crx-") as tmp:
        tmpdir = Path(tmp)
        (tmpdir / "pub.der").write_bytes(public_der)
        (tmpdir / "sig.bin").write_bytes(signature)
        (tmpdir / "data.bin").write_bytes(signed_data)
        _openssl(
            ["rsa", "-pubin", "-inform", "DER", "-in", str(tmpdir / "pub.der"),
             "-pubout", "-out", str(tmpdir / "pub.pem")]
        )
        proc = subprocess.run(
            ["openssl", "dgst", "-sha256", "-verify", str(tmpdir / "pub.pem"),
             "-signature", str(tmpdir / "sig.bin"), str(tmpdir / "data.bin")],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0 or "Verified OK" not in proc.stdout:
            raise SystemExit(f"error: CRX signature check failed: {proc.stdout.strip()} {proc.stderr.strip()}")

    with tempfile.TemporaryDirectory(prefix="pastex-crx-") as tmp:
        zip_path = Path(tmp) / "ext.zip"
        zip_path.write_bytes(zip_bytes)
        try:
            with zipfile.ZipFile(zip_path) as archive:
                if archive.testzip() is not None:
                    raise SystemExit("error: embedded zip is corrupt")
                names = archive.namelist()
        except zipfile.BadZipFile as exc:
            raise SystemExit(f"error: embedded zip is invalid: {exc}")
        if "manifest.json" not in names:
            raise SystemExit("error: embedded zip has no manifest.json")
    return ext_id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build and verify the Pastex CRX package.")
    parser.add_argument("--key", help="RSA private key (PEM) used for signing")
    parser.add_argument("--src", default="extension", help="Extension source directory")
    parser.add_argument("--out", help="Write the .crx file here (prints the extension id)")
    parser.add_argument("--print-id", action="store_true", help="Print the extension id for --key")
    parser.add_argument("--verify", metavar="FILE.crx", help="Verify a .crx file (prints the extension id)")
    args = parser.parse_args(argv)

    if args.verify:
        print(verify_crx(Path(args.verify)))
        return 0
    if args.print_id:
        if not args.key:
            parser.error("--print-id requires --key")
        print(extension_id(public_key_der(Path(args.key))))
        return 0
    if not args.key or not args.out:
        parser.error("building requires --key and --out")
    crx, ext_id = build_crx(Path(args.src), Path(args.key))
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(crx)
    print(ext_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
