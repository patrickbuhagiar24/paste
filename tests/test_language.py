"""The project speaks one language: English.

Three layers, weakest to strongest:

1. an explicit word list, so the failure names the language;
2. every tracked text file, including tests and fixtures — that is where the
   last Indonesian sentence hid (`baris pertama` in a test fixture);
3. letters outside ASCII, which no English document needs beyond the UI glyphs
   this project actually uses.

Historical `debian/changelog` entries are exempt from the word list, because a
changelog records what happened under the names that existed then.
"""

import re

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Documents a user reads, plus the code and scripts they run.
LIVING_FILES = (
    "README.md",
    "ARCHITECTURE.md",
    "ANALYSIS.md",
    "DEVELOPMENT.md",
    "SECURITY.md",
    "running-on-linux-mint.md",
    "where-order-and-collapse-live.md",
    "apt-repo/README.md",
    "extension/README.md",
)

CODE_GLOBS = ("extension/*.js", "scripts/*.sh", "tests/*.py", "debian/postinst", "debian/postrm")

# Every tracked file is scanned by the two broad checks below: a fixture or a
# comment is project content just like a document. Binary files are skipped.
BINARY_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2", ".deb", ".crx", ".gz")

# Non-ASCII characters the project uses on purpose: UI glyphs, typography and
# the German/French accented words in the UTF-8 test fixture.
ALLOWED_NON_ASCII = set("—–…→←↑↓↻▸▾▼│•·×≥≥⚡＋☑☐⚠✎📝📄📁🗑✓😀üöéï’‘“”§≤")

# Indonesian function words and everyday nouns. None of these are English words.
INDONESIAN = (
    # function words
    "yang", "untuk", "tidak", "dengan", "adalah", "sudah", "belum", "harus",
    "karena", "atau", "jika", "juga", "hanya", "bisa", "masih", "selalu",
    "mungkin", "supaya", "sebelum", "sesudah", "sebagai", "antara", "tetapi",
    "namun", "bahwa", "oleh", "secara", "setiap", "semua", "tanpa", "saat",
    "ketika", "sambil", "dalam", "atas", "bawah", "lebih", "paling", "sangat",
    # everyday nouns a fixture or UI string tends to use
    "berkas", "tombol", "halaman", "pengguna", "tautan", "unduh", "unggah",
    "perubahan", "bilah", "pohon", "pilihan", "kesalahan", "petunjuk",
    "panduan", "keamanan", "jendela", "catatan", "contoh", "baris", "teks",
    "tulisan", "daftar", "urutan", "warna", "huruf", "angka",
    # ordinals and verbs that appear in sample text
    "pertama", "kedua", "ketiga", "keempat", "kelima", "baru", "lama",
    "memakai", "dipakai", "tersebut", "caranya", "membuat", "membaca",
    "menulis", "menghapus", "menyimpan", "membuka", "menutup", "berjalan",
)

PATTERN = re.compile(r"\b(?:" + "|".join(INDONESIAN) + r")\b", re.IGNORECASE)


def _living_paths():
    """Files the rule applies to. This guard lists the banned words, so it is
    the one file that naturally contains them."""
    paths = [ROOT / name for name in LIVING_FILES]
    for glob in CODE_GLOBS:
        paths.extend(sorted(ROOT.glob(glob)))
    me = Path(__file__).resolve()
    return [path for path in paths if path.is_file() and path.resolve() != me]


def test_living_files_and_code_are_english():
    findings = []
    for path in _living_paths():
        text = path.read_text(encoding="utf-8")
        for match in PATTERN.finditer(text):
            line = text[: match.start()].count("\n") + 1
            findings.append(f"{path.relative_to(ROOT)}:{line}: {match.group(0)!r}")
    assert not findings, (
        "Indonesian text is back in a living file:\n  "
        + "\n  ".join(findings[:10])
        + "\nSee DEVELOPMENT.md: one language, English."
    )


def _tracked_text_files():
    import subprocess

    names = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    for name in names:
        if name.lower().endswith(BINARY_SUFFIXES):
            continue
        path = ROOT / name
        try:
            yield path, path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue


def test_every_tracked_file_is_english():
    """The word list applies everywhere, tests and fixtures included."""
    me = Path(__file__).resolve()
    findings = []
    for path, text in _tracked_text_files():
        if path.resolve() == me or path.name == "changelog":
            continue
        for match in PATTERN.finditer(text):
            line = text[: match.start()].count("\n") + 1
            findings.append(f"{path.relative_to(ROOT)}:{line}: {match.group(0)!r}")
    assert not findings, (
        "Indonesian text is back in the project:\n  "
        + "\n  ".join(findings[:10])
        + "\nSee DEVELOPMENT.md rule 6: one language, English."
    )


def test_no_letters_from_another_script():
    """English needs no non-Latin letter; a stray one means pasted foreign text."""
    findings = []
    for path, text in _tracked_text_files():
        for index, character in enumerate(text):
            if ord(character) <= 127 or character in ALLOWED_NON_ASCII:
                continue
            line = text[:index].count("\n") + 1
            findings.append(f"{path.relative_to(ROOT)}:{line}: U+{ord(character):04X} {character!r}")
    assert not findings, (
        "a character outside English and the project's own glyph set was found:\n  "
        + "\n  ".join(findings[:10])
    )


def test_documents_are_named_in_english():
    """The project's four long-form documents carry English file names."""
    for name in ("ARCHITECTURE.md", "ANALYSIS.md", "DEVELOPMENT.md", "SECURITY.md"):
        assert (ROOT / name).is_file(), f"{name} is missing"
    for old in ("ARSITEKTUR.md", "ANALISIS.md", "cara-jalankan-get-linux-mint.md",
                "lokasi-collapse-urutan.md"):
        assert not (ROOT / old).exists(), f"{old} still exists; the rename is not a copy"


def test_document_links_point_at_names_that_exist():
    """A renamed document must not leave a dead link in the living docs."""
    pattern = re.compile(r"\]\(([^)#\s]+\.md)\)")
    for path in _living_paths():
        if path.suffix != ".md":
            continue
        text = path.read_text(encoding="utf-8")
        for match in pattern.finditer(text):
            target = match.group(1)
            if target.startswith("http"):
                continue
            resolved = (path.parent / target).resolve()
            assert resolved.is_file(), (
                f"{path.relative_to(ROOT)} links to {target}, which does not exist"
            )
